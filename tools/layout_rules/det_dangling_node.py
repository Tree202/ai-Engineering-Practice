# -*- coding: utf-8 -*-
"""
det_dangling_node.py —— SVG 悬空节点检测器 (key: dangling_node)

只用 Python 3.12 标准库。

检测目标
--------
手写 SVG 流程图里 **有入边、却一条出边都没有** 的节点。
典型症状:一个分支画到某个方框就断了,读者看不出这条分支之后回到哪里。
(样本缺陷 A:01-overview.html 的七步流程图里 5b / 5c / 兜底 三格画完就断)

做法
----
1. 从每个 <svg> 里抽 **节点**(rect / circle / ellipse / polygon,过滤掉
   defs 里的箭头、太小的装饰点、以及"把别的节点整个框住"的分组底板)。
2. 抽 **边**(line / path / polyline,必须有 stroke 且 fill 为 none)。
   path 的 d 用一个小型解析器走 M/m L/l H/h V/v C/c S/s Q/q T/t A/a Z,
   取首点和末点。
3. 每条边的两端按 **12px 容差吸附**到最近的节点包围盒。
   带 marker-end 的边(marker-end 可以写在自己身上,也可以继承自祖先 <g>)
   算有向边:尾 -> 头;带 marker-start 而无 marker-end 的反过来;
   两个 marker 都没有的边按 **无向** 处理 —— 无向边给两端都记出度,
   这是"宁可漏报"的方向。
4. 报出 入度>=1 且 出度==0 的节点。

放过合理终点(这几条是为了压误报,顺序即优先级)
------------------------------------------------
- 一张图里零出度节点 **只有一个** -> 不报(那就是正常终点)。
- 全图最靠下那一排(y2 落在全图最大 y2 的 8px 内)的零出度节点 -> 不报。
- 剔完之后剩下的零出度节点 **少于 2 个** -> 整张图都不报。
- 要求该节点的入边来自 **另一个节点**(尾端也吸附上了),躲开孤立装饰箭头。
- 整张图节点 < 4 个、或有向边 < 2 条 -> 不是流程图,不看。
"""

from __future__ import annotations

import html as _html
import os

from _baseline import 样本, 基线目录, 已修目录  # noqa: E402
import re
import sys

# ---------------------------------------------------------------- 调参区
SNAP_TOL = 12.0        # 端点吸附到节点包围盒的容差(px)
MIN_NODE_W = 34.0      # 节点最小宽(滤掉刻度、圆点、小色块)
MIN_NODE_H = 20.0      # 节点最小高
MIN_CIRCLE_R = 14.0    # 圆形节点最小半径
MIN_EDGE_LEN = 3.0     # 边的最小首末距离(16-pipeline 里相邻格之间只有 5px 的箭头)
BOTTOM_TOL = 8.0       # "最靠下一排"的容差
ROW_TOL = 12.0         # 判定"同一排"的竖直中心容差
MIN_NODES = 4          # 低于此节点数不当流程图
MIN_DIRECTED = 2       # 低于此有向边数不当流程图

_NUM = re.compile(r"[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?")
_TAG = re.compile(
    r"<(/?)([A-Za-z][\w:.-]*)((?:\s+[\w:.-]+(?:\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s\"'>]+))?)*)\s*(/?)>",
    re.S,
)
_ATTR = re.compile(
    r"([\w:.-]+)(?:\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s\"'>]+)))?"
)
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_SVG = re.compile(r"<svg\b.*?</svg\s*>", re.S | re.I)

_CMD_ARGS = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4,
             "Q": 4, "T": 2, "A": 7, "Z": 0}
_CMD_SPLIT = re.compile(r"([MmLlHhVvCcSsQqTtAaZz])([^MmLlHhVvCcSsQqTtAaZz]*)")


# ---------------------------------------------------------------- 小工具
def _attrs(blob: str) -> dict:
    out = {}
    for m in _ATTR.finditer(blob or ""):
        name = m.group(1).lower()
        val = m.group(2)
        if val is None:
            val = m.group(3)
        if val is None:
            val = m.group(4)
        out[name] = val if val is not None else ""
    return out


def _f(d: dict, key: str, default=None):
    v = d.get(key)
    if v is None:
        return default
    m = _NUM.search(v)
    return float(m.group()) if m else default


def _blank_comments(text: str) -> str:
    """把注释挖空但保留换行,行号才不会漂。"""
    def rep(m):
        return re.sub(r"[^\n]", " ", m.group())
    return _COMMENT.sub(rep, text)


def _clean_text(s: str) -> str:
    s = re.sub(r"<[^>]*>", "", s)
    s = _html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def _parse_path(d: str) -> list[tuple[float, float]]:
    """把 path 的 d 走一遍,返回沿途的绝对坐标点(只要首末,但顺手全收）。"""
    pts: list[tuple[float, float]] = []
    cx = cy = 0.0
    sx = sy = 0.0
    started = False
    for m in _CMD_SPLIT.finditer(d or ""):
        c = m.group(1)
        nums = [float(x) for x in _NUM.findall(m.group(2))]
        u = c.upper()
        n = _CMD_ARGS[u]
        if n == 0:                       # Z
            if started:
                cx, cy = sx, sy
                pts.append((cx, cy))
            continue
        if not nums:
            continue
        cur = c
        k = 0
        while k + n <= len(nums):
            a = nums[k:k + n]
            k += n
            uu = cur.upper()
            rel = cur.islower()
            if uu == "H":
                cx = cx + a[0] if rel else a[0]
            elif uu == "V":
                cy = cy + a[0] if rel else a[0]
            else:
                ex, ey = a[-2], a[-1]
                if rel:
                    cx, cy = cx + ex, cy + ey
                else:
                    cx, cy = ex, ey
            if uu == "M" and not started:
                sx, sy, started = cx, cy, True
            elif uu == "M":
                sx, sy = cx, cy
            pts.append((cx, cy))
            # 同一命令后面跟多组参数时:M 续写成 L,m 续写成 l
            if cur == "M":
                cur = "L"
            elif cur == "m":
                cur = "l"
            n = _CMD_ARGS[cur.upper()]
    return pts


def _box_dist(pt, box) -> float:
    x, y = pt
    x1, y1, x2, y2 = box
    dx = max(x1 - x, 0.0, x - x2)
    dy = max(y1 - y, 0.0, y - y2)
    return (dx * dx + dy * dy) ** 0.5


# ---------------------------------------------------------------- SVG 扫描
class _Node:
    __slots__ = ("box", "line", "tag", "label", "area")

    def __init__(self, box, line, tag):
        self.box = box
        self.line = line
        self.tag = tag
        self.label = ""
        self.area = max(1.0, (box[2] - box[0]) * (box[3] - box[1]))


def _scan_svg(svg: str, base_line: int):
    """返回 (nodes, edges, texts)。edges 项为 (p0, p1, directed, reverse)。"""
    nodes: list[_Node] = []
    edges: list[tuple] = []
    texts: list[tuple[float, float, str]] = []

    stack: list[dict] = []          # 祖先 <g> 的属性(用于继承 marker/stroke/fill)
    defs_depth = 0
    pos = 0
    while True:
        m = _TAG.search(svg, pos)
        if not m:
            break
        pos = m.end()
        closing, tag, blob, selfclose = m.group(1), m.group(2).lower(), m.group(3), m.group(4)
        line = base_line + svg.count("\n", 0, m.start())

        if closing:
            if tag in ("defs", "marker", "clippath", "mask", "pattern", "symbol"):
                defs_depth = max(0, defs_depth - 1)
            elif stack and stack[-1].get("__tag__") == tag:
                stack.pop()
            continue

        if tag in ("defs", "marker", "clippath", "mask", "pattern", "symbol"):
            if not selfclose:
                defs_depth += 1
            continue
        if defs_depth:
            continue

        a = _attrs(blob)

        if tag in ("g", "svg", "a"):
            if not selfclose:
                a["__tag__"] = tag
                stack.append(a)
            continue

        # 继承链:祖先 <g> 的 marker-end / stroke / fill
        def inh(key):
            if a.get(key):
                return a[key]
            for anc in reversed(stack):
                if anc.get(key):
                    return anc[key]
            return None

        if tag == "text":
            tx, ty = _f(a, "x"), _f(a, "y")
            end = svg.find("</text", pos)
            body = svg[pos:end] if end != -1 else ""
            if tx is not None and ty is not None:
                txt = _clean_text(body)
                if txt:
                    texts.append((tx, ty, txt))
            continue

        # ---- 节点 ----
        if tag == "rect":
            x, y = _f(a, "x", 0.0), _f(a, "y", 0.0)
            w, h = _f(a, "width"), _f(a, "height")
            if w and h and w >= MIN_NODE_W and h >= MIN_NODE_H:
                nodes.append(_Node((x, y, x + w, y + h), line, "rect"))
            continue
        if tag in ("circle", "ellipse"):
            cx, cy = _f(a, "cx"), _f(a, "cy")
            if cx is None or cy is None:
                continue
            rx = _f(a, "r") or _f(a, "rx")
            ry = _f(a, "r") or _f(a, "ry")
            if rx and ry and rx >= MIN_CIRCLE_R and ry >= MIN_CIRCLE_R / 2:
                nodes.append(_Node((cx - rx, cy - ry, cx + rx, cy + ry), line, tag))
            continue
        if tag == "polygon":
            nums = [float(x) for x in _NUM.findall(a.get("points", ""))]
            xs, ys = nums[0::2], nums[1::2]
            if len(xs) >= 3 and len(xs) == len(ys):
                bx = (min(xs), min(ys), max(xs), max(ys))
                if bx[2] - bx[0] >= MIN_NODE_W and bx[3] - bx[1] >= MIN_NODE_H:
                    nodes.append(_Node(bx, line, "polygon"))
            continue

        # ---- 边 ----
        if tag in ("line", "path", "polyline"):
            stroke = inh("stroke")
            fill = inh("fill")
            if not stroke or stroke.lower() == "none":
                continue
            if fill and fill.lower() not in ("none", "transparent"):
                continue          # 有填充的 path 是图形不是边
            if tag == "line":
                p0 = (_f(a, "x1"), _f(a, "y1"))
                p1 = (_f(a, "x2"), _f(a, "y2"))
                if None in p0 or None in p1:
                    continue
                pts = [p0, p1]
            elif tag == "path":
                pts = _parse_path(a.get("d", ""))
            else:
                nums = [float(x) for x in _NUM.findall(a.get("points", ""))]
                pts = list(zip(nums[0::2], nums[1::2]))
            if len(pts) < 2:
                continue
            p0, p1 = pts[0], pts[-1]
            if ((p0[0] - p1[0]) ** 2 + (p0[1] - p1[1]) ** 2) ** 0.5 < MIN_EDGE_LEN:
                continue
            me = inh("marker-end")
            ms = inh("marker-start")
            if me and me.lower() != "none":
                edges.append((p0, p1, True, False))
            elif ms and ms.lower() != "none":
                edges.append((p0, p1, True, True))
            else:
                edges.append((p0, p1, False, False))
            continue

    return nodes, edges, texts


def _drop_containers(nodes: list[_Node]) -> list[_Node]:
    """把"整个框住 >=2 个别的节点"的底板/分组框剔掉,它们不是流程节点。"""
    keep = []
    for i, n in enumerate(nodes):
        x1, y1, x2, y2 = n.box
        inside = 0
        for j, o in enumerate(nodes):
            if i == j:
                continue
            ox1, oy1, ox2, oy2 = o.box
            if ox1 >= x1 - 2 and oy1 >= y1 - 2 and ox2 <= x2 + 2 and oy2 <= y2 + 2 \
               and o.area < n.area * 0.9:
                inside += 1
        if inside < 2:
            keep.append(n)
    return keep


def _snap(pt, nodes: list[_Node]):
    best, bd = None, SNAP_TOL + 1.0
    for n in nodes:
        d = _box_dist(pt, n.box)
        if d <= SNAP_TOL and (d < bd - 1e-9 or (abs(d - bd) < 1e-9 and best and n.area < best.area)):
            best, bd = n, d
    return best


def _label(n: _Node, texts) -> str:
    x1, y1, x2, y2 = n.box
    cand = [(ty, t) for (tx, ty, t) in texts
            if x1 - 4 <= tx <= x2 + 4 and y1 - 4 <= ty <= y2 + 6]
    if not cand:
        return ""
    cand.sort()
    return cand[0][1]


# ---------------------------------------------------------------- 对外接口
def check(path: str, html_text: str) -> list[dict]:
    """返回问题列表,每项 {"line": int, "kind": str, "msg": str, "detail": str}"""
    if re.search(r"[\\/]_v1[\\/]", path.replace("\\", "/")):
        return []

    src = _blank_comments(html_text)
    out: list[dict] = []

    for si, sm in enumerate(_SVG.finditer(src), 1):
        svg = sm.group()
        base_line = src.count("\n", 0, sm.start()) + 1
        nodes, edges, texts = _scan_svg(svg, base_line)
        nodes = _drop_containers(nodes)

        n_directed = sum(1 for e in edges if e[2])
        if len(nodes) < MIN_NODES or n_directed < MIN_DIRECTED:
            continue

        indeg = {id(n): 0 for n in nodes}
        outdeg = {id(n): 0 for n in nodes}
        in_from_node = {id(n): 0 for n in nodes}

        for p0, p1, directed, rev in edges:
            tail, head = (p1, p0) if rev else (p0, p1)
            a = _snap(tail, nodes)
            b = _snap(head, nodes)
            if a is not None and b is not None and a is b:
                continue                      # 自环/贴同一个框的短线,忽略
            if directed:
                if a is not None:
                    outdeg[id(a)] += 1
                if b is not None:
                    indeg[id(b)] += 1
                    if a is not None:
                        in_from_node[id(b)] += 1
            else:
                # 无向边:两端都记出度(保守,压误报)
                if a is not None:
                    outdeg[id(a)] += 1
                if b is not None:
                    outdeg[id(b)] += 1

        zero = [n for n in nodes if indeg[id(n)] >= 1 and outdeg[id(n)] == 0
                and in_from_node[id(n)] >= 1]
        if len(zero) < 2:
            continue                          # 唯一零出度 = 正常终点

        max_bottom = max(n.box[3] for n in nodes)
        cand = [n for n in zero if n.box[3] < max_bottom - BOTTOM_TOL]

        # 兄弟排不对称:同一排(竖直中心相近)里必须有别的节点是**有出边**的。
        # 5a/5b/5c/兜底 同排,只有 5a 画了回程边 —— 这种不对称才说明是漏画;
        # 而 17 页三个「否」结局同排、全都没有出边,那是一整排合理终点,放过。
        def _row_peers_have_out(n):
            cy = (n.box[1] + n.box[3]) / 2.0
            for m in nodes:
                if m is n:
                    continue
                if abs((m.box[1] + m.box[3]) / 2.0 - cy) <= ROW_TOL and outdeg[id(m)] >= 1:
                    return True
            return False

        suspects = [n for n in cand if _row_peers_have_out(n)]
        if len(suspects) < 2:
            continue                          # 剔掉合理终点后不成规模,不报

        bottom_lbl = "、".join(
            (_label(m, texts) or "?")
            for m in nodes if m.box[3] >= max_bottom - BOTTOM_TOL)

        for n in sorted(suspects, key=lambda n: (n.box[1], n.box[0])):
            lab = _label(n, texts) or f"<{n.tag}> @ ({n.box[0]:.0f},{n.box[1]:.0f})"
            cy = (n.box[1] + n.box[3]) / 2.0
            peers = [(_label(m, texts) or "?") for m in nodes
                     if m is not n
                     and abs((m.box[1] + m.box[3]) / 2.0 - cy) <= ROW_TOL
                     and outdeg[id(m)] >= 1]
            out.append({
                "line": n.line,
                "kind": "dangling_node",
                "msg": f"SVG 流程图里「{lab}」只有入边、没有出边,这条分支画到这里就断了",
                "detail": (
                    f"第 {si} 张 <svg>:节点 <{n.tag}> "
                    f"x={n.box[0]:.0f} y={n.box[1]:.0f} "
                    f"w={n.box[2]-n.box[0]:.0f} h={n.box[3]-n.box[1]:.0f},"
                    f"入度 {indeg[id(n)]}、出度 0。"
                    f"同图共 {len(suspects)} 个这样的零出度节点;"
                    f"最靠下那一排(「{bottom_lbl}」)已按正常终点放过。"
                    f"同排的「{'、'.join(peers)}」是画了出边的 —— "
                    f"同一排兄弟节点有的有去路、有的没有,通常就是漏画了这条边,"
                    f"读者看不出走完这一格之后该去哪。"
                ),
            })

    return out


# ---------------------------------------------------------------- 自测
def _run(p: str):
    with open(p, encoding="utf-8") as f:
        return check(p, f.read())


def _selftest():
    bad, good = 样本("01-overview.html")
    坏 = 0

    for tag, p, 期望 in (("坏样本", bad, 3), ("好样本(已修)", good, 0)):
        print(f"\n===== {tag}: {p or '(无基线)'}")
        if not (p and os.path.exists(p)):
            print("  (样本不在,跳过真值段 —— 不当作通过)")
            continue
        res = _run(p)
        坏 += 0 if len(res) == 期望 else 1
        print(f"  检出 {len(res)} 条(期望 {期望})"
              f"{'' if len(res) == 期望 else '  <== 失败'}")
        for r in res:
            print(f"  L{r['line']:>5}  [{r['kind']}] {r['msg']}")
            print(f"         {r['detail']}")

    book = 已修目录()
    if os.path.isdir(book):
        print(f"\n===== 全书扫描: {book}")
        total = 0
        for fn in sorted(os.listdir(book)):
            if not fn.endswith(".html"):
                continue
            res = _run(os.path.join(book, fn))
            total += len(res)
            if res:
                for r in res:
                    print(f"  {fn}:{r['line']}  {r['msg']}")
        print(f"  23 页合计检出 {total} 条")
    return 1 if 坏 else 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    sys.exit(_selftest())
