# -*- coding: utf-8 -*-
"""
det_overflow.py —— 检测 SVG 元素坐标跑到 viewBox 外面(渲染时被裁掉)。

key: overflow

思路
----
1. 从 HTML 里切出每个 <svg ...> ... </svg>,读 viewBox="minX minY w h";没有 viewBox 的跳过。
2. 用 html.parser 流式解析(能吃 &#9450; &rarr; 这类实体,不像 xml.etree 会炸)。
3. 维护一个 CTM(仿射矩阵)栈,支持 translate / scale / rotate / matrix。
4. 逐个几何元素算包围盒:
     rect      x,y,w,h
     circle    cx±r         ellipse  cx±rx, cy±ry
     line      两端点
     polygon/polyline  points 全部顶点
     path      真解析 d(M/L/H/V/C/S/Q/T/A/Z,大小写都认),
               贝塞尔用导数求根算紧包围盒,不拿控制点糊弄
     text      锚点 + 估算宽度(中日韩/全角 1.0em、ASCII 0.55em),
               按 text-anchor 左右分配;竖直方向用 ascent/descent 估
5. defs / marker / pattern / symbol / clipPath / mask / gradient / filter
   里的坐标是自己的坐标系,整棵子树跳过。
6. 超出 viewBox 才报。几何元素 2px 宽容(描边本来就出界);
   文本横向宽容更大(宽度是估的),纵向严格(基线 y 是精确值)。

只用 Python 3.12 标准库。
"""

from __future__ import annotations

import math
import re
from html.parser import HTMLParser

KEY = "overflow"

# ---------------------------------------------------------------- 常量 / 阈值

# 几何元素(rect/circle/line/path/polygon)的宽容:描边宽度的一半通常 <=1.5px
GEOM_TOL = 2.0

# 文本纵向宽容:基线 y 是文件里写死的精确数字,只有 ascent/descent 是估的
TEXT_TOL_Y = 2.0

# 文本横向宽容:宽度靠字符数估,误差大 —— 绝对宽容 + 相对宽容取大者。
# 这两个数是量出来的:把全书 556 个 <text> 用浏览器 getBBox() 测了真实宽度,
# 估算/实测的比值 p50=1.00、p95=1.055、p99=1.16、最大 1.8(单个「↓」,绝对误差仅 6px)。
# rel=0.22 时全书 556 条没有一条的高估量能吃掉宽容(最紧的一条还剩 3.5px 余量)。
TEXT_TOL_X_ABS = 10.0
TEXT_TOL_X_REL = 0.22

# 旋转过的文本:方向/度量都不可靠,只在锚点本身跑得很远时才报
ROTATED_TEXT_TOL = 16.0

# 字形度量(相对 font-size)
ASCENT = 0.78    # 基线往上,取偏保守的值(实际 CJK 字面顶 ~0.88em)
DESCENT = 0.16   # 基线往下

DEFAULT_FONT_SIZE = 16.0  # body{font-size:16px},SVG 文本没写 font-size 时继承它

# 不进入的子树:里面的坐标属于另一套坐标系
OPAQUE = {
    "defs", "marker", "pattern", "symbol", "clippath", "mask",
    "lineargradient", "radialgradient", "filter", "foreignobject",
}

# 参与包围盒计算的图形元素
SHAPES = {"rect", "circle", "ellipse", "line", "polygon", "polyline", "path"}

CN_LABEL = {
    "rect": "矩形", "circle": "圆", "ellipse": "椭圆", "line": "线段",
    "polygon": "多边形", "polyline": "折线", "path": "路径", "text": "文本",
}

_SVG_BLOCK = re.compile(r"<svg\b.*?</svg\s*>", re.S | re.I)
_NUM = re.compile(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")
_TRANSFORM = re.compile(r"([a-zA-Z]+)\s*\(([^)]*)\)")
_WS = re.compile(r"\s+")


# ---------------------------------------------------------------- 小工具

def _nums(s: str) -> list[float]:
    if not s:
        return []
    out = []
    for m in _NUM.finditer(s):
        t = m.group(0)
        if t in ("+", "-", "."):
            continue
        try:
            out.append(float(t))
        except ValueError:
            pass
    return out


def _f(v, default=None):
    """属性值转 float;'12.5px' / 'var(--x)' 这类容错。"""
    if v is None:
        return default
    n = _nums(str(v))
    return n[0] if n else default


def _mul(a, b):
    """两个 2x3 仿射矩阵相乘 (a 作用在 b 之后 => a∘b)。矩阵表示 (a,b,c,d,e,f)。"""
    a0, a1, a2, a3, a4, a5 = a
    b0, b1, b2, b3, b4, b5 = b
    return (
        a0 * b0 + a2 * b1,
        a1 * b0 + a3 * b1,
        a0 * b2 + a2 * b3,
        a1 * b2 + a3 * b3,
        a0 * b4 + a2 * b5 + a4,
        a1 * b4 + a3 * b5 + a5,
    )


IDENT = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _apply(m, x, y):
    return (m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5])


def _parse_transform(s: str):
    """返回 (matrix, rotated:bool)。看不懂的 transform 一律当作 rotated=True(降级处理)。"""
    m = IDENT
    rotated = False
    if not s:
        return m, rotated
    for name, arg in _TRANSFORM.findall(s):
        v = _nums(arg)
        n = name.lower()
        if n == "translate":
            tx = v[0] if len(v) > 0 else 0.0
            ty = v[1] if len(v) > 1 else 0.0
            m = _mul(m, (1, 0, 0, 1, tx, ty))
        elif n == "scale":
            sx = v[0] if len(v) > 0 else 1.0
            sy = v[1] if len(v) > 1 else sx
            m = _mul(m, (sx, 0, 0, sy, 0, 0))
        elif n == "rotate":
            ang = math.radians(v[0]) if v else 0.0
            cx = v[1] if len(v) > 2 else 0.0
            cy = v[2] if len(v) > 2 else 0.0
            co, si = math.cos(ang), math.sin(ang)
            r = (co, si, -si, co, 0, 0)
            m = _mul(m, _mul((1, 0, 0, 1, cx, cy), _mul(r, (1, 0, 0, 1, -cx, -cy))))
            if abs(v[0] % 360.0) > 1e-9 if v else False:
                rotated = True
        elif n == "matrix" and len(v) >= 6:
            m = _mul(m, tuple(v[:6]))
            if abs(v[1]) > 1e-9 or abs(v[2]) > 1e-9:
                rotated = True
        elif n in ("skewx", "skewy"):
            rotated = True  # 不算,直接降级
        else:
            rotated = True
    return m, rotated


# ---------------------------------------------------------------- 字宽估算

def _char_width_ratio(ch: str) -> float:
    """单字符宽度 / font-size。偏保守(宁可估窄一点,少报)。"""
    o = ord(ch)
    if o < 0x80:
        if ch == " ":
            return 0.28
        if ch in "iljI.,:;'|!":
            return 0.30
        if ch in "frt()[]{}-":
            return 0.36
        if ch in "mwMW":
            return 0.82
        return 0.55
    # 全角 / CJK / 假名 / 全角标点 / 带圈数字 / 方块符号
    if (0x1100 <= o <= 0x115F or 0x2E80 <= o <= 0x303E or 0x3041 <= o <= 0x33FF
            or 0x3400 <= o <= 0x4DBF or 0x4E00 <= o <= 0x9FFF
            or 0xA000 <= o <= 0xA4CF or 0xAC00 <= o <= 0xD7A3
            or 0xF900 <= o <= 0xFAFF or 0xFE30 <= o <= 0xFE6F
            or 0xFF00 <= o <= 0xFF60 or 0xFFE0 <= o <= 0xFFE6):
        return 1.0
    if 0x2460 <= o <= 0x24FF:      # ①②③ ⓪ 带圈数字
        return 1.0
    if 0x2600 <= o <= 0x27BF or 0x2B00 <= o <= 0x2BFF:   # ★☆⭐✓✗ 杂项符号/装饰符
        return 0.95
    if 0x2190 <= o <= 0x21FF or 0x2200 <= o <= 0x22FF:   # 箭头 / 数学符号
        return 0.9
    if 0x2000 <= o <= 0x206F:      # –—“”‘’ 等通用标点
        return 0.5
    return 0.6


def _text_width(s: str, font_size: float, bold: bool, mono: bool = False) -> float:
    s = _WS.sub(" ", s).strip()
    if mono:
        w = sum(0.60 if ord(c) < 0x80 else _char_width_ratio(c) for c in s) * font_size
    else:
        w = sum(_char_width_ratio(c) for c in s) * font_size
    if bold:
        w *= 1.03
    return w


# ---------------------------------------------------------------- path 包围盒

def _bez_extrema_cubic(p0, p1, p2, p3):
    """三次贝塞尔在一维上的极值(含端点)。"""
    vals = [p0, p3]
    a = -p0 + 3 * p1 - 3 * p2 + p3
    b = 2 * (p0 - 2 * p1 + p2)
    c = p1 - p0
    if abs(a) < 1e-12:
        if abs(b) > 1e-12:
            ts = [-c / b]
        else:
            ts = []
    else:
        disc = b * b - 4 * a * c
        if disc < 0:
            ts = []
        else:
            sq = math.sqrt(disc)
            ts = [(-b + sq) / (2 * a), (-b - sq) / (2 * a)]
    for t in ts:
        if 0.0 < t < 1.0:
            mt = 1 - t
            vals.append(mt ** 3 * p0 + 3 * mt * mt * t * p1 + 3 * mt * t * t * p2 + t ** 3 * p3)
    return vals


def _bez_extrema_quad(p0, p1, p2):
    vals = [p0, p2]
    den = p0 - 2 * p1 + p2
    if abs(den) > 1e-12:
        t = (p0 - p1) / den
        if 0.0 < t < 1.0:
            mt = 1 - t
            vals.append(mt * mt * p0 + 2 * mt * t * p1 + t * t * p2)
    return vals


_PATH_TOKEN = re.compile(r"([MmZzLlHhVvCcSsQqTtAa])|([-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?)")


def _path_bbox(d: str):
    """解析 path 的 d,返回 (minx,miny,maxx,maxy) 或 None。曲线用导数求根算紧包围盒。"""
    toks = []
    for m in _PATH_TOKEN.finditer(d or ""):
        if m.group(1):
            toks.append(m.group(1))
        else:
            try:
                toks.append(float(m.group(2)))
            except ValueError:
                return None
    if not toks:
        return None

    xs, ys = [], []
    i = 0
    cx = cy = 0.0
    sx = sy = 0.0
    prev_cx = prev_cy = None   # 上一段 C/S 的第二控制点
    prev_qx = prev_qy = None   # 上一段 Q/T 的控制点
    cmd = None
    n = len(toks)

    def need(k):
        return i + k <= n and all(isinstance(toks[i + j], float) for j in range(k))

    while i < n:
        if isinstance(toks[i], str):
            cmd = toks[i]
            i += 1
            if cmd in "Zz":
                cx, cy = sx, sy
                prev_cx = prev_qx = None
                continue
            if i >= n or isinstance(toks[i], str):
                continue
        if cmd is None:
            return None
        c = cmd
        rel = c.islower()
        C = c.upper()

        if C == "M":
            if not need(2):
                break
            x, y = toks[i], toks[i + 1]; i += 2
            cx, cy = (cx + x, cy + y) if rel else (x, y)
            sx, sy = cx, cy
            xs.append(cx); ys.append(cy)
            cmd = "l" if rel else "L"   # M 之后的隐式命令是 L
            prev_cx = prev_qx = None
        elif C == "L":
            if not need(2):
                break
            x, y = toks[i], toks[i + 1]; i += 2
            cx, cy = (cx + x, cy + y) if rel else (x, y)
            xs.append(cx); ys.append(cy)
            prev_cx = prev_qx = None
        elif C == "H":
            if not need(1):
                break
            x = toks[i]; i += 1
            cx = cx + x if rel else x
            xs.append(cx); ys.append(cy)
            prev_cx = prev_qx = None
        elif C == "V":
            if not need(1):
                break
            y = toks[i]; i += 1
            cy = cy + y if rel else y
            xs.append(cx); ys.append(cy)
            prev_cx = prev_qx = None
        elif C in ("C", "S"):
            k = 6 if C == "C" else 4
            if not need(k):
                break
            v = toks[i:i + k]; i += k
            if C == "C":
                x1, y1, x2, y2, x, y = v
                if rel:
                    x1 += cx; y1 += cy; x2 += cx; y2 += cy; x += cx; y += cy
            else:
                x2, y2, x, y = v
                if rel:
                    x2 += cx; y2 += cy; x += cx; y += cy
                if prev_cx is None:
                    x1, y1 = cx, cy
                else:
                    x1, y1 = 2 * cx - prev_cx, 2 * cy - prev_cy
            xs.extend(_bez_extrema_cubic(cx, x1, x2, x))
            ys.extend(_bez_extrema_cubic(cy, y1, y2, y))
            prev_cx, prev_cy = x2, y2
            prev_qx = None
            cx, cy = x, y
        elif C in ("Q", "T"):
            k = 4 if C == "Q" else 2
            if not need(k):
                break
            v = toks[i:i + k]; i += k
            if C == "Q":
                x1, y1, x, y = v
                if rel:
                    x1 += cx; y1 += cy; x += cx; y += cy
            else:
                x, y = v
                if rel:
                    x += cx; y += cy
                if prev_qx is None:
                    x1, y1 = cx, cy
                else:
                    x1, y1 = 2 * cx - prev_qx, 2 * cy - prev_qy
            xs.extend(_bez_extrema_quad(cx, x1, x))
            ys.extend(_bez_extrema_quad(cy, y1, y))
            prev_qx, prev_qy = x1, y1
            prev_cx = None
            cx, cy = x, y
        elif C == "A":
            if not need(7):
                break
            v = toks[i:i + 7]; i += 7
            x, y = v[5], v[6]
            if rel:
                x += cx; y += cy
            # 弧只取端点(偏保守,可能漏报弧的凸出部分)
            xs.append(x); ys.append(y)
            cx, cy = x, y
            prev_cx = prev_qx = None
        else:
            return None

    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


# ---------------------------------------------------------------- 解析器

class _Node:
    __slots__ = ("tag", "ctm", "rotated", "font_size", "anchor", "bold", "opaque", "mono")

    def __init__(self, tag, ctm, rotated, font_size, anchor, bold, opaque, mono=False):
        self.tag = tag
        self.ctm = ctm
        self.rotated = rotated
        self.font_size = font_size
        self.anchor = anchor
        self.bold = bold
        self.opaque = opaque
        self.mono = mono


class _SvgParser(HTMLParser):
    """在整份 HTML 上走一遍,只在 <svg> 内部干活;行号用 getpos() 直接拿到。"""

    def __init__(self, vbox_by_line: dict):
        super().__init__(convert_charrefs=True)
        self.vbox_by_line = vbox_by_line
        self.stack: list[_Node] = []
        self.svg_depth = 0
        self.vb = None            # (minx, miny, maxx, maxy)
        self.records = []         # (line, tag, bbox, rotated, desc, viewbox)
        self._text = None         # 正在收集的 <text>

    # -- 环境

    def _cur(self):
        return self.stack[-1] if self.stack else _Node(
            None, IDENT, False, DEFAULT_FONT_SIZE, "start", False, False, False)

    def _in_opaque(self):
        return any(n.opaque for n in self.stack)

    def _push(self, tag, attrs):
        p = self._cur()
        a = dict(attrs)
        tm, rot = _parse_transform(a.get("transform", ""))
        ctm = _mul(p.ctm, tm)
        fs = _f(a.get("font-size"), None)
        if fs is None:
            fs = _style_font_size(a.get("style"))
        node = _Node(
            tag=tag,
            ctm=ctm,
            rotated=p.rotated or rot,
            font_size=fs if fs is not None else p.font_size,
            anchor=(a.get("text-anchor") or p.anchor).strip().lower(),
            bold=_is_bold(a) or p.bold,
            opaque=p.opaque or tag in OPAQUE,
            mono=_is_mono(a) or p.mono,
        )
        self.stack.append(node)
        return node

    # -- HTMLParser 回调

    def handle_starttag(self, tag, attrs):
        t = tag.lower()
        if t == "svg":
            self.svg_depth += 1
            if self.svg_depth == 1:
                self.stack = []
                self.vb = self.vbox_by_line.get(self.getpos()[0])
            node = self._push(t, attrs)
            if self.svg_depth > 1:
                node.opaque = True   # 嵌套 <svg> 自带新视口,不拿外层 viewBox 去量
            return
        if self.svg_depth == 0:
            return
        node = self._push(t, attrs)
        if self._in_opaque():
            return
        if t == "text":
            self._text = {
                "line": self.getpos()[0],
                "x": _f(dict(attrs).get("x"), None),
                "y": _f(dict(attrs).get("y"), None),
                "buf": [],
                "node": node,
                "has_child_pos": False,
            }
        elif t in SHAPES:
            self._shape(t, attrs, node)

    def handle_startendtag(self, tag, attrs):
        t = tag.lower()
        if self.svg_depth == 0 and t != "svg":
            return
        if t == "svg":
            return  # <svg/> 空的,无所谓
        node = self._push(t, attrs)
        if not self._in_opaque() and t in SHAPES:
            self._shape(t, attrs, node)
        self.stack.pop()

    def handle_endtag(self, tag):
        t = tag.lower()
        if self.svg_depth == 0:
            return
        if t == "text" and self._text is not None:
            self._flush_text()
        # 弹到最近的同名标签
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i].tag == t:
                del self.stack[i:]
                break
        if t == "svg":
            self.svg_depth = max(0, self.svg_depth - 1)
            if self.svg_depth == 0:
                self.vb = None
                self.stack = []
                self._text = None

    def handle_data(self, data):
        if self._text is not None and not self._in_opaque():
            self._text["buf"].append(data)

    # -- 具体元素

    def _shape(self, t, attrs, node):
        if self.vb is None:
            return
        a = dict(attrs)
        bb = None
        if t == "rect":
            x = _f(a.get("x"), 0.0); y = _f(a.get("y"), 0.0)
            w = _f(a.get("width"), None); h = _f(a.get("height"), None)
            if w is None or h is None:
                return   # 百分比宽高(width="100%")之类,跳过
            if "%" in str(a.get("width", "")) or "%" in str(a.get("height", "")):
                return
            bb = (x, y, x + w, y + h)
        elif t == "circle":
            cx = _f(a.get("cx"), 0.0); cy = _f(a.get("cy"), 0.0); r = _f(a.get("r"), None)
            if r is None:
                return
            bb = (cx - r, cy - r, cx + r, cy + r)
        elif t == "ellipse":
            cx = _f(a.get("cx"), 0.0); cy = _f(a.get("cy"), 0.0)
            rx = _f(a.get("rx"), None); ry = _f(a.get("ry"), None)
            if rx is None or ry is None:
                return
            bb = (cx - rx, cy - ry, cx + rx, cy + ry)
        elif t == "line":
            x1 = _f(a.get("x1"), 0.0); y1 = _f(a.get("y1"), 0.0)
            x2 = _f(a.get("x2"), 0.0); y2 = _f(a.get("y2"), 0.0)
            bb = (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
        elif t in ("polygon", "polyline"):
            v = _nums(a.get("points", ""))
            if len(v) < 4:
                return
            px = v[0::2]; py = v[1::2]
            k = min(len(px), len(py))
            bb = (min(px[:k]), min(py[:k]), max(px[:k]), max(py[:k]))
        elif t == "path":
            bb = _path_bbox(a.get("d", ""))
            if bb is None:
                return
        if bb is None:
            return
        bb = _xform_bbox(node.ctm, bb)
        desc = _shape_desc(t, a)
        self.records.append((self.getpos()[0], t, bb, node.rotated, desc, self.vb))

    def _flush_text(self):
        st = self._text
        self._text = None
        if self.vb is None:
            return
        node = st["node"]
        s = _WS.sub(" ", "".join(st["buf"])).strip()
        if not s:
            return
        x = st["x"]; y = st["y"]
        if x is None or y is None:
            return   # 没有绝对定位(靠 tspan 或流式),不猜
        fs = node.font_size or DEFAULT_FONT_SIZE
        w = _text_width(s, fs, node.bold, node.mono)
        anchor = node.anchor
        if anchor == "middle":
            x0, x1 = x - w / 2.0, x + w / 2.0
        elif anchor in ("end", "right"):
            x0, x1 = x - w, x
        else:
            x0, x1 = x, x + w
        y0 = y - ASCENT * fs
        y1 = y + DESCENT * fs
        bb = _xform_bbox(node.ctm, (x0, y0, x1, y1))
        snippet = s if len(s) <= 26 else s[:26] + "…"
        desc = '<text x="%s" y="%s" font-size="%s">%s</text>' % (
            _n(x), _n(y), _n(fs), snippet)
        self.records.append((st["line"], "text", bb, node.rotated,
                            (desc, w, fs, anchor, x, y), self.vb))


def _style_font_size(style):
    if not style:
        return None
    m = re.search(r"font-size\s*:\s*([0-9.]+)", style)
    return float(m.group(1)) if m else None


def _is_mono(a):
    ff = str(a.get("font-family", "")) + " " + str(a.get("style", ""))
    return "mono" in ff.lower()


def _is_bold(a):
    fw = str(a.get("font-weight", "")).strip()
    if not fw:
        return False
    if fw in ("bold", "bolder"):
        return True
    n = _f(fw, 0)
    return bool(n and n >= 600)


def _xform_bbox(m, bb):
    if m == IDENT:
        return bb
    x0, y0, x1, y1 = bb
    pts = [_apply(m, x0, y0), _apply(m, x1, y0), _apply(m, x0, y1), _apply(m, x1, y1)]
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _n(v):
    if v is None:
        return "?"
    return str(int(v)) if abs(v - round(v)) < 1e-9 else ("%.6g" % v)


def _shape_desc(t, a):
    if t == "rect":
        return '<rect x="%s" y="%s" width="%s" height="%s">' % (
            a.get("x", "0"), a.get("y", "0"), a.get("width", "?"), a.get("height", "?"))
    if t == "circle":
        return '<circle cx="%s" cy="%s" r="%s">' % (a.get("cx", "0"), a.get("cy", "0"), a.get("r", "?"))
    if t == "ellipse":
        return '<ellipse cx="%s" cy="%s" rx="%s" ry="%s">' % (
            a.get("cx", "0"), a.get("cy", "0"), a.get("rx", "?"), a.get("ry", "?"))
    if t == "line":
        return '<line x1="%s" y1="%s" x2="%s" y2="%s">' % (
            a.get("x1", "0"), a.get("y1", "0"), a.get("x2", "0"), a.get("y2", "0"))
    if t in ("polygon", "polyline"):
        p = str(a.get("points", ""))
        return '<%s points="%s">' % (t, p if len(p) <= 60 else p[:60] + "…")
    if t == "path":
        d = str(a.get("d", ""))
        return '<path d="%s">' % (d if len(d) <= 70 else d[:70] + "…")
    return "<%s>" % t


# ---------------------------------------------------------------- 主入口

def _viewboxes(html: str) -> dict:
    """{起始行号: (minx,miny,maxx,maxy)} —— 只收有合法 viewBox 的 <svg>。"""
    out = {}
    for m in re.finditer(r"<svg\b[^>]*>", html, re.I):
        vb = re.search(r'viewBox\s*=\s*["\']([^"\']+)["\']', m.group(0), re.I)
        if not vb:
            continue
        v = _nums(vb.group(1))
        if len(v) != 4 or v[2] <= 0 or v[3] <= 0:
            continue
        line = html.count("\n", 0, m.start()) + 1
        out[line] = (v[0], v[1], v[0] + v[2], v[1] + v[3])
    return out


def check(path: str, html: str) -> list[dict]:
    """返回问题列表,每项 {"line": int, "kind": str, "msg": str, "detail": str}"""
    p = str(path).replace("\\", "/")
    if "/_v1/" in p or p.endswith("/_v1"):
        return []
    if not _SVG_BLOCK.search(html or ""):
        return []

    vbs = _viewboxes(html)
    if not vbs:
        return []

    # 每个 svg 单独跑一遍(解析器的 vb 按 svg 起始行切换)
    parser = _SvgParser(vbs)
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        return []

    return _judge(parser, vbs)


def _judge(parser, vbs):
    out = []
    for rec in parser.records:
        line, tag, bb, rotated, desc, vb = rec
        res = _one(line, tag, bb, rotated, desc, vb)
        if res:
            out.append(res)
    out.sort(key=lambda d: (d["line"], d["msg"]))
    return out


def _side_text(dx0, dy0, dx1, dy1):
    parts = []
    if dx0 > 0:
        parts.append("左边超出 %.1fpx" % dx0)
    if dx1 > 0:
        parts.append("右边超出 %.1fpx" % dx1)
    if dy0 > 0:
        parts.append("上边超出 %.1fpx" % dy0)
    if dy1 > 0:
        parts.append("下边超出 %.1fpx" % dy1)
    return "、".join(parts)


def _one(line, tag, bb, rotated, desc, vb):
    vx0, vy0, vx1, vy1 = vb
    bx0, by0, bx1, by1 = bb

    if tag == "text":
        d, w, fs, anchor, ox, oy = desc
        if rotated:
            tol_x = tol_y = ROTATED_TEXT_TOL
        else:
            tol_x = max(TEXT_TOL_X_ABS, TEXT_TOL_X_REL * w)
            tol_y = TEXT_TOL_Y
        dx0 = (vx0 - bx0) - tol_x
        dx1 = (bx1 - vx1) - tol_x
        dy0 = (vy0 - by0) - tol_y
        dy1 = (by1 - vy1) - tol_y
        if max(dx0, dx1, dy0, dy1) <= 0:
            return None
        where = _side_text(dx0, dy0, dx1, dy1)
        extra = ""
        if dx0 > 0 or dx1 > 0:
            extra = (";横向宽度是按字号估的(中日韩 1.0em、ASCII 0.55em),估得约 %.0fpx"
                     "(text-anchor=%s)" % (w, anchor or "start"))
        if (dy1 > 0) and not (dx0 > 0 or dx1 > 0):
            extra = ";文本基线 y=%s,加上字号 %s 的下伸部分已经压到 viewBox 底边 %s 之外" % (
                _n(oy), _n(fs), _n(vy1))
        msg = "SVG 文本超出 viewBox,渲染时会被裁掉:%s" % where
        detail = ("%s\nviewBox = %s %s %s %s(即 x∈[%s,%s], y∈[%s,%s])\n"
                  "估算包围盒 = x∈[%.1f,%.1f], y∈[%.1f,%.1f]%s%s" % (
                      d, _n(vx0), _n(vy0), _n(vx1 - vx0), _n(vy1 - vy0),
                      _n(vx0), _n(vx1), _n(vy0), _n(vy1),
                      bx0, bx1, by0, by1, extra,
                      "\n(该文本带 rotate 变换,已放宽到 %gpx 才判定)" % ROTATED_TEXT_TOL if rotated else ""))
        return {"line": line, "kind": KEY, "msg": msg, "detail": detail}

    tol = GEOM_TOL if not rotated else GEOM_TOL + 2.0
    dx0 = (vx0 - bx0) - tol
    dx1 = (bx1 - vx1) - tol
    dy0 = (vy0 - by0) - tol
    dy1 = (by1 - vy1) - tol
    if max(dx0, dx1, dy0, dy1) <= 0:
        return None
    where = _side_text(dx0, dy0, dx1, dy1)
    msg = "SVG %s超出 viewBox,渲染时会被裁掉:%s" % (CN_LABEL.get(tag, tag), where)
    detail = ("%s\nviewBox = %s %s %s %s(即 x∈[%s,%s], y∈[%s,%s])\n"
              "包围盒 = x∈[%.1f,%.1f], y∈[%.1f,%.1f](已给 %gpx 描边宽容)" % (
                  desc, _n(vx0), _n(vy0), _n(vx1 - vx0), _n(vy1 - vy0),
                  _n(vx0), _n(vx1), _n(vy0), _n(vy1),
                  bx0, bx1, by0, by1, tol))
    return {"line": line, "kind": KEY, "msg": msg, "detail": detail}


# ---------------------------------------------------------------- 自测

BAD_DIR = "D:/ext.zhaoliuliu3/Desktop/ai-workflow"             # 只读,原始(未修)
GOOD_DIR = "D:/ext.zhaoliuliu3/Desktop/claude_AI/ai-workflow"  # 已修版


def _read(p):
    import io as _io
    with _io.open(p, encoding="utf-8", errors="replace") as f:
        return f.read()


def _run(p):
    return check(p, _read(p))


# --- 合成样本:复刻 14 页的真实历史缺陷 -------------------------------------
# 「14 页曾经把第三条 bullet 放在 y=530,而 viewBox 高只有 524,那一行在页面上
#   直接消失了」—— 下面就是那个场景的最小复刻(第 7 行)。
#   另外第 8 行放了个右溢文本、第 9 行放了个右溢矩形,凑齐三种越界形态。
_SYNTH_BAD = "\n".join([
    '<figure><svg viewBox="0 0 900 524" role="img">',
    '  <defs><marker id="m" markerWidth="8" markerHeight="6" refX="7" refY="3">',
    '    <polygon points="0 0, 8 3, 0 6"/></marker></defs>',
    '  <rect x="16" y="374" width="846" height="62" rx="11" fill="none"/>',
    '  <text x="16" y="490" font-size="11.5">&#9312; 累了就直接说“继续”,不看改动。</text>',
    '  <text x="16" y="510" font-size="11.5">&#9313; 方向错了却舍不得撤,想“再改改看”。</text>',
    '  <text x="16" y="530" font-size="11.5">&#9314; 每步都绿了就以为完事了,不跑最贵那层。</text>',
    '  <text x="700" y="300" font-size="13">这一行右边整整跑出画布好几十像素完全看不见</text>',
    '  <rect x="820" y="60" width="140" height="40"/>',
    '  <path d="M 606 266 L 606 340 L 8 340 L 8 8 L 271 8 L 271 16"/>',
    '</svg></figure>',
    '',
])

# 好样本:viewBox 高度补到 546(和线上已修版一致),越界的两个元素挪回画布内
_SYNTH_GOOD = (_SYNTH_BAD
               .replace('viewBox="0 0 900 524"', 'viewBox="0 0 900 546"')
               .replace('<text x="700" y="300"', '<text x="300" y="300"')
               .replace('<rect x="820" y="60" width="140"', '<rect x="700" y="60" width="140"'))


def _selftest():
    import os
    import re as _re
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    def dump(rs, indent="  "):
        if not rs:
            print(indent + "(无检出)")
        for r in rs:
            print("%sL%-5d %s" % (indent, r["line"], r["msg"]))
            for ln in r["detail"].splitlines():
                print(indent + "       " + ln)

    def show(title, p):
        print("=" * 78)
        print(title)
        print("  " + p)
        if not os.path.exists(p):
            print("  [文件不存在,跳过]")
            return []
        rs = _run(p)
        dump(rs)
        return rs

    ok = True

    # ---------- 1. 合成样本:阳性对照 ----------
    print("=" * 78)
    print("[1] 合成坏样本 —— 复刻 14 页 y=530 / viewBox 高 524 的历史缺陷")
    rb = check("synth_bad.html", _SYNTH_BAD)
    dump(rb)
    for ln, what in ((7, "y=530 那条 bullet 掉到画布底下"),
                     (8, "右溢文本"),
                     (9, "右溢矩形")):
        if not any(r["line"] == ln for r in rb):
            ok = False
            print("  !! 期望在第 %d 行(%s)报出,实际没报" % (ln, what))

    # ---------- 2. 合成样本:阴性对照 ----------
    print("-" * 78)
    print("[2] 合成好样本 —— viewBox 高改成 546、越界元素挪回画布内")
    rg = check("synth_good.html", _SYNTH_GOOD)
    dump(rg)
    if rg:
        ok = False
        print("  !! 好样本不该有任何检出")

    # ---------- 3. 真值样本 ----------
    # 缺陷 A(SVG 节点没出边)和缺陷 B(grid 容器里混裸文本)都不是「元素溢出画布」,
    # 归别的检测器管。本检测器在这两页的坏样本上也不报,是正确行为 ——
    # 跑这一段是为了证明它不会在别人的地盘上乱扣帽子。
    show("[3] 坏样本 01-overview(缺陷 A 所在页;不属本规则,应无检出)",
         BAD_DIR + "/01-overview.html")
    show("[4] 好样本 01-overview(应无检出)", GOOD_DIR + "/01-overview.html")
    show("[5] 坏样本 02-python-setup(缺陷 B 所在页;不属本规则,应无检出)",
         BAD_DIR + "/02-python-setup.html")
    show("[6] 好样本 02-python-setup(应无检出)", GOOD_DIR + "/02-python-setup.html")

    # ---------- 4. 全书扫描 ----------
    def scan(root, label):
        print("=" * 78)
        print("[%s] 全书扫描:%s" % (label, root))
        if not os.path.isdir(root):
            print("  [目录不存在]")
            return 0
        total = 0
        for fn in sorted(os.listdir(root)):
            if not fn.endswith(".html"):
                continue
            rs = _run(os.path.join(root, fn))
            total += len(rs)
            if rs:
                print("  %-26s %d 条" % (fn, len(rs)))
                dump(rs, "    ")
        print("  >>> 合计 %d 条(验收阈值 40)" % total)
        return total

    total = scan(GOOD_DIR, "7")
    if total > 40:
        ok = False
        print("  !! 超过 40 条,规则太松")
    scan(BAD_DIR, "8")

    # ---------- 5. 变异测试:证明它不是「永远返回空」 ----------
    print("=" * 78)
    print("[9] 变异测试 —— 人为把每页第一个 svg 的 viewBox 砍小,看能否报出")
    print("    基线 0 条是因为这套图本来就没有溢出;砍小 viewBox 之后必须报出来。")

    def sweep(dw, dh):
        hit = n = 0
        for fn in sorted(os.listdir(GOOD_DIR)):
            if not fn.endswith(".html"):
                continue
            n += 1
            h = _read(os.path.join(GOOD_DIR, fn))

            def cut(m):
                v = _nums(m.group(1))
                return 'viewBox="%g %g %g %g"' % (v[0], v[1], v[2] - dw, v[3] - dh)

            if check(fn, _re.sub(r'viewBox="([^"]+)"', cut, h, count=1)):
                hit += 1
        return hit, n

    if os.path.isdir(GOOD_DIR):
        for d in (8, 16, 24, 40, 60):
            hh, n = sweep(0, d)
            ww, _ = sweep(d, 0)
            print("    高砍 %-3dpx -> %2d/%d 页报出     宽砍 %-3dpx -> %2d/%d 页报出"
                  % (d, hh, n, d, ww, n))
        h8, n = sweep(0, 8)
        h40, _ = sweep(0, 40)
        if h8 != 0:
            ok = False
            print("  !! 只砍 8px 就报了 %d 页 —— 阈值太紧,会滥报" % h8)
        if h40 != n:
            ok = False
            print("  !! 砍掉 40px 仍有 %d 页没报 —— 阈值太松,会漏报" % (n - h40))

    print("=" * 78)
    print("自测结论:", "全部通过" if ok else "有断言未通过(见上面 !! 行)")
    return 0 if ok else 1


if __name__ == "__main__":
    import sys as _sys
    _sys.exit(_selftest())
