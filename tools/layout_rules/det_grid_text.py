# -*- coding: utf-8 -*-
"""det_grid_text —— 检测「grid/flex 容器里混有裸文本节点」。

问题原理
--------
`display:grid` / `display:flex` 会把容器的**每一个直接子节点**变成
网格项 / 弹性项，包括那些没有被任何标签包住的匿名文本节点。于是

    .chk li{display:grid;grid-template-columns:26px 1fr}
    <li><code>.venv/bin/python --version</code> 能打印出版本号</li>

这个 <li> 实际有 3 个格子项：::before、<code>、以及裸文本
「 能打印出版本号」。第 3 个被塞进第 1 列（26px 宽），渲染成一字一行。

检测思路
--------
1. 从 <style> 里扒出所有 display:grid / inline-grid / flex / inline-flex 的规则；
2. 只支持简单选择器（.cls / tag / tag.cls / #id，以及后代 " " 和子代 ">" 组合），
   带伪类、伪元素、属性选择器、* 、+ 、~ 的一律跳过（宁可漏报）；
3. 用 html.parser 建栈式 DOM 遍历，找出匹配这些选择器的元素；
4. 若该元素的**直接子节点**里既有元素子节点、又有非空白裸文本 → 报出来。

只用 Python 3.12 标准库。
"""

from __future__ import annotations

import re
import sys
from html.parser import HTMLParser

KIND = "grid_text"

# ---------------------------------------------------------------- 常量

VOID = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}

# 这些标签在手写 HTML 里经常不写闭合标签，遇到同名开标签要隐式收尾
AUTO_CLOSE_SAME = {"li", "dt", "dd", "p", "tr", "td", "th", "option", "figcaption"}
# <li> 出现时应关掉未闭合的 <p>；<tr> 出现时应关掉 <td>/<th> …
AUTO_CLOSE_BY = {
    "li": {"p"},
    "tr": {"td", "th", "p"},
    "td": {"td", "th", "p"},
    "th": {"td", "th", "p"},
    "ul": {"p", "li"},
    "ol": {"p", "li"},
    "table": {"p"},
    "div": {"p"},
    "h1": {"p"}, "h2": {"p"}, "h3": {"p"}, "h4": {"p"},
    "pre": {"p"}, "figure": {"p"}, "blockquote": {"p"},
}

DISPLAY_RE = re.compile(r"(?:^|;)\s*display\s*:\s*([a-zA-Z-]+)", re.I)
COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)
STYLE_RE = re.compile(r"<style\b[^>]*>(.*?)</style>", re.S | re.I)

# 允许的简单复合选择器：可选 tag，后跟任意多个 .cls / #id
COMPOUND_RE = re.compile(r"^(?:([A-Za-z][\w-]*)|\*?)((?:[.#][\w-]+)*)$")
# 出现这些字符就放弃该选择器（保守）
UNSUPPORTED_RE = re.compile(r"[:\[\]()+~@%]|\*")

FLEXY = {"flex": "flex", "inline-flex": "flex", "grid": "grid", "inline-grid": "grid"}


# ---------------------------------------------------------------- CSS 侧

class Rule:
    __slots__ = ("selector", "sels", "mode", "raw", "line", "media", "tracks", "inline")

    def __init__(self, selector, sels, mode, raw, line, media, tracks, inline):
        self.selector = selector      # 原始选择器文本
        self.sels = sels              # 编译后的复合选择器链
        self.mode = mode              # "grid" / "flex"
        self.raw = raw                # 声明块原文（压缩后）
        self.line = line              # 该规则在文件里的行号
        self.media = media            # 所处 @media 条件（None 表示无条件生效）
        self.tracks = tracks          # grid-template-columns 的值（可能为 None）
        self.inline = inline          # 是否 inline-grid / inline-flex


def _compile_selector(sel: str):
    """把 '.cls tag' 之类编译成 [(comb, tag, classes, id), ...]；不支持就返回 None。"""
    sel = sel.strip()
    if not sel or UNSUPPORTED_RE.search(sel):
        return None
    # 归一化子代组合符
    sel = re.sub(r"\s*>\s*", " > ", sel)
    parts = sel.split()
    chain = []
    comb = " "          # 与上一段的关系：" " 后代，">" 子代
    for part in parts:
        if part == ">":
            comb = ">"
            continue
        m = COMPOUND_RE.match(part)
        if not m:
            return None
        tag = (m.group(1) or "").lower() or None
        classes, eid = set(), None
        for tok in re.findall(r"[.#][\w-]+", m.group(2) or ""):
            if tok[0] == ".":
                classes.add(tok[1:])
            else:
                eid = tok[1:]
        chain.append((comb, tag, classes, eid))
        comb = " "
    return chain or None


def _iter_css_rules(css: str, base_line: int):
    """极简 CSS 扫描：产出 (selector_text, decl_text, line, media_condition)。

    支持一层 @media / @supports 嵌套；@keyframes 整块跳过。
    """
    css_nc = COMMENT_RE.sub(lambda m: "\n" * m.group(0).count("\n"), css)
    i, n = 0, len(css_nc)
    line = base_line
    media_stack: list[str] = []
    buf_start = 0
    buf = []

    def cur_line(pos):
        return base_line + css_nc.count("\n", 0, pos)

    depth_skip = 0
    while i < n:
        ch = css_nc[i]
        if ch == "{":
            head = "".join(buf).strip()
            buf = []
            if depth_skip:
                depth_skip += 1
                i += 1
                continue
            if head.startswith("@"):
                at = head.split(None, 1)[0].lower()
                if at in ("@media", "@supports"):
                    media_stack.append(head)
                    i += 1
                    continue
                # @keyframes / @font-face 等：整块跳过
                depth_skip = 1
                i += 1
                continue
            # 普通规则：找到配对的 }
            j = css_nc.find("}", i)
            if j < 0:
                break
            decl = css_nc[i + 1:j]
            yield head, decl, cur_line(buf_start), (media_stack[-1] if media_stack else None)
            i = j + 1
            buf_start = i
            continue
        if ch == "}":
            if depth_skip:
                depth_skip -= 1
            elif media_stack:
                media_stack.pop()
            buf = []
            i += 1
            buf_start = i
            continue
        if not buf:
            if ch.isspace():          # 别让选择器以空白开头，否则行号会偏到上一行
                i += 1
                continue
            buf_start = i
        buf.append(ch)
        i += 1


def extract_rules(html: str) -> tuple[list[Rule], list[str]]:
    """返回 (含 grid/flex 的规则列表, 跳过的选择器列表)。"""
    rules: list[Rule] = []
    skipped: list[str] = []
    for m in STYLE_RE.finditer(html):
        base_line = html.count("\n", 0, m.start(1)) + 1
        for head, decl, line, media in _iter_css_rules(m.group(1), base_line):
            dm = None
            for d in DISPLAY_RE.finditer(";" + decl):
                dm = d.group(1).lower()          # 取最后一次声明
            if dm not in FLEXY:
                continue
            tracks = None
            tm = re.search(r"grid-template-columns\s*:\s*([^;}]+)", decl, re.I)
            if tm:
                tracks = tm.group(1).strip()
            for one in head.split(","):
                one = one.strip()
                if not one:
                    continue
                chain = _compile_selector(one)
                if chain is None:
                    skipped.append(one)
                    continue
                rules.append(Rule(
                    selector=one,
                    sels=chain,
                    mode=FLEXY[dm],
                    raw=re.sub(r"\s+", " ", decl).strip(),
                    line=line,
                    media=media,
                    tracks=tracks,
                    inline=dm.startswith("inline-"),
                ))
    return rules, skipped


# ---------------------------------------------------------------- DOM 侧

class Frame:
    __slots__ = ("tag", "classes", "eid", "line", "n_elem", "texts", "in_svg",
                 "kids", "has_text_deep", "n_kid_with_text")

    def __init__(self, tag, classes, eid, line, in_svg):
        self.tag = tag
        self.classes = classes
        self.eid = eid
        self.line = line
        self.in_svg = in_svg
        self.n_elem = 0
        self.texts: list[tuple[int, str]] = []   # (行号, 文本)
        self.kids: list[str] = []                # 直接元素子节点的标签名
        # 该元素子树里是否有非空白文本（用来区分「装饰性空元素」和「行文里的内联元素」）
        self.has_text_deep = False
        # 有几个直接元素子节点自身带文字
        self.n_kid_with_text = 0


def _match_chain(chain, stack_desc, idx):
    """stack_desc: [(tag, classes, eid), ...]；idx 指向当前候选元素。"""
    def compound_ok(ci, si):
        _, tag, classes, eid = chain[ci]
        t, cs, i_ = stack_desc[si]
        if tag and tag != t:
            return False
        if classes and not classes <= cs:
            return False
        if eid and eid != i_:
            return False
        return True

    def rec(ci, si):
        if not compound_ok(ci, si):
            return False
        if ci == 0:
            return True
        comb = chain[ci][0]
        if comb == ">":
            return si > 0 and rec(ci - 1, si - 1)
        for k in range(si - 1, -1, -1):
            if rec(ci - 1, k):
                return True
        return False

    return rec(len(chain) - 1, idx)


class _Walker(HTMLParser):
    def __init__(self, rules):
        super().__init__(convert_charrefs=True)
        self.rules = rules
        self.stack: list[Frame] = []
        self.hits: list[tuple[Frame, Rule]] = []
        self._raw_mode = 0     # 在 <script>/<style>/<pre> 里

    # --- 工具
    def _desc(self):
        return [(f.tag, f.classes, f.eid) for f in self.stack]

    def _close_implied(self, tag):
        want = set(AUTO_CLOSE_BY.get(tag, ()))
        if tag in AUTO_CLOSE_SAME:
            want.add(tag)
        while self.stack and self.stack[-1].tag in want:
            self._pop()

    def _pop(self):
        if not self.stack:
            return
        f = self.stack.pop()
        own_text = any(t.strip(_WS) for _, t in f.texts)
        if own_text:
            f.has_text_deep = True
        # 向父节点回传：本元素自身有没有文字（决定它是不是「装饰性空元素」）
        if self.stack:
            p = self.stack[-1]
            if f.has_text_deep:
                p.has_text_deep = True
                p.n_kid_with_text += 1
        if f.in_svg or f.tag in ("script", "style"):
            return
        if f.n_elem and own_text:
            desc = self._desc() + [(f.tag, f.classes, f.eid)]
            for r in self.rules:
                if _match_chain(r.sels, desc, len(desc) - 1):
                    self.hits.append((f, r))
                    break     # 一个元素只报一次

    # --- HTMLParser 回调
    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in ("script", "style"):
            self._raw_mode += 1
        self._close_implied(tag)
        d = dict(attrs)
        classes = set((d.get("class") or "").split())
        eid = d.get("id")
        in_svg = bool(self.stack and self.stack[-1].in_svg) or tag == "svg"
        if self.stack:
            self.stack[-1].n_elem += 1
            self.stack[-1].kids.append(tag)
        if tag in VOID:
            return
        self.stack.append(Frame(tag, classes, eid, self.getpos()[0], in_svg))

    def handle_startendtag(self, tag, attrs):
        tag = tag.lower()
        if self.stack:
            self.stack[-1].n_elem += 1
            self.stack[-1].kids.append(tag)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in ("script", "style"):
            self._raw_mode = max(0, self._raw_mode - 1)
        if tag in VOID:
            return
        for k in range(len(self.stack) - 1, -1, -1):
            if self.stack[k].tag == tag:
                while len(self.stack) > k:
                    self._pop()
                return
        # 没有匹配的开标签：忽略

    def handle_data(self, data):
        if self._raw_mode or not self.stack:
            return
        self.stack[-1].texts.append((self.getpos()[0], data))

    def close(self):
        super().close()
        while self.stack:
            self._pop()


# ---------------------------------------------------------------- 报告

_WS = " \t\r\n\u3000"


def _snippet(s: str, n: int = 46) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def check(path: str, html: str) -> list[dict]:
    """返回问题列表，每项 {"line": int, "kind": str, "msg": str, "detail": str}"""
    rules, _skipped = extract_rules(html)
    if not rules:
        return []
    w = _Walker(rules)
    try:
        w.feed(html)
        w.close()
    except Exception as e:                                    # noqa: BLE001
        return [{
            "line": 0, "kind": KIND,
            "msg": "HTML 解析失败，本项检查未执行",
            "detail": f"{type(e).__name__}: {e}",
        }]

    out = []
    seen = set()
    for f, r in w.hits:
        bare = [(ln, t) for ln, t in f.texts if t.strip(_WS)]
        if not bare:
            continue

        # —— 收紧规则，宁可漏报 ——
        #
        # 1) inline-flex：本来就是「让一小撮内联东西排成一行」的用法，
        #    裸文本参与排版正是作者要的，跳过。
        if r.inline:
            continue
        #
        # 2) 装饰性空元素 + 一段标签文字，是完全正常的 flex 用法。典型：
        #       .term-bar{display:flex;align-items:center;gap:7px}
        #       <div class="term-bar"><span class="dot"></span>×3 &nbsp; 真实输出</div>
        #    三个 .dot 是纯装饰的空 span，那段标签文字自己占一个弹性项，
        #    正好被 gap/align-items 摆在圆点右边——这是特性不是缺陷。
        #    真正的缺陷长成另一副样子：**行文**被内联元素（<code>/<b>/<a>…）
        #    切开，前后半句各自变成匿名项。判据就是：至少要有一个直接元素
        #    子节点自身带文字，说明这里在排「一句话」，而不是「图标 + 标签」。
        if f.n_kid_with_text == 0:
            continue
        #
        # 3) flex 方向未知时，一两个字的裸文本（分隔符 · / → 之类）不算缺陷。
        if r.mode == "flex":
            if sum(len(t.strip(_WS)) for _, t in bare) <= 2:
                continue

        key = (f.line, r.selector)
        if key in seen:
            continue
        seen.add(key)

        who = "<" + f.tag
        if f.eid:
            who += f' id="{f.eid}"'
        if f.classes:
            who += ' class="' + " ".join(sorted(f.classes)) + '"'
        who += ">"

        first_line, first_text = bare[0]
        n_bare = len(bare)

        if r.mode == "grid":
            if r.tracks:
                why = (f"该容器 grid-template-columns 是 {r.tracks!r}，"
                       f"裸文本会被当成独立网格项塞进下一个列轨，撑不开就一字一行")
            else:
                why = "裸文本会各自占一个网格项，被迫单独成行"
            fix = ("把整句包进一个 <span>/<div> 只留一个网格项；"
                   "若网格只是为了给 ::before 腾位置，改用 "
                   "position:relative + padding-left + 绝对定位的 ::before")
        else:
            why = ("裸文本会各自变成一个匿名弹性项，句子被 gap 从中间撑开、"
                   "首尾空白被吃掉，无法与相邻内联元素连成一行")
            fix = "把被切开的整句包进一个 <span>，让它只占一个弹性项"

        media = f"（仅在 {r.media} 内生效）" if r.media else ""
        msg = (f"{who} 同时含元素子节点和 {n_bare} 段裸文本，"
               f"但 CSS 把它设成了 display:{r.mode}")
        detail = (
            f"选择器 {r.selector}（CSS 第 {r.line} 行{media}）："
            f"{{{_snippet(r.raw, 90)}}}；"
            f"元素直接子节点中有 {f.n_elem} 个元素 + {n_bare} 段非空白裸文本，"
            f"首段在第 {first_line} 行：「{_snippet(first_text)}」。"
            f"{why}。修法：{fix}。"
        )
        out.append({"line": f.line, "kind": KIND, "msg": msg, "detail": detail})

    out.sort(key=lambda d: d["line"])
    return out


# ---------------------------------------------------------------- 自测

_BAD_DIR = r"D:/ext.zhaoliuliu3/Desktop/ai-workflow"
_GOOD_DIR = r"D:/ext.zhaoliuliu3/Desktop/claude_AI/ai-workflow"


def _run(path):
    import io
    with io.open(path, "r", encoding="utf-8", errors="replace") as fh:
        return check(path, fh.read())


def _show(title, path):
    print("=" * 78)
    print(f"{title}: {path}")
    res = _run(path)
    if not res:
        print("  （无检出）")
    for r in res:
        print(f"  L{r['line']:>5}  [{r['kind']}] {r['msg']}")
        print(f"          {r['detail']}")
    print(f"  小计 {len(res)} 条")
    return res


_CASES = [
    # (应否报出, 说明, HTML)
    (True, "grid + 定轨 + 行文被 <code> 切开（缺陷 B 的最小复现）",
     '<style>.chk li{display:grid;grid-template-columns:26px 1fr;gap:8px}</style>'
     '<ul class="chk"><li><code>x --version</code> 能打印出版本号</li></ul>'),
    (False, "grid 只有元素子节点，文本全是空白（.step 的正确用法）",
     '<style>.step{display:grid;grid-template-columns:72px 1fr}</style>'
     '<div class="step">\n  <div class="n">&#9312;</div>\n  <div class="c"><p>正文</p></div>\n</div>'),
    (False, "flex 只有裸文本、没有元素子节点",
     '<style>.b{display:flex}</style><div class="b">只有一句话，没有标签</div>'),
    (False, "flex + 装饰性空元素 + 标签文字（.term-bar 的正确用法）",
     '<style>.term-bar{display:flex;align-items:center;gap:7px}</style>'
     '<div class="term-bar"><span class="dot"></span><span class="dot"></span>&nbsp; 真实输出</div>'),
    (True, "flex + 行文被内联元素切开（gap 会插进句子中间）",
     '<style>.term-bar{display:flex;gap:7px}</style>'
     '<div class="term-bar"><span class="dot"></span> 真实输出(<code>-rs</code> 会打印原因)</div>'),
    (False, "inline-flex：让一小撮内联东西排一行，是有意为之",
     '<style>.tag{display:inline-flex;gap:4px}</style>'
     '<span class="tag"><b>标签</b> 说明文字</span>'),
    (False, "非 grid/flex 容器，混排是正常的",
     '<style>.p{display:block}</style><p class="p">看 <code>x</code> 这里</p>'),
    (False, "grid 声明在别的选择器上，本元素并不匹配",
     '<style>.cards{display:grid}</style><div class="box">看 <code>x</code> 这里</div>'),
    (False, "带伪类的选择器一律跳过（保守）",
     '<style>.z:hover{display:grid;grid-template-columns:26px 1fr}</style>'
     '<div class="z">看 <code>x</code> 这里</div>'),
    (True, "后代选择器 .a li 也要能匹配上",
     '<style>.a li{display:grid;grid-template-columns:20px 1fr}</style>'
     '<ul class="a"><li>前 <code>c</code> 后</li></ul>'),
    (False, "SVG 内部不参与判定（<text> 里全是裸文字）",
     '<style>text{display:flex}</style>'
     '<svg><text>甲 <tspan>乙</tspan> 丙</text></svg>'),
]


def _case_test():
    print("=" * 78)
    print("合成用例（正/负对照）：")
    bad_n = 0
    for want, note, doc in _CASES:
        got = bool(check("<mem>", doc))
        ok = (got == want)
        bad_n += (not ok)
        print(f"  [{'PASS' if ok else 'FAIL'}] 期望{'报' if want else '不报'} / 实际{'报' if got else '不报'}  {note}")
    print(f"  合成用例 {len(_CASES)} 条，失败 {bad_n} 条")
    return bad_n == 0


def _selftest():
    import os
    _case_test()
    bad = _show("坏样本", os.path.join(_BAD_DIR, "02-python-setup.html"))
    good = _show("好样本（已修）", os.path.join(_GOOD_DIR, "02-python-setup.html"))

    ok = any(".chk" in r["detail"] for r in bad) and not any(".chk" in r["detail"] for r in good)
    print("=" * 78)
    print(f"真值判定：坏样本报出 .chk li 且好样本不报 -> {'PASS' if ok else 'FAIL'}")

    print("=" * 78)
    print(f"全书扫描（{_GOOD_DIR}，跳过 _v1/）：")
    total = 0
    for name in sorted(os.listdir(_GOOD_DIR)):
        if not name.endswith(".html"):
            continue
        p = os.path.join(_GOOD_DIR, name)
        if not os.path.isfile(p):
            continue
        res = _run(p)
        total += len(res)
        flag = "" if not res else "  <-- "
        print(f"  {name:<28} {len(res)}{flag}")
        for r in res:
            print(f"      L{r['line']}  {r['msg']}")
    print(f"  全书总计 {total} 条（阈值 40）-> {'OK' if total <= 40 else 'TOO LOOSE'}")

    print("=" * 78)
    print(f"对照：只读旧目录 {_BAD_DIR} 全书扫描")
    total_old = 0
    for name in sorted(os.listdir(_BAD_DIR)):
        if not name.endswith(".html"):
            continue
        p = os.path.join(_BAD_DIR, name)
        if not os.path.isfile(p):
            continue
        res = _run(p)
        total_old += len(res)
        if res:
            print(f"  {name:<28} {len(res)}")
    print(f"  旧目录总计 {total_old} 条")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    _selftest()
