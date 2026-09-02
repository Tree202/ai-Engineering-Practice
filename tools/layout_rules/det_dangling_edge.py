# -*- coding: utf-8 -*-
"""
检测器:SVG 悬空边(dangling_edge)

只报一种缺陷:**带箭头的那一端指向空处**。

典型症状(真值样本,01-overview.html 原版第 773 行):
    <path d="M 116 394 L 116 410 L 316 410" ... marker-end="url(#a2)"/>
终点 (316,410) 想指向「步骤 7」,但那个 <rect> 在 y=420 才开始。
箭头不是「差一点没够到」,而是整整偏出了目标框那一行 10px,
渲染出来就是一条戛然而止的断线,读者看不出「让它改」之后去哪。

一条边要同时满足下面三个条件才报,少一个都不报:

  1. 箭头尖没碰到任何东西 —— 端点离图里每一个节点的包围盒都超过 TOL(8px)。
  2. 这条边的另一端牢牢接在某个节点上(<= ANCHOR_TOL)。
     坐标轴箭头、图例箭头、指向画布外的示意箭头两头都不接节点,
     这一条把它们全滤掉(00/01 页时间轴那两条 47px 的箭头就是靠它排除的)。
  3. 把端点到「行进方向前方最近的那个节点」的间距,按行进方向分解成两个分量:
       - 垂直分量 lat > LAT_TOL(6px):箭头压根没对准目标那一行/那一列 —— 最刺眼的断线;
       - 平行分量 fwd > FWD_TOL(14px):方向对了但线画短了一大截。
     两者有一个成立才报。只差几 px 没够到的,是作者的画法,不报。
     目标节点只在**行进方向的前半平面**里挑,否则会把「刚离开的源节点」误当目标。

参数是从已修版 23 页 95 个箭头端点的实测分布反推的:
  端点到最近节点的距离全部 <= 8.0px(直方图 0/2/4/6/8),
  垂直偏移全部为 0;而缺陷处是 10.77px、垂直偏移 10px。8px / 6px 卡在两者中间。

保守取向(宁可漏报,不可滥报):
  * 无 marker 的那一端一律不查(汇入点、跨线跳线本来就从空白起笔)。
  * 节点包围盒宁可算大不算小(path 用含控制点的粗包围盒、text 用偏宽的字宽估算);
    盒子越大 → 距离越小 → 越不容易报,方向上是安全的。
  * 单个 <svg> 最多报 MAX_REPORT_PER_SVG 条,一张图崩了也不会刷屏。

只用 Python 3.12 标准库。
"""

from __future__ import annotations

import math
import os
import re
import sys
from html.parser import HTMLParser

# ---------------------------------------------------------------- 参数

TOL = 8.0          # 端点到最近节点包围盒的容差(px,SVG 用户坐标)
ANCHOR_TOL = 8.0   # 「另一端必须贴住某个节点」的容差,用来把坐标轴/图例箭头排除掉
LAT_TOL = 6.0      # 垂直于行进方向的偏移容差:箭头「有没有对准目标那一行/那一列」
FWD_TOL = 14.0     # 沿行进方向的短缺容差:方向对了、只是画短了,宽容一些
MAX_REPORT_PER_SVG = 3   # 单个 svg 最多报几条,防止一张图崩了刷屏

KIND = "dangling_edge"

# 这些容器里的东西不是画面上的图元(marker 里的三角形、渐变等)
SKIP_SUBTREE = {"defs", "marker", "clippath", "mask", "pattern", "symbol",
                "lineargradient", "radialgradient", "filter"}

# SVG 里这些标签没有闭合标签 / 一律当自闭合处理
SELF_CLOSING = {"rect", "circle", "ellipse", "line", "polyline", "polygon",
                "path", "use", "image", "stop", "br", "hr", "img"}

NODE_TAGS = {"rect", "circle", "ellipse", "polygon", "image", "use", "foreignobject"}
EDGE_TAGS = {"line", "polyline", "path"}

# 继承下去的表现属性
INHERIT = ("marker-end", "marker-start", "font-size", "text-anchor",
           "fill", "stroke", "font-weight", "display")


# ---------------------------------------------------------------- 小工具

_NUM = re.compile(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")


def _nums(s: str) -> list[float]:
    if not s:
        return []
    return [float(m.group()) for m in _NUM.finditer(s)]


def _f(attrs: dict, name: str, default: float = 0.0) -> float:
    v = attrs.get(name)
    if v is None:
        return default
    n = _nums(v)
    return n[0] if n else default


# ---------------------------------------------------------------- 变换矩阵

class Mat:
    """2D 仿射矩阵 [a c e; b d f]"""
    __slots__ = ("a", "b", "c", "d", "e", "f")

    def __init__(self, a=1.0, b=0.0, c=0.0, d=1.0, e=0.0, f=0.0):
        self.a, self.b, self.c, self.d, self.e, self.f = a, b, c, d, e, f

    def mul(self, o: "Mat") -> "Mat":
        # self * o
        return Mat(self.a * o.a + self.c * o.b,
                   self.b * o.a + self.d * o.b,
                   self.a * o.c + self.c * o.d,
                   self.b * o.c + self.d * o.d,
                   self.a * o.e + self.c * o.f + self.e,
                   self.b * o.e + self.d * o.f + self.f)

    def apply(self, x: float, y: float) -> tuple[float, float]:
        return (self.a * x + self.c * y + self.e,
                self.b * x + self.d * y + self.f)

    def scale_hint(self) -> float:
        """平均缩放倍率,用来把容差换算回本地坐标(通常是 1)。"""
        det = abs(self.a * self.d - self.b * self.c)
        return math.sqrt(det) if det > 0 else 1.0


_TRANSFORM = re.compile(r"(matrix|translate|scale|rotate|skewX|skewY)\s*\(([^)]*)\)")


def parse_transform(s: str) -> Mat:
    m = Mat()
    if not s:
        return m
    for fn, arg in _TRANSFORM.findall(s):
        v = _nums(arg)
        if fn == "matrix" and len(v) >= 6:
            t = Mat(*v[:6])
        elif fn == "translate":
            t = Mat(e=v[0] if v else 0.0, f=v[1] if len(v) > 1 else 0.0)
        elif fn == "scale":
            sx = v[0] if v else 1.0
            sy = v[1] if len(v) > 1 else sx
            t = Mat(a=sx, d=sy)
        elif fn == "rotate" and v:
            r = math.radians(v[0])
            cs, sn = math.cos(r), math.sin(r)
            t = Mat(cs, sn, -sn, cs)
            if len(v) >= 3:
                cx, cy = v[1], v[2]
                t = Mat(e=cx, f=cy).mul(t).mul(Mat(e=-cx, f=-cy))
        elif fn == "skewX" and v:
            t = Mat(c=math.tan(math.radians(v[0])))
        elif fn == "skewY" and v:
            t = Mat(b=math.tan(math.radians(v[0])))
        else:
            continue
        m = m.mul(t)
    return m


# ---------------------------------------------------------------- path 解析

_PATH_TOK = re.compile(r"([MmLlHhVvCcSsQqTtAaZz])|([-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?)")


def parse_path(d: str):
    """返回 (所有落点(含控制点,用于粗包围盒), 起点, 终点, 是否闭合)"""
    toks = []
    for m in _PATH_TOK.finditer(d or ""):
        toks.append(m.group(1) if m.group(1) else float(m.group(2)))

    pts: list[tuple[float, float]] = []
    cx = cy = 0.0
    sx = sy = 0.0          # 当前子路径起点
    start = None
    closed = False
    i = 0
    cmd = None
    while i < len(toks):
        t = toks[i]
        if isinstance(t, str):
            cmd = t
            i += 1
            if cmd in "Zz":
                closed = True
                cx, cy = sx, sy
                pts.append((cx, cy))
                continue
        if cmd is None:
            i += 1
            continue

        def take(n):
            nonlocal i
            vals = []
            for k in range(n):
                if i < len(toks) and not isinstance(toks[i], str):
                    vals.append(toks[i])
                    i += 1
                else:
                    vals.append(0.0)
            return vals

        rel = cmd.islower()
        c = cmd.upper()
        if c == "M":
            x, y = take(2)
            cx, cy = (cx + x, cy + y) if rel else (x, y)
            sx, sy = cx, cy
            cmd = "l" if rel else "L"     # 后续隐式为 lineto
        elif c == "L":
            x, y = take(2)
            cx, cy = (cx + x, cy + y) if rel else (x, y)
        elif c == "H":
            (x,) = take(1)
            cx = cx + x if rel else x
        elif c == "V":
            (y,) = take(1)
            cy = cy + y if rel else y
        elif c == "C":
            v = take(6)
            for k in (0, 2):
                px, py = (cx + v[k], cy + v[k + 1]) if rel else (v[k], v[k + 1])
                pts.append((px, py))
            cx, cy = (cx + v[4], cy + v[5]) if rel else (v[4], v[5])
        elif c == "S":
            v = take(4)
            px, py = (cx + v[0], cy + v[1]) if rel else (v[0], v[1])
            pts.append((px, py))
            cx, cy = (cx + v[2], cy + v[3]) if rel else (v[2], v[3])
        elif c == "Q":
            v = take(4)
            px, py = (cx + v[0], cy + v[1]) if rel else (v[0], v[1])
            pts.append((px, py))
            cx, cy = (cx + v[2], cy + v[3]) if rel else (v[2], v[3])
        elif c == "T":
            v = take(2)
            cx, cy = (cx + v[0], cy + v[1]) if rel else (v[0], v[1])
        elif c == "A":
            v = take(7)
            cx, cy = (cx + v[5], cy + v[6]) if rel else (v[5], v[6])
        else:
            i += 1
            continue
        pts.append((cx, cy))
        if start is None:
            start = (cx, cy)
    if not pts:
        return [], None, None, False
    return pts, (start or pts[0]), (cx, cy), closed


# ---------------------------------------------------------------- 数据结构

class Node:
    __slots__ = ("x0", "y0", "x1", "y1", "tag", "line", "label")

    def __init__(self, x0, y0, x1, y1, tag, line, label=""):
        self.x0, self.y0 = min(x0, x1), min(y0, y1)
        self.x1, self.y1 = max(x0, x1), max(y0, y1)
        self.tag, self.line, self.label = tag, line, label

    def dist(self, px, py) -> float:
        dx = max(self.x0 - px, 0.0, px - self.x1)
        dy = max(self.y0 - py, 0.0, py - self.y1)
        return math.hypot(dx, dy)

    def desc(self) -> str:
        s = "<%s (%.0f,%.0f)-(%.0f,%.0f)>" % (self.tag, self.x0, self.y0, self.x1, self.y1)
        if self.label:
            s += " 「%s」" % self.label[:18]
        return s


class Edge:
    __slots__ = ("pts", "start", "end", "line", "tag", "raw", "m_end", "m_start")

    def __init__(self, pts, start, end, line, tag, raw, m_end, m_start):
        self.pts, self.start, self.end = pts, start, end
        self.line, self.tag, self.raw = line, tag, raw
        self.m_end, self.m_start = m_end, m_start


def _bbox_from_points(pts, mat: Mat):
    tp = [mat.apply(x, y) for x, y in pts]
    xs = [p[0] for p in tp]
    ys = [p[1] for p in tp]
    return min(xs), min(ys), max(xs), max(ys)


def _text_width(s: str, fs: float, bold: bool) -> float:
    w = 0.0
    for ch in s:
        o = ord(ch)
        if o >= 0x2E80 or o in (0x2014, 0x2013):     # CJK / 全角标点
            w += 1.02
        elif ch == " ":
            w += 0.30
        elif ch in "iljI.,'|":
            w += 0.30
        else:
            w += 0.58
    return w * fs * (1.06 if bold else 1.0)


# ---------------------------------------------------------------- 解析器

class SvgScan(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.reset_state()

    def reset_state(self):
        self.svgs: list[dict] = []        # {"line":int, "nodes":[], "edges":[]}
        self._cur = None
        self._svg_depth = 0
        self._skip_depth = 0
        self._stack: list[tuple[str, Mat, dict]] = []
        self._text_buf = None             # (line, x, y, mat, props)

    # ---- 属性继承
    def _props(self) -> dict:
        return self._stack[-1][2] if self._stack else {}

    def _mat(self) -> Mat:
        return self._stack[-1][1] if self._stack else Mat()

    def _push(self, tag, attrs: dict):
        base = dict(self._props())
        for k in INHERIT:
            if k in attrs:
                base[k] = attrs[k]
        style = attrs.get("style", "")
        if style:
            for k in INHERIT:
                mm = re.search(re.escape(k) + r"\s*:\s*([^;]+)", style)
                if mm:
                    base[k] = mm.group(1).strip()
        m = self._mat()
        if attrs.get("transform"):
            m = m.mul(parse_transform(attrs["transform"]))
        self._stack.append((tag, m, base))

    # ---- HTMLParser 回调
    def handle_starttag(self, tag, attrs):
        self._on_start(tag, dict(attrs), self.getpos()[0])
        if tag in SELF_CLOSING:
            self._on_end(tag)

    def handle_startendtag(self, tag, attrs):
        self._on_start(tag, dict(attrs), self.getpos()[0])
        self._on_end(tag)

    def handle_endtag(self, tag):
        self._on_end(tag)

    def handle_data(self, data):
        if self._text_buf is not None:
            self._text_buf[-1].append(data)

    # ---- 核心
    def _on_start(self, tag, attrs, line):
        if tag == "svg":
            self._svg_depth += 1
            if self._svg_depth == 1:
                self._cur = {"line": line, "nodes": [], "edges": []}
                self._stack = []
                self._skip_depth = 0
        if self._svg_depth == 0:
            return

        self._push(tag, attrs)

        if self._skip_depth or tag in SKIP_SUBTREE:
            if tag in SKIP_SUBTREE:
                self._skip_depth += 1
            return

        props = self._props()
        if str(props.get("display", "")).strip() == "none":
            return
        mat = self._mat()
        c = self._cur
        if c is None:
            return

        try:
            self._emit(tag, attrs, line, mat, props, c)
        except Exception:
            pass

    def _emit(self, tag, attrs, line, mat, props, c):
        # ------- 节点
        if tag == "rect":
            x, y = _f(attrs, "x"), _f(attrs, "y")
            w, h = _f(attrs, "width"), _f(attrs, "height")
            if w > 0 and h > 0:
                bb = _bbox_from_points([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], mat)
                c["nodes"].append(Node(*bb, "rect", line))
            return
        if tag in ("circle", "ellipse"):
            cx, cy = _f(attrs, "cx"), _f(attrs, "cy")
            if tag == "circle":
                rx = ry = _f(attrs, "r")
            else:
                rx, ry = _f(attrs, "rx"), _f(attrs, "ry")
            if rx > 0 and ry > 0:
                bb = _bbox_from_points([(cx - rx, cy - ry), (cx + rx, cy - ry),
                                        (cx + rx, cy + ry), (cx - rx, cy + ry)], mat)
                c["nodes"].append(Node(*bb, tag, line))
            return
        if tag in ("polygon", "image", "use", "foreignobject"):
            if tag == "polygon":
                v = _nums(attrs.get("points", ""))
                pts = list(zip(v[0::2], v[1::2]))
            else:
                x, y = _f(attrs, "x"), _f(attrs, "y")
                w, h = _f(attrs, "width"), _f(attrs, "height")
                pts = [(x, y), (x + max(w, 1), y + max(h, 1))]
            if pts:
                c["nodes"].append(Node(*_bbox_from_points(pts, mat), tag, line))
            return

        # ------- 文字:开始缓冲
        if tag in ("text", "tspan"):
            if tag == "text":
                self._text_buf = [line, _f(attrs, "x") + _f(attrs, "dx"),
                                  _f(attrs, "y") + _f(attrs, "dy"), mat, dict(props), []]
            return

        # ------- 边
        if tag in EDGE_TAGS:
            m_end = str(props.get("marker-end", "")).strip().lower()
            m_start = str(props.get("marker-start", "")).strip().lower()
            has_end = m_end.startswith("url(")
            has_start = m_start.startswith("url(")

            if tag == "line":
                p0 = (_f(attrs, "x1"), _f(attrs, "y1"))
                p1 = (_f(attrs, "x2"), _f(attrs, "y2"))
                pts, closed = [p0, p1], False
            elif tag == "polyline":
                v = _nums(attrs.get("points", ""))
                pts = list(zip(v[0::2], v[1::2]))
                if len(pts) < 2:
                    return
                p0, p1, closed = pts[0], pts[-1], False
            else:  # path
                pts, p0, p1, closed = parse_path(attrs.get("d", ""))
                if not pts or p0 is None:
                    return
                fill = str(props.get("fill", "")).strip().lower()
                # 有填充或闭合的 path 是形状,不是边;也当成节点候选
                if closed or (fill and fill not in ("none", "transparent")):
                    c["nodes"].append(Node(*_bbox_from_points(pts, mat), "path", line))
                    if not has_end and not has_start:
                        return

            if not has_end and not has_start:
                return
            if p0 == p1:
                return
            c["edges"].append(Edge([mat.apply(*p) for p in pts],
                                   mat.apply(*p0), mat.apply(*p1),
                                   line, tag, _short(attrs), has_end, has_start))

    def _on_end(self, tag):
        if self._svg_depth == 0:
            return
        if tag == "text" and self._text_buf is not None:
            self._flush_text()
        if self._stack and self._stack[-1][0] == tag:
            self._stack.pop()
        elif self._stack:
            for k in range(len(self._stack) - 1, -1, -1):
                if self._stack[k][0] == tag:
                    del self._stack[k:]
                    break
        if tag in SKIP_SUBTREE and self._skip_depth:
            self._skip_depth -= 1
        if tag == "svg":
            self._svg_depth -= 1
            if self._svg_depth == 0 and self._cur is not None:
                self.svgs.append(self._cur)
                self._cur = None

    def _flush_text(self):
        line, x, y, mat, props, chunks = self._text_buf
        self._text_buf = None
        if self._cur is None or self._skip_depth:
            return
        s = "".join(chunks).strip()
        if not s:
            return
        fs = _f(props, "font-size", 16.0) if isinstance(props.get("font-size"), str) else 16.0
        try:
            fs = float(_nums(str(props.get("font-size", "16")))[0])
        except Exception:
            fs = 16.0
        if fs <= 0:
            fs = 16.0
        bold = str(props.get("font-weight", "")).strip() in ("bold", "700", "800", "900")
        w = _text_width(s, fs, bold)
        anchor = str(props.get("text-anchor", "start")).strip()
        if anchor == "middle":
            x0 = x - w / 2
        elif anchor == "end":
            x0 = x - w
        else:
            x0 = x
        pts = [(x0, y - 0.82 * fs), (x0 + w, y - 0.82 * fs),
               (x0 + w, y + 0.24 * fs), (x0, y + 0.24 * fs)]
        self._cur["nodes"].append(Node(*_bbox_from_points(pts, mat), "text", line, s))


def _tangent(pts, at_end: bool):
    """箭头那一端的行进方向(单位向量)。曲线的控制点也在 pts 里,
    所以 pts[-2]→pts[-1] 正好是端点处的切线。"""
    if len(pts) < 2:
        return None
    if at_end:
        tip = pts[-1]
        seq = range(len(pts) - 2, -1, -1)
    else:
        tip = pts[0]
        seq = range(1, len(pts))
    for i in seq:
        dx, dy = tip[0] - pts[i][0], tip[1] - pts[i][1]
        L = math.hypot(dx, dy)
        if L > 1e-6:
            return (dx / L, dy / L)
    return None


def _short(attrs: dict) -> str:
    for k in ("d", "points"):
        if k in attrs:
            v = " ".join(attrs[k].split())
            return '%s="%s"' % (k, v if len(v) <= 70 else v[:67] + "…")
    return " ".join('%s="%s"' % (k, attrs.get(k, "")) for k in ("x1", "y1", "x2", "y2")
                    if k in attrs)


# ---------------------------------------------------------------- 主检查

def check(path: str, html: str) -> list[dict]:
    """返回 [{"line":int,"kind":str,"msg":str,"detail":str}, ...]"""
    if "_v1" in str(path).replace("\\", "/").split("/"):
        return []
    if "<svg" not in html:
        return []

    p = SvgScan()
    try:
        p.feed(html)
        p.close()
    except Exception:
        raise  # 让驱动层的「规则异常」兜底可见,别把解析失败伪装成零检出

    out: list[dict] = []
    for si, svg in enumerate(p.svgs, 1):
        nodes = svg["nodes"]
        edges = svg["edges"]
        if not nodes or not edges:
            continue
        # 图太小(节点少于 3 个)时形状估算不可靠,跳过
        if len(nodes) < 3:
            continue

        def nearest(e: Edge, px: float, py: float):
            best, bestd = None, 1e18
            for n in nodes:
                # 不拿这条边自己当参照(闭合 path 既是节点又是边的情况)
                if n.line == e.line and n.tag == "path":
                    continue
                d = n.dist(px, py)
                if d < bestd:
                    bestd, best = d, n
            return best, bestd

        def forward_target(e: Edge, px, py, dx, dy):
            """箭头朝哪个节点去的:只在行进方向的前半平面里挑最近的一个。
            这样才不会把「刚离开的那个源节点」当成目标。"""
            best, bestd = None, 1e18
            for n in nodes:
                if n.line == e.line and n.tag == "path":
                    continue
                cx, cy = (n.x0 + n.x1) / 2, (n.y0 + n.y1) / 2
                if (cx - px) * dx + (cy - py) * dy <= 0:
                    continue
                d = n.dist(px, py)
                if d < bestd:
                    bestd, best = d, n
            return best, bestd

        hits = []
        for e in edges:
            ends = []
            if e.m_end:
                ends.append(("终点", e.end, e.start, "marker-end", True))
            if e.m_start:
                ends.append(("起点", e.start, e.end, "marker-start", False))
            for which, pt, other, marker, at_end in ends:
                px, py = pt

                # 条件 1:箭头尖没碰到任何东西 —— 离最近的节点都超过容差。
                near, neard = nearest(e, px, py)
                if near is None or neard <= TOL:
                    continue

                # 条件 2:只报「一头接着、另一头掉了」的边。
                # 坐标轴箭头、图例箭头、指向画布外的示意箭头两头都不接节点,
                # 那是画法而不是缺陷 —— 用这一条把它们全滤掉。
                _, otherd = nearest(e, other[0], other[1])
                if otherd > ANCHOR_TOL:
                    continue

                tan = _tangent(e.pts, at_end)
                if tan is None:
                    continue
                dx, dy = tan
                tgt, tgtd = forward_target(e, px, py, dx, dy)
                if tgt is None:
                    # 前方什么都没有,箭头彻底射向空白
                    tgt, tgtd = near, neard
                    lat = fwd = tgtd
                    why = "箭头前方根本没有任何节点"
                else:
                    gx = max(tgt.x0 - px, 0.0, px - tgt.x1)
                    gy = max(tgt.y0 - py, 0.0, py - tgt.y1)
                    # 沿行进方向分解:主轴上的差是「还差多远才够到」,
                    # 垂直轴上的差是「压根没对准」。后者才是真正刺眼的断线。
                    if abs(dx) >= abs(dy):
                        fwd, lat = gx, gy
                    else:
                        fwd, lat = gy, gx
                    if lat > LAT_TOL:
                        why = ("箭头垂直于行进方向偏出目标 %.1fpx —— 它指的位置根本不在目标框那一行/那一列上"
                               % lat)
                    elif fwd > FWD_TOL:
                        why = "箭头方向对准了目标,但沿途少画了 %.1fpx,尖端停在半空" % fwd
                    else:
                        continue

                hits.append((neard, which, px, py, tgt, e, marker, otherd, lat, fwd, why))

        hits.sort(key=lambda h: -h[0])
        for neard, which, px, py, tgt, e, marker, otherd, lat, fwd, why in hits[:MAX_REPORT_PER_SVG]:
            out.append({
                "line": e.line,
                "kind": KIND,
                "msg": ("SVG 悬空边:<%s> 的%s(%.0f,%.0f)带箭头(%s),"
                        "离最近的节点还有 %.1fpx,箭头停在空白处"
                        % (e.tag, which, px, py, marker, neard)),
                "detail": ("第 %d 个 <svg>(起于第 %d 行)。边:%s\n"
                           "  箭头朝向的目标是 %s。%s。\n"
                           "  判据:尖端离任何节点都有 %.1fpx(>容差 %.0fpx);"
                           "另一端却牢牢接在节点上(%.1fpx),说明这是一条真的连接线,"
                           "不是坐标轴或图例箭头。\n"
                           "  读者看到的是一条指向空处的断线,看不出这一步接下来去哪。"
                           % (si, svg["line"], e.raw, tgt.desc(), why, neard, TOL, otherd)),
            })
    out.sort(key=lambda r: r["line"])
    return out


# ---------------------------------------------------------------- 自测

def _run(p):
    try:
        with open(p, "r", encoding="utf-8") as fh:
            return check(p, fh.read())
    except FileNotFoundError:
        return None


def _show(title, path):
    res = _run(path)
    print("=" * 78)
    print(title)
    print("  ", path)
    if res is None:
        print("   [文件不存在]")
        return 0
    if not res:
        print("   -> 无检出")
    for r in res:
        print("   -> L%-5d %s" % (r["line"], r["msg"]))
        for ln in r["detail"].splitlines():
            print("        " + ln)
    return len(res)


if __name__ == "__main__":
    try:                                   # Windows 控制台默认 cp1252,中文会炸
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    BAD = "D:/ext.zhaoliuliu3/Desktop/ai-workflow"
    GOOD = "D:/ext.zhaoliuliu3/Desktop/claude_AI/ai-workflow"

    print("### 容差 TOL = %.1f px\n" % TOL)
    _show("坏样本(应报出 01 页那条悬空边)", os.path.join(BAD, "01-overview.html"))
    _show("好样本(同一处,应无检出)", os.path.join(GOOD, "01-overview.html"))
    _show("坏样本 02(本路不负责,预期无检出)", os.path.join(BAD, "02-python-setup.html"))

    print("=" * 78)
    print("全量:%s 下 23 页" % GOOD)
    total = 0
    for fn in sorted(os.listdir(GOOD)):
        if not fn.endswith(".html"):
            continue
        fp = os.path.join(GOOD, fn)
        res = _run(fp) or []
        total += len(res)
        if res:
            for r in res:
                print("  %-24s L%-5d %s" % (fn, r["line"], r["msg"]))
    print("  总检出数 = %d" % total)
    sys.exit(0)
