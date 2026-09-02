# -*- coding: utf-8 -*-
"""det_collision —— SVG 元素碰撞与遮挡检测器(key: collision)

只用 Python 3.12 标准库。检测手写 SVG 流程图里「元素互相压住」的版式缺陷:
XML 解析完全正常、链接锚点也都对,但渲染出来是一团糟。

五条规则(全部经过全站 23 页实测调参,好样本上零检出):

  R1 rect-overlap    两个方框部分重叠(互不包含),重叠面积占小框比例超阈值
  R2 text-over-rect  悬空文字(中心不在任何框内)压进了某个方框
  R3 text-spill      文字从自己的框里溢出,溢出部分压到了另一个方框上
  R4 text-overlap    两段文字互相压住
  R5 text-clipped    文字被 viewBox 边缘裁掉(被画布遮挡)
  R6 path-over-text  连线(<path>/<line>)从文字中间穿过去

设计原则是「宁可漏报,不可滥报」:阈值都取在实测数据的安全余量之外,
容器框(自身完整包住别的框、或占画布近半)一律不作为受害者参与文字类判定。

用法:
    from det_collision import check
    issues = check(path, html)     # [{"line":int,"kind":str,"msg":str,"detail":str}, ...]

自测:
    python det_collision.py
"""

from __future__ import annotations

import os
import re
import sys
from html.parser import HTMLParser

from _baseline import 基线目录, 已修目录  # noqa: E402

# ---------------------------------------------------------------- 可调阈值 ---

# R1 方框重叠。实测:全站 12 处 rect 相交全部是「完整包含」(底板套子框),
# 一处部分重叠都没有,所以阈值可以放得比较灵敏而不出误报。
RR_MIN_IX = 4.0          # 横向重叠至少 4px(挡掉共边、描边压线、rx 圆角)
RR_MIN_IY = 4.0
RR_MIN_RATIO = 0.05      # 重叠面积 / 小框面积
RR_CONTAIN = 0.995       # 达到这个比例视为「包含」,属正常的底板/泳道,不报

# R2 悬空文字压框。实测非底板框上的最大压盖比例是 0.00,底板上 0.23(已排除)。
TR_MIN_IX = 5.0
TR_MIN_IY = 5.0
TR_MIN_FRAC = 0.28       # 压住的面积占文字自身包围盒的比例

# R3 文字溢出自己的框并压到别的框。实测最紧的正常文字离框边还有 3.0px 余量,
# 取 8px 意味着宽度模型要错 11px 才会误报。
# 实测全站 395 段「有归属」的文字,溢出量一律是 0,所以 8px 这道闸已经足够严;
# 落点条件只要求「确实压到了邻框」:横向至少 8px 且不少于 0.6 个字宽,纵向不少于半行。
TS_MIN_OVERFLOW = 8.0
TS_MIN_IX = 8.0
TS_MIN_IX_EM = 0.6       # 横向压入量至少 0.6 倍字号
TS_MIN_IY_EM = 0.5       # 纵向压入量至少 0.5 倍字号

# R4 文字互压。实测最大一例 iy=0.9 / 比例 0.02(相邻两行的亚像素贴边)。
TT_MIN_IX = 4.0
TT_MIN_IY = 3.5
TT_MIN_RATIO = 0.08

# R5 文字被画布裁掉
VB_MIN_OVER = 6.0        # 超出 viewBox 至少 6px
VB_MIN_FRAC = 0.08       # 且至少占文字宽度的 8%

# 容器框判定:占画布面积超过这个比例,视为底板
CONTAINER_AREA_FRAC = 0.45

# --- R6 path-over-text ---
# 只抓「穿过去」,不抓「指过来」:连线必须在文字盒的**中间带**里连续走一段,
# 且它在盒外两侧都还有轨迹(说明是穿越,不是端点停在标签旁边)。
PT_INNER_X = 0.16        # 盒左右各内缩这个比例,避开贴边而过的线
PT_INNER_Y = 0.22        # 盒上下各内缩,避开擦着字顶/字底走的线
PT_MIN_RUN_EM = 0.30     # 在中间带里连续走过的横向距离,至少这么多个字号
PT_OUT_PAD = 3.0         # 判断「盒外」时给的余量
PT_SAMPLES = 240         # 每段曲线采样点数

MAX_PER_RULE_PER_SVG = 6  # 单个 svg 单条规则最多列出几条,其余汇总成一句

# 不参与布局的子树(marker / defs 里的图元不在页面坐标系上)
SKIP_SUB = {
    "defs", "marker", "symbol", "clippath", "mask", "pattern",
    "lineargradient", "radialgradient", "filter",
}


# ------------------------------------------------------------------ 工具 ---

def _num(v, default=None):
    """从属性值里取第一个数(容忍 '12px' '1.6' 这类写法)。"""
    if v is None:
        return default
    m = re.match(r"\s*(-?\d+(?:\.\d+)?)", str(v))
    return float(m.group(1)) if m else default


def _char_w(ch: str, fs: float) -> float:
    """单字宽度估算:中文/全角/带圈数字按 1.0 倍字号,ASCII 按 0.55 倍。"""
    o = ord(ch)
    if o < 0x80:
        return 0.55 * fs
    if (0x2460 <= o <= 0x24FF          # ①②③ 带圈数字
            or 0x3000 <= o <= 0x9FFF   # CJK 标点 + 汉字
            or 0x3400 <= o <= 0x4DBF
            or 0xF900 <= o <= 0xFAFF
            or 0xFF00 <= o <= 0xFFEF):  # 全角
        return 1.0 * fs
    return 0.62 * fs                   # → · — 等符号,取中间值


def _area(b):
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _inter(a, b):
    """返回 (横向重叠, 纵向重叠, 重叠面积)。"""
    ix = min(a[2], b[2]) - max(a[0], b[0])
    iy = min(a[3], b[3]) - max(a[1], b[1])
    return (ix, iy, ix * iy) if (ix > 0 and iy > 0) else (ix, iy, 0.0)


def _rbox(r):
    return (r["x"], r["y"], r["x"] + r["w"], r["y"] + r["h"])


def _tbox(t):
    """文字包围盒。x 按 text-anchor 摆放,y 是基线,上升 0.80em、下降 0.22em。"""
    w = t["w"]
    a = t["anchor"]
    if a == "middle":
        x0 = t["x"] - w / 2.0
    elif a in ("end", "right"):
        x0 = t["x"] - w
    else:
        x0 = t["x"]
    return (x0, t["y"] - 0.80 * t["fs"], x0 + w, t["y"] + 0.22 * t["fs"])


def _clip(s, n=26):
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= n else s[: n - 1] + "…"


# ------------------------------------------------------------------ 解析 ---

class _SvgGeom(HTMLParser):
    """从整篇 HTML 里抽出每个 <svg> 的 rect / text 几何。

    用 html.parser 而不是 xml.etree:页面里全是 &#9450; &rarr; 这类 HTML 实体,
    喂给 XML 解析器会因「未定义实体」直接报错;html.parser 自带实体解码。
    行号用 getpos(),因为喂的是整篇文档,拿到的就是文件里的绝对行号。
    """

    ROOT = {"fs": 16.0, "anchor": "start", "tx": 0.0, "ty": 0.0, "rot": False}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.svgs = []
        self._depth = 0        # svg 嵌套深度
        self._skip = 0         # 处在 defs/marker 之类子树里的深度
        self._stack = []
        self._cur = None
        self._text = None

    def _ctx(self):
        return self._stack[-1] if self._stack else dict(self.ROOT)

    def _push(self, d):
        c = dict(self._ctx())
        fs = _num(d.get("font-size"))
        if fs:
            c["fs"] = fs
        if d.get("text-anchor"):
            c["anchor"] = d["text-anchor"].strip().lower()
        tr = d.get("transform", "")
        if tr:
            for m in re.finditer(r"translate\(\s*(-?[\d.]+)\s*[, ]\s*(-?[\d.]+)?", tr):
                c["tx"] += float(m.group(1))
                c["ty"] += float(m.group(2) or 0.0)
            # 旋转/缩放/斜切一律标脏:这类元素的包围盒不能用轴对齐盒近似,后面整个跳过
            if re.search(r"\b(rotate|matrix|scale|skew[XY])\s*\(", tr):
                c["rot"] = True
        self._stack.append(c)

    # -- 标签 --
    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if self._skip:
            self._skip += 1
            return
        d = {k.lower(): (v or "") for k, v in attrs}
        if tag == "svg":
            if self._depth == 0:
                self._cur = {"line": self.getpos()[0], "vb": d.get("viewbox", ""),
                             "rects": [], "texts": [], "strokes": []}
                self._stack = []
            self._depth += 1
        if self._depth == 0:
            return
        if tag in SKIP_SUB:
            self._skip = 1
            return
        self._push(d)
        c = self._ctx()
        if tag == "rect":
            w, h = _num(d.get("width")), _num(d.get("height"))
            if w and h and w > 0 and h > 0 and not c["rot"]:
                self._cur["rects"].append({
                    "x": _num(d.get("x"), 0.0) + c["tx"],
                    "y": _num(d.get("y"), 0.0) + c["ty"],
                    "w": w, "h": h, "line": self.getpos()[0],
                    "fill": d.get("fill", ""), "stroke": d.get("stroke", ""),
                })
        elif tag in ("path", "line") and not c["rot"]:
            # 只看真的画出来的线:没有 stroke 或 stroke:none 的不算
            st = (d.get("stroke", "") or "").strip().lower()
            if st and st != "none":
                if tag == "path":
                    pts = _path_points(d.get("d", ""))
                else:
                    x1, y1 = _num(d.get("x1"), 0.0), _num(d.get("y1"), 0.0)
                    x2, y2 = _num(d.get("x2"), 0.0), _num(d.get("y2"), 0.0)
                    pts = [(x1, y1), (x2, y2)] if None not in (x1, y1, x2, y2) else []
                if len(pts) >= 2:
                    self._cur["strokes"].append({
                        "pts": [(x + c["tx"], y + c["ty"]) for x, y in pts],
                        "line": self.getpos()[0], "tag": tag,
                        "w": _num(d.get("stroke-width"), 1.0) or 1.0,
                        "dash": bool((d.get("stroke-dasharray", "") or "").strip()),
                    })
        elif tag == "text":
            self._text = {"x": _num(d.get("x"), 0.0) + c["tx"],
                          "y": _num(d.get("y"), 0.0) + c["ty"],
                          "fs": c["fs"], "anchor": c["anchor"],
                          "line": self.getpos()[0], "rot": c["rot"], "seg": []}

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data):
        if self._text is not None and not self._skip and self._depth:
            self._text["seg"].append((data, self._ctx()["fs"]))

    def handle_endtag(self, tag):
        tag = tag.lower()
        if self._skip:
            self._skip -= 1
            return
        if self._depth == 0:
            return
        if tag == "text" and self._text is not None:
            t = self._text
            self._text = None
            s = re.sub(r"\s+", " ", "".join(x for x, _ in t["seg"])).strip()
            if s and not t["rot"]:
                t["s"] = s
                t["w"] = sum(_char_w(ch, fs) for chunk, fs in t["seg"] for ch in chunk
                             if not ch.isspace()) + \
                    sum(0.28 * fs for chunk, fs in t["seg"] for ch in chunk if ch.isspace())
                self._cur["texts"].append(t)
        if self._stack:
            self._stack.pop()
        if tag == "svg":
            self._depth -= 1
            if self._depth == 0 and self._cur is not None:
                self.svgs.append(self._cur)
                self._cur = None


# ------------------------------------------------------------------ 规则 ---

def _mark_containers(rects, vb):
    """标出「底板/泳道」类的框:完整包住别的框,或者占画布面积近一半。

    这类框上面本来就要压文字、压别的框,不能当成受害者。
    """
    vb_area = (vb[2] * vb[3]) if vb else 0.0
    for r in rects:
        r["container"] = False
    for i, a in enumerate(rects):
        ab = _rbox(a)
        aa = _area(ab)
        if vb_area and aa >= CONTAINER_AREA_FRAC * vb_area:
            a["container"] = True
            continue
        for j, b in enumerate(rects):
            if i == j:
                continue
            bb = _rbox(b)
            _, _, ia = _inter(ab, bb)
            if ia >= RR_CONTAIN * _area(bb) and _area(bb) < aa:
                a["container"] = True
                break


def _svg_where(sv):
    return "第 %d 行起的 <svg>" % sv["line"]


def _rule_rect_overlap(sv, out):
    R = sv["rects"]
    for i in range(len(R)):
        for j in range(i + 1, len(R)):
            a, b = R[i], R[j]
            ab, bb = _rbox(a), _rbox(b)
            ix, iy, ia = _inter(ab, bb)
            if ia <= 0 or ix < RR_MIN_IX or iy < RR_MIN_IY:
                continue
            amin = min(_area(ab), _area(bb))
            if amin <= 0:
                continue
            # 一个完整包住另一个 = 底板/泳道,正常排版
            if ia >= RR_CONTAIN * amin:
                continue
            if ia / amin < RR_MIN_RATIO:
                continue
            out.append({
                "line": max(a["line"], b["line"]),
                "kind": "svg-rect-overlap",
                "msg": "两个方框互相压住:第 %d 行的 rect 与第 %d 行的 rect 重叠 %.0f×%.0f px"
                       % (a["line"], b["line"], ix, iy),
                "detail": "%s 内,rect(x=%g y=%g w=%g h=%g)与 rect(x=%g y=%g w=%g h=%g)"
                          "部分相交,重叠面积 %.0f,占较小方框的 %.0f%%,且互不包含 —— "
                          "不是底板套子框那种正常写法,渲染出来就是两个框叠在一起。"
                          % (_svg_where(sv), a["x"], a["y"], a["w"], a["h"],
                             b["x"], b["y"], b["w"], b["h"], ia, 100 * ia / amin),
            })


def _rule_text_over_rect(sv, out):
    """悬空文字压进方框:文字中心不在任何框内,却明显盖住了某个非底板框。"""
    R = [r for r in sv["rects"] if not r["container"]]
    ALL = sv["rects"]
    for t in sv["texts"]:
        tb = _tbox(t)
        ta = _area(tb)
        if ta <= 0:
            continue
        cx = (tb[0] + tb[2]) / 2.0
        cy = t["y"] - 0.29 * t["fs"]
        # 「有归属」只认非底板框:文字落在泳道底板上不算有主,那是常态
        inside = any(r["x"] <= cx <= r["x"] + r["w"] and r["y"] <= cy <= r["y"] + r["h"]
                     for r in R)
        if inside:
            continue                       # 有归属的文字交给 R3
        for r in R:
            ix, iy, ia = _inter(tb, _rbox(r))
            if ia <= 0 or ix < TR_MIN_IX or iy < TR_MIN_IY:
                continue
            if ia / ta < TR_MIN_FRAC:
                continue
            out.append({
                "line": t["line"],
                "kind": "svg-text-over-rect",
                "msg": "文字压在别的方框上:「%s」盖住了第 %d 行的 rect(%.0f%% 面积重叠)"
                       % (_clip(t["s"]), r["line"], 100 * ia / ta),
                "detail": "%s 内,<text>「%s」(x=%g y=%g 字号 %g,估算宽 %.0f)的中心点"
                          "不落在任何方框内(说明它是条悬空标签),但它的包围盒和第 %d 行的 "
                          "rect(x=%g y=%g w=%g h=%g)重叠 %.0f×%.0f px —— 标签压在了别人的框上。"
                          % (_svg_where(sv), _clip(t["s"], 40), t["x"], t["y"], t["fs"], t["w"],
                             r["line"], r["x"], r["y"], r["w"], r["h"], ix, iy),
            })
            break


def _rule_text_spill(sv, out):
    """文字撑破自己的框,溢出部分压到了旁边的框上。"""
    ALL = sv["rects"]
    for t in sv["texts"]:
        tb = _tbox(t)
        ta = _area(tb)
        if ta <= 0:
            continue
        cx = (tb[0] + tb[2]) / 2.0
        cy = t["y"] - 0.29 * t["fs"]
        owners = [r for r in ALL
                  if r["x"] <= cx <= r["x"] + r["w"] and r["y"] <= cy <= r["y"] + r["h"]]
        if not owners:
            continue
        own = min(owners, key=lambda r: r["w"] * r["h"])
        ob = _rbox(own)
        spill = max(ob[0] - tb[0], 0.0) + max(tb[2] - ob[2], 0.0)
        if spill < TS_MIN_OVERFLOW:
            continue
        for r in ALL:
            if r is own or r["container"]:
                continue
            ix, iy, ia = _inter(tb, _rbox(r))
            if ia <= 0:
                continue
            if ix < max(TS_MIN_IX, TS_MIN_IX_EM * t["fs"]) or iy < TS_MIN_IY_EM * t["fs"]:
                continue
            out.append({
                "line": t["line"],
                "kind": "svg-text-spill",
                "msg": "文字撑出自己的方框并压到邻框:「%s」溢出 %.0fpx,压住第 %d 行的 rect"
                       % (_clip(t["s"]), spill, r["line"]),
                "detail": "%s 内,<text>「%s」(字号 %g,估算宽 %.0f)所在的 rect 只有 %g 宽"
                          "(第 %d 行),文字横向溢出 %.0f px,溢出部分与第 %d 行的 rect 重叠 "
                          "%.0f×%.0f px。要么缩字号、要么加宽方框、要么断成两行。"
                          % (_svg_where(sv), _clip(t["s"], 40), t["fs"], t["w"], own["w"],
                             own["line"], spill, r["line"], ix, iy),
            })
            break


def _rule_text_text(sv, out):
    T = sv["texts"]
    for i in range(len(T)):
        for j in range(i + 1, len(T)):
            a, b = _tbox(T[i]), _tbox(T[j])
            ix, iy, ia = _inter(a, b)
            if ia <= 0 or ix < TT_MIN_IX or iy < TT_MIN_IY:
                continue
            amin = min(_area(a), _area(b))
            if amin <= 0 or ia / amin < TT_MIN_RATIO:
                continue
            out.append({
                "line": max(T[i]["line"], T[j]["line"]),
                "kind": "svg-text-overlap",
                "msg": "两段文字互相压住:「%s」(第 %d 行)与「%s」(第 %d 行)重叠 %.0f×%.0f px"
                       % (_clip(T[i]["s"], 16), T[i]["line"], _clip(T[j]["s"], 16),
                          T[j]["line"], ix, iy),
                "detail": "%s 内,两段 <text> 的估算包围盒相交,重叠面积占较小那段的 %.0f%%。"
                          "字号分别是 %g / %g,基线 y 分别是 %g / %g —— 行距不够或 x 摆重了。"
                          % (_svg_where(sv), 100 * ia / amin, T[i]["fs"], T[j]["fs"],
                             T[i]["y"], T[j]["y"]),
            })


def _path_points(d: str):
    """把 <path d="..."> 采样成折线点列。

    只实现页面里实际用到的指令:M/L/H/V/C/Q/Z 及其相对形式。
    遇到没实现的指令就整条放弃(返回空),宁可漏报也不要拿错坐标去定罪。
    """
    if not d:
        return []
    toks = re.findall(r"[MmLlHhVvCcQqZz]|-?\d*\.?\d+(?:[eE][-+]?\d+)?", d)
    pts, i = [], 0
    cx = cy = 0.0
    sx = sy = 0.0
    cmd = ""

    def nums(k):
        nonlocal i
        out = []
        for _ in range(k):
            if i >= len(toks) or re.match(r"[A-Za-z]", toks[i]):
                return None
            out.append(float(toks[i]))
            i += 1
        return out

    while i < len(toks):
        if re.match(r"[A-Za-z]", toks[i]):
            cmd = toks[i]
            i += 1
            if cmd in "Zz":
                if pts:
                    pts.append((sx, sy))
                cx, cy = sx, sy
                continue
        if cmd == "":
            return []
        up = cmd.upper()
        rel = cmd.islower()
        if up == "M":
            v = nums(2)
            if v is None:
                return []
            cx, cy = (cx + v[0], cy + v[1]) if rel else (v[0], v[1])
            sx, sy = cx, cy
            pts.append((cx, cy))
            cmd = "l" if rel else "L"          # M 之后的连续坐标按 L 处理
        elif up == "L":
            v = nums(2)
            if v is None:
                return []
            cx, cy = (cx + v[0], cy + v[1]) if rel else (v[0], v[1])
            pts.append((cx, cy))
        elif up == "H":
            v = nums(1)
            if v is None:
                return []
            cx = cx + v[0] if rel else v[0]
            pts.append((cx, cy))
        elif up == "V":
            v = nums(1)
            if v is None:
                return []
            cy = cy + v[0] if rel else v[0]
            pts.append((cx, cy))
        elif up in ("C", "Q"):
            k = 6 if up == "C" else 4
            v = nums(k)
            if v is None:
                return []
            if rel:
                v = [v[j] + (cx if j % 2 == 0 else cy) for j in range(k)]
            p0 = (cx, cy)
            if up == "C":
                p1, p2, p3 = (v[0], v[1]), (v[2], v[3]), (v[4], v[5])
                for j in range(1, PT_SAMPLES + 1):
                    t = j / PT_SAMPLES
                    m = 1 - t
                    pts.append((m**3*p0[0] + 3*m*m*t*p1[0] + 3*m*t*t*p2[0] + t**3*p3[0],
                                m**3*p0[1] + 3*m*m*t*p1[1] + 3*m*t*t*p2[1] + t**3*p3[1]))
                cx, cy = p3
            else:
                p1, p2 = (v[0], v[1]), (v[2], v[3])
                for j in range(1, PT_SAMPLES + 1):
                    t = j / PT_SAMPLES
                    m = 1 - t
                    pts.append((m*m*p0[0] + 2*m*t*p1[0] + t*t*p2[0],
                                m*m*p0[1] + 2*m*t*p1[1] + t*t*p2[1]))
                cx, cy = p2
        else:
            return []                          # A/S/T 等没实现,整条放弃
    return pts


def _rule_path_over_text(sv, out):
    """R6:连线从文字中间穿过去。

    判「穿过」不判「指到」——后者是正常的引线。三个条件都要满足:
      · 线在文字盒的中间带(左右内缩 16%、上下内缩 22%)里连续走过 ≥0.30em;
      · 该段之前和之后,线都跑到了盒外(说明是穿越,不是端点停在标签边上);
      · 文字本身不是被这条线当作端点标注的短标签(靠上面两条自然排除)。
    """
    for t in sv["texts"]:
        if t.get("rot"):
            continue
        x0, y0, x1, y1 = _tbox(t)
        w, h = x1 - x0, y1 - y0
        if w <= 0 or h <= 0:
            continue
        ix0, ix1 = x0 + w * PT_INNER_X, x1 - w * PT_INNER_X
        iy0, iy1 = y0 + h * PT_INNER_Y, y1 - h * PT_INNER_Y
        for st in sv["strokes"]:
            pts = st["pts"]
            inside = [(ix0 <= x <= ix1 and iy0 <= y <= iy1) for x, y in pts]
            if not any(inside):
                continue
            # 找最长的一段连续「在中间带内」
            best = cur = None
            for k, f in enumerate(inside):
                if f:
                    cur = k if cur is None else cur
                    if best is None or (k - cur) > (best[1] - best[0]):
                        best = (cur, k)
                else:
                    cur = None
            if best is None:
                continue
            a, b = best
            run = abs(pts[b][0] - pts[a][0])
            runy = abs(pts[b][1] - pts[a][1])
            if max(run, runy) < PT_MIN_RUN_EM * t["fs"]:
                continue
            outside = lambda p: not (x0 - PT_OUT_PAD <= p[0] <= x1 + PT_OUT_PAD
                                     and y0 - PT_OUT_PAD <= p[1] <= y1 + PT_OUT_PAD)
            if not (any(outside(p) for p in pts[:a]) and any(outside(p) for p in pts[b + 1:])):
                continue                      # 端点就停在标签旁边 —— 那是引线,不是穿越
            out.append({
                "line": t["line"],
                "kind": "svg-path-over-text",
                "msg": "连线从文字中间穿过去:第 %d 行的 <%s> 压过「%s」"
                       % (st["line"], st["tag"], _clip(t["s"])),
                "detail": "%s 内,<text>「%s」(字号 %.1f,包围盒 (%.0f,%.0f)-(%.0f,%.0f))"
                          "被第 %d 行的 <%s>(stroke-width %.1f%s)横穿 %.1f px"
                          % (_svg_where(sv), _clip(t["s"]), t["fs"], x0, y0, x1, y1,
                             st["line"], st["tag"], st["w"],
                             ",虚线" if st["dash"] else "", max(run, runy)),
            })


def _rule_text_clipped(sv, vb, out):
    if not vb:
        return
    x0, y0, w, h = vb
    for t in sv["texts"]:
        tb = _tbox(t)
        tw = max(1.0, tb[2] - tb[0])
        cand = [("左", x0 - tb[0]), ("右", tb[2] - (x0 + w)),
                ("上", y0 - tb[1]), ("下", tb[3] - (y0 + h))]
        side, over = max(cand, key=lambda p: p[1])
        if over < VB_MIN_OVER:
            continue
        if side in ("左", "右") and over / tw < VB_MIN_FRAC:
            continue
        out.append({
            "line": t["line"],
            "kind": "svg-text-clipped",
            "msg": "文字被画布边缘裁掉:「%s」超出 viewBox %s边 %.0f px"
                   % (_clip(t["s"]), side, over),
            "detail": "%s 的 viewBox 是 \"%g %g %g %g\",<text>「%s」(x=%g y=%g 字号 %g,"
                      "text-anchor=%s,估算宽 %.0f)的包围盒是 (%.0f,%.0f)-(%.0f,%.0f),"
                      "越过%s边界 %.0f px。svg 默认裁剪 viewBox 之外的内容,这段会被切掉。"
                      % (_svg_where(sv), x0, y0, w, h, _clip(t["s"], 40), t["x"], t["y"],
                         t["fs"], t["anchor"], t["w"], tb[0], tb[1], tb[2], tb[3], side, over),
        })


# ------------------------------------------------------------------- 入口 ---

def check(path: str, html: str) -> list[dict]:
    """检查一篇 HTML 里所有内联 SVG 的元素碰撞/遮挡。

    返回 [{"line": int, "kind": str, "msg": str, "detail": str}, ...]
    """
    # _v1/ 是历史页,不计入
    norm = str(path).replace("\\", "/")
    if "/_v1/" in norm or norm.endswith("/_v1"):
        return []

    p = _SvgGeom()
    try:
        p.feed(html)
        p.close()
    except Exception:
        raise  # 让驱动层的「规则异常」兜底可见,别把解析失败伪装成零检出

    issues: list[dict] = []
    for sv in p.svgs:
        vb = None
        parts = re.split(r"[\s,]+", sv["vb"].strip()) if sv["vb"] else []
        if len(parts) == 4:
            try:
                vb = tuple(float(x) for x in parts)
                if vb[2] <= 0 or vb[3] <= 0:
                    vb = None
            except ValueError:
                vb = None

        _mark_containers(sv["rects"], vb)

        for fn in (_rule_rect_overlap, _rule_text_over_rect, _rule_text_spill,
                   _rule_text_text, _rule_path_over_text):
            bucket: list[dict] = []
            fn(sv, bucket)
            issues.extend(_cap(bucket, sv))
        bucket = []
        _rule_text_clipped(sv, vb, bucket)
        issues.extend(_cap(bucket, sv))

    issues.sort(key=lambda d: (d["line"], d["kind"]))
    return issues


def _cap(bucket, sv):
    """单个 svg 单条规则最多列 MAX_PER_RULE_PER_SVG 条,防止一张烂图刷屏。"""
    if len(bucket) <= MAX_PER_RULE_PER_SVG:
        return bucket
    kept = bucket[:MAX_PER_RULE_PER_SVG]
    kept[-1] = dict(kept[-1])
    kept[-1]["detail"] += "(同一 svg 内该类问题共 %d 处,只列前 %d 处)" % (
        len(bucket), MAX_PER_RULE_PER_SVG)
    return kept


# ------------------------------------------------------------------- 自测 ---

_SYNTH_BAD = """<html><body>
<figure><svg viewBox="0 0 600 260">
  <g class="st" text-anchor="middle" font-size="13">
    <!-- R1:两个方框部分重叠 -->
    <rect x="20" y="20" width="140" height="50" rx="8" fill="#eef"/>
    <text x="90" y="48">正常的框内文字</text>
    <rect x="120" y="40" width="140" height="50" rx="8" fill="#fee"/>

    <!-- R2:悬空标签压在别人的框上(中心在框外,盒子伸进框里) -->
    <rect x="400" y="20" width="120" height="50" rx="8" fill="#efe"/>
    <text x="350" y="50" text-anchor="start">压上去的边标签</text>

    <!-- R3:文字撑出自己的框,溢出部分压到邻框 -->
    <rect x="150" y="120" width="90" height="40" fill="#efe"/>
    <text x="195" y="144">这一句实在是太长了根本装不下</text>
    <rect x="270" y="120" width="90" height="40" fill="#eef"/>

    <!-- R4:两段文字互压 -->
    <text x="500" y="130">重叠甲</text>
    <text x="500" y="134">重叠乙</text>

    <!-- R5:文字被 viewBox 裁掉 -->
    <text x="580" y="240" text-anchor="start">被画布裁掉的一句话</text>
  </g>
</svg></figure>
</body></html>"""

_SYNTH_OK = """<html><body>
<figure><svg viewBox="0 0 400 200">
  <g class="st" text-anchor="middle" font-size="13">
    <rect x="10" y="10" width="380" height="180" rx="8" fill="#fafafa"/>
    <rect x="30" y="30" width="140" height="50" rx="8" fill="#eef"/>
    <text x="100" y="58">框内文字</text>
    <rect x="220" y="30" width="140" height="50" rx="8" fill="#fee"/>
    <text x="290" y="58">另一段文字</text>
  </g>
</svg></figure>
</body></html>"""


def _run(label, path):
    try:
        html = open(path, encoding="utf-8", errors="replace").read()
    except OSError as e:
        print("  [%s] 读不到:%s" % (label, e))
        return []
    res = check(path, html)
    print("  [%s] %s -> %d 条" % (label, os.path.basename(path), len(res)))
    for r in res:
        print("      L%-5d %-22s %s" % (r["line"], r["kind"], r["msg"]))
    return res


def _mutation_test(good_dir):
    """灵敏度验证:把一页真实页面在内存里改坏,看检测器抓不抓得住。

    只在内存里改字符串,绝不写回任何文件。
    """
    path = os.path.join(good_dir, "01-overview.html")
    try:
        base = open(path, encoding="utf-8", errors="replace").read()
    except OSError as e:
        print("  读不到 %s:%s" % (path, e))
        return
    print("  基线(未改动):%d 条" % len(check(path, base)))
    muts = [
        ("把「步骤 2」的方框上移 32px,压住「步骤 1」的方框",
         '<rect x="320" y="140" width="260" height="44"',
         '<rect x="320" y="108" width="260" height="44"'),
        ("把「步骤 2」的标题文字上移 62px,压住「步骤 1」的标题",
         '<text x="450" y="159" font-weight="700"',
         '<text x="450" y="97" font-weight="700"'),
        ("把「步骤 1」的副标题右移到画布外",
         '<text x="450" y="111" font-size="11.5"',
         '<text x="930" y="111" font-size="11.5"'),
    ]
    for desc, old, new in muts:
        if old not in base:
            print("  [跳过] 找不到锚点:%s" % desc)
            continue
        res = check(path, base.replace(old, new, 1))
        print("  改动:%s -> %d 条" % (desc, len(res)))
        for r in res:
            print("      L%-5d %-22s %s" % (r["line"], r["kind"], r["msg"]))


def _selftest():
    坏 = 0
    print("=== 1) 合成样本(必须报) ===")
    for r in check("synth_bad.html", _SYNTH_BAD):
        print("      L%-5d %-22s %s" % (r["line"], r["kind"], r["msg"]))
        print("            detail: %s" % r["detail"])
    坏 += 0 if len(check("synth_bad.html", _SYNTH_BAD)) else 1
    print("=== 2) 合成好样本(必须零报) ===")
    _ok_n = len(check("synth_ok.html", _SYNTH_OK))
    坏 += 0 if _ok_n == 0 else 1
    print("      %d 条%s" % (_ok_n, "" if _ok_n == 0 else "  <== 失败"))

    bad_dir = 基线目录()
    good_dir = 已修目录()

    print("=== 3) 真值样本:坏样本(只读) ===")
    if not bad_dir:
        print("  (无只读基线,跳过 —— 不算通过)")
        坏 += 1
    for n in ("01-overview.html", "02-python-setup.html"):
        if bad_dir:
            _run("坏", os.path.join(bad_dir, n))
    print("=== 4) 真值样本:已修版 ===")
    for n in ("01-overview.html", "02-python-setup.html"):
        _run("好", os.path.join(good_dir, n))

    print("=== 5) 灵敏度验证:把真实页面改坏(只改内存,不写盘) ===")
    _mutation_test(good_dir)

    for label, d in [x for x in (("坏 · 全站", bad_dir), ("已修 · 全站", good_dir)) if x[1]]:
        print("=== 全站扫描:%s ===" % d)
        total = 0
        try:
            names = sorted(x for x in os.listdir(d) if x.endswith(".html"))
        except OSError as e:
            print("  读不到目录:%s" % e)
            continue
        for n in names:
            res = _run(label, os.path.join(d, n))
            total += len(res)
        print("  ---- %s 共 %d 页,总检出 %d 条 ----" % (label, len(names), total))
    return 1 if 坏 else 0


if __name__ == "__main__":
    try:                       # Windows 控制台默认 cp1252,中文直接崩
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(_selftest())
