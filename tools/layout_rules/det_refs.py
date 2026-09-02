# -*- coding: utf-8 -*-
"""det_refs —— SVG 引用完整性 与 图注一致性 检查器(key: refs)。

只用 Python 3.12 标准库(re / html / sys / pathlib)。

覆盖两类问题:

一、SVG 引用完整性(高置信,零猜测)
    1. svg-ref-missing   url(#x) / href="#x" 指向的 id 在本 <svg> 里没有定义
                         → 箭头、渐变、裁剪路径在浏览器里直接不渲染。
    2. svg-ref-cross-svg url(#x) 指向的 id 定义在同一页的**另一个** <svg> 里。
                         浏览器按文档顺序解析,今天能显示,但两图顺序一变或
                         其中一图被搬走就断,属于潜伏故障。
    3. svg-ref-type      引用能解析,但目标元素类型不对
                         (marker-end 指到 linearGradient、fill 指到 marker 等)
                         → 同样不渲染,而且比 missing 更难肉眼发现。
    4. svg-ref-malformed marker-start/mid/end 的取值既不是 url(#..) 也不是 none。
    5. svg-id-dup        同一个 <svg> 内出现重复 id → 后一个定义永远取不到。
    6. svg-def-unused    <defs> 里定义了却全页没被引用的 marker/渐变等死代码(提示级)。

二、图注一致性(必然模糊,所以卡得很死)
    7. fig-count         <figcaption> 开头声称的数量(「六幕证据链」「八道关卡」)
                         与图内可数的编号序列不符。只在证据非常硬时才报,详见
                         _caption_count 的注释。
    8. fig-no-caption    <figure> 里有 <svg> 却完全没有 <figcaption>。

设计原则:宁可漏报。第二类的判据要求「图注开头就是数量短语」且「图内存在
一条从 0 或 1 开始、长度 >= 3 的连续编号」,任何一条不满足就直接放弃,不猜。
"""

from __future__ import annotations

import html as _html
import re
import sys
from pathlib import Path

from _baseline import 基线目录, 已修目录  # noqa: E402

# --------------------------------------------------------------------------
# 基础正则
# --------------------------------------------------------------------------

SVG_RE = re.compile(r"<svg\b.*?</svg\s*>", re.S | re.I)
FIGURE_RE = re.compile(r"<figure\b.*?</figure\s*>", re.S | re.I)
FIGCAP_RE = re.compile(r"<figcaption\b[^>]*>(.*?)</figcaption\s*>", re.S | re.I)
DEFS_RE = re.compile(r"<defs\b.*?</defs\s*>", re.S | re.I)
TAG_RE = re.compile(r"<\s*([A-Za-z][\w:-]*)((?:\s+[^<>]*)?)/?>", re.S)
ATTR_RE = re.compile(r'([:A-Za-z][\w:.-]*)\s*=\s*"([^"]*)"', re.S)
URL_REF_RE = re.compile(r"url\(\s*['\"]?#([^)'\"\s]+)['\"]?\s*\)")
HASH_REF_RE = re.compile(r"^\s*#([^\s]+)\s*$")
STRIP_TAG_RE = re.compile(r"<[^>]+>")

# url(#x) 出现在哪个属性里 → 目标元素允许的标签集合
REF_TYPE_OK = {
    "marker-start": {"marker"},
    "marker-mid": {"marker"},
    "marker-end": {"marker"},
    "marker": {"marker"},
    "fill": {"lineargradient", "radialgradient", "pattern"},
    "stroke": {"lineargradient", "radialgradient", "pattern"},
    "stop-color": {"lineargradient", "radialgradient", "pattern"},
    "flood-color": {"lineargradient", "radialgradient", "pattern"},
    "clip-path": {"clippath"},
    "mask": {"mask"},
    "filter": {"filter"},
}

# defs 里这些标签是纯定义,定义了没人用就是死代码
DEF_LIKE = {
    "marker", "lineargradient", "radialgradient", "pattern",
    "clippath", "mask", "filter", "symbol",
}

MARKER_ATTRS = ("marker-start", "marker-mid", "marker-end")


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _strip_markup(s: str) -> str:
    return STRIP_TAG_RE.sub("", _html.unescape(s)).strip()


# --------------------------------------------------------------------------
# 一、SVG 引用完整性
# --------------------------------------------------------------------------

def _scan_svg(block: str, base: int, html: str):
    """扫一个 <svg> 块,返回 (id定义表, 引用列表)。

    id定义表: {id: [(tag, 绝对行号), ...]}   —— 用 list 才能查出重复
    引用列表: [(被引 id, 属性名, 绝对行号, 引用写法)]
    """
    ids: dict[str, list[tuple[str, int]]] = {}
    refs: list[tuple[str, str, int, str]] = []

    for m in TAG_RE.finditer(block):
        tag = m.group(1).lower()
        attrs = m.group(2) or ""
        line = base + block.count("\n", 0, m.start())
        for am in ATTR_RE.finditer(attrs):
            name, val = am.group(1), am.group(2)
            lname = name.lower()
            if lname == "id":
                ids.setdefault(val, []).append((tag, line))
                continue
            # style="fill:url(#g1)" —— 属性名要从 CSS 声明里取
            if lname == "style":
                for decl in val.split(";"):
                    prop, _, pv = decl.partition(":")
                    for um in URL_REF_RE.finditer(pv):
                        refs.append((um.group(1), prop.strip().lower(), line,
                                     f'style="{decl.strip()}"'))
                continue
            for um in URL_REF_RE.finditer(val):
                refs.append((um.group(1), lname, line, f'{name}="{val}"'))
            # <use href="#x"> / xlink:href="#x"
            if lname in ("href", "xlink:href"):
                hm = HASH_REF_RE.match(val)
                if hm:
                    refs.append((hm.group(1), lname, line, f'{name}="{val}"'))
    return ids, refs


def _check_svg_refs(path: str, html: str) -> list[dict]:
    out: list[dict] = []
    blocks = []
    for m in SVG_RE.finditer(html):
        blocks.append((m.start(), _line_of(html, m.start()), m.group(0)))

    # 先把全页所有 svg 的 id 收齐,才能区分「压根没有」和「在隔壁那张图里」
    page_ids: dict[str, list[tuple[int, str, int]]] = {}   # id -> [(svg序号, tag, line)]
    parsed = []
    for i, (_pos, base, blk) in enumerate(blocks):
        ids, refs = _scan_svg(blk, base, html)
        parsed.append((i, base, blk, ids, refs))
        for k, v in ids.items():
            for tag, line in v:
                page_ids.setdefault(k, []).append((i, tag, line))

    # 全页(含 <style> / 内联 style)出现过的引用,用于判断 defs 是不是死代码
    page_refs = set(URL_REF_RE.findall(html))
    for am in ATTR_RE.finditer(html):
        if am.group(1).lower() in ("href", "xlink:href"):
            hm = HASH_REF_RE.match(am.group(2))
            if hm:
                page_refs.add(hm.group(1))

    for i, base, blk, ids, refs in parsed:
        tag_id = f"第 {i + 1} 个 <svg>"

        # --- 5. 同一个 svg 内重复 id ---
        for k, v in ids.items():
            if len(v) > 1:
                lines = "、".join(str(ln) for _t, ln in v)
                out.append({
                    "line": v[1][1],
                    "kind": "svg-id-dup",
                    "msg": f'{tag_id} 内 id="{k}" 定义了 {len(v)} 次,后面的定义永远取不到',
                    "detail": (f'重复出现在第 {lines} 行(标签 '
                               f'{"、".join(t for t, _l in v)})。SVG 的 url(#{k}) 只会解析到'
                               f'文档顺序里的第一个,其余是死代码;若两处样式不同,'
                               f'会出现「改了没生效」。置信度:高。'),
                    "conf": "高",
                })

        # --- 1/2/3. 引用解析 ---
        for name, attr, line, raw in refs:
            if name in ids:
                # 类型是否对得上
                allow = REF_TYPE_OK.get(attr)
                if allow:
                    tgt = ids[name][0][0]
                    if tgt not in allow:
                        out.append({
                            "line": line,
                            "kind": "svg-ref-type",
                            "msg": (f'{tag_id} 里 {attr} 引用了 #{name},但 #{name} 是 '
                                    f'<{tgt}>,不是 {"/".join(sorted(allow))}'),
                            "detail": (f'原文:{raw}。属性 {attr} 只接受 '
                                       f'{"/".join(sorted(allow))} 类型的引用,类型不匹配时'
                                       f'浏览器直接忽略,元素会以「没设置该属性」的样子渲染 —— '
                                       f'箭头/渐变凭空消失。置信度:高。'),
                            "conf": "高",
                        })
                continue

            if name in page_ids:
                where = sorted({j for j, _t, _l in page_ids[name]})
                out.append({
                    "line": line,
                    "kind": "svg-ref-cross-svg",
                    "msg": (f'{tag_id} 里 {attr}="url(#{name})" 引用的 id 定义在同一页的'
                            f'第 {"、".join(str(j + 1) for j in where)} 个 <svg> 里,不在本图内'),
                    "detail": (f'原文:{raw}。HTML 里 url(#) 是全文档解析,所以现在能显示,'
                               f'但这是巧合:两张图调换顺序、或本图被复制到别的页面,'
                               f'引用立刻断掉。应在本 <svg> 的 <defs> 里自带一份定义。'
                               f'置信度:高(事实判断,严重度低)。'),
                    "conf": "高",
                })
                continue

            out.append({
                "line": line,
                "kind": "svg-ref-missing",
                "msg": f'{tag_id} 里 {attr}="url(#{name})" 指向的 id 全页都没有定义',
                "detail": (f'原文:{raw}。#{name} 在本 <svg> 的 <defs> 里没有,'
                           f'在同页其他 <svg> 里也没有。浏览器会把这条引用当作无效值丢弃:'
                           f'marker-* 失效 = 箭头不画,fill/stroke 失效 = 图形变透明或纯黑,'
                           f'clip-path 失效 = 裁剪不生效。肉眼很容易漏掉。置信度:高。'),
                "conf": "高",
            })

        # --- 4. marker-* 取值畸形 ---
        for m in TAG_RE.finditer(blk):
            line = base + blk.count("\n", 0, m.start())
            for am in ATTR_RE.finditer(m.group(2) or ""):
                lname = am.group(1).lower()
                val = am.group(2).strip()
                if lname in MARKER_ATTRS and val.lower() != "none" \
                        and not URL_REF_RE.search(val) and val != "":
                    out.append({
                        "line": line,
                        "kind": "svg-ref-malformed",
                        "msg": f'{tag_id} 里 {lname}="{val}" 不是合法取值',
                        "detail": ('marker-start/mid/end 只接受 url(#id) 或 none。'
                                   '写成裸 id、"#id" 或别的东西都会被静默忽略,箭头不画。'
                                   '置信度:高。'),
                        "conf": "高",
                    })

        # --- 6. defs 里定义了却全页没人引用 ---
        for dm in DEFS_RE.finditer(blk):
            dblk = dm.group(0)
            dbase = base + blk.count("\n", 0, dm.start())
            for tm in TAG_RE.finditer(dblk):
                tag = tm.group(1).lower()
                if tag not in DEF_LIKE:
                    continue
                attrs = dict((a.group(1).lower(), a.group(2))
                             for a in ATTR_RE.finditer(tm.group(2) or ""))
                did = attrs.get("id")
                if not did or did in page_refs:
                    continue
                out.append({
                    "line": dbase + dblk.count("\n", 0, tm.start()),
                    "kind": "svg-def-unused",
                    "msg": f'{tag_id} 的 <defs> 里定义了 <{tag} id="{did}">,但全页无人引用',
                    "detail": (f'没有任何 url(#{did}) 或 href="#{did}"。不影响渲染,'
                               f'但通常是改图时删掉了用它的那条线却忘了删定义 —— '
                               f'值得回头确认那条线是不是本该还在。置信度:高(事实),'
                               f'严重度:提示。'),
                    "conf": "高(事实判断,仅提示)",
                })

    return out


# --------------------------------------------------------------------------
# 二、图注一致性
# --------------------------------------------------------------------------

CN_NUM = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5,
          "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
# 量词白名单:只收「数 + 量 + 名」里那些真的在数图上节点的量词。
# 故意不收「句/次/秒/年/成/倍/分」这类度量单位。
UNITS = "个道层条件步幕种类块项张支问段轮关排组列行"

# 图注开头的数量短语,例:六幕证据链 / 八道关卡 / 三层拦截 / 七步流程 / 2 个圈
LEAD_QTY_RE = re.compile(
    r"^(?:(\d{1,2})|([一二两三四五六七八九十]{1,3}))\s*([" + UNITS + r"])"
)

# 圈码 ⓪ ①..⑳
CIRCLED = {"\u24ea": 0}
for _i in range(20):
    CIRCLED[chr(0x2460 + _i)] = _i + 1


def _cn_to_int(s: str) -> int | None:
    if s in CN_NUM:
        return CN_NUM[s]
    if len(s) == 2 and s[0] == "十":            # 十一 .. 十九
        return 10 + CN_NUM.get(s[1], 0)
    if len(s) == 2 and s[1] == "十":            # 二十 ..
        return CN_NUM.get(s[0], 0) * 10
    if len(s) == 3 and s[1] == "十":            # 二十一 ..
        return CN_NUM.get(s[0], 0) * 10 + CN_NUM.get(s[2], 0)
    return None


def _contiguous_run(nums: set[int]) -> int | None:
    """nums 必须是从 0 或 1 起步、不断档的一串,返回长度;否则 None。"""
    if not nums:
        return None
    lo = min(nums)
    if lo not in (0, 1):
        return None
    if nums != set(range(lo, lo + len(nums))):
        return None
    return len(nums)


def _svg_series(svg_text: str, unit: str) -> list[tuple[str, int]]:
    """从 svg 里找可数的编号序列。返回 [(证据名, 数量)],可能多条。"""
    found = []

    # (a) 显式的、单位对得上的序号:第 3 幕 / 步骤 4 / 第 2 层
    pats = [(f"第 N {unit}", re.compile(r"第\s*(\d{1,2})\s*" + re.escape(unit)))]
    if unit == "步":
        pats.append(("步骤 N", re.compile(r"步骤\s*(\d{1,2})")))
    for label, pat in pats:
        nums = {int(x) for x in pat.findall(svg_text)}
        n = _contiguous_run(nums)
        if n is not None and n >= 3:
            found.append((f"图内「{label}」编号 {min(nums)}–{max(nums)}", n))

    # (b) 圈码徽标 ⓪①②③…
    cnums = {CIRCLED[c] for c in svg_text if c in CIRCLED}
    n = _contiguous_run(cnums)
    if n is not None and n >= 3:
        lo, hi = min(cnums), max(cnums)
        glyphs = "".join(k for k, v in sorted(CIRCLED.items(), key=lambda kv: kv[1])
                         if lo <= v <= hi)
        found.append((f"图内圈码徽标 {glyphs}", n))

    return found


def _check_captions(path: str, html: str) -> list[dict]:
    out: list[dict] = []
    for idx, fm in enumerate(FIGURE_RE.finditer(html)):
        blk = fm.group(0)
        fig_line = _line_of(html, fm.start())
        sm = SVG_RE.search(blk)
        if not sm:
            continue                      # 图片/表格类 figure,不归本检查器管
        cm = FIGCAP_RE.search(blk)

        # --- 8. 有图无图注 ---
        if cm is None:
            label = ""
            am = re.search(r'aria-label\s*=\s*"([^"]*)"', sm.group(0))
            if am:
                label = _html.unescape(am.group(1))
            out.append({
                "line": fig_line,
                "kind": "fig-no-caption",
                "msg": f"第 {idx + 1} 个 <figure> 里有 <svg> 却没有 <figcaption>",
                "detail": (f'该图 aria-label="{label}"。全书其余每一张图都配了图注,'
                           f'只有这一张没有 —— 读者看完图没有一句话告诉他「该记住什么」,'
                           f'而且屏幕阅读器之外的读者拿不到任何文字概括。'
                           f'置信度:高(结构事实,不是猜测)。'),
                "conf": "高",
            })
            continue

        cap_line = fig_line + blk.count("\n", 0, cm.start())
        cap = _strip_markup(cm.group(1))
        cap = cap.lstrip("「『（(【[\"' \t")
        qm = LEAD_QTY_RE.match(cap)
        if not qm:
            continue                      # 图注开头不是数量短语 → 不猜
        n = int(qm.group(1)) if qm.group(1) else _cn_to_int(qm.group(2))
        unit = qm.group(3)
        if n is None or n < 3:
            continue                      # 「两个圈」这种太弱,证据不够,放弃

        svg_text = _html.unescape(sm.group(0))
        series = _svg_series(svg_text, unit)
        if not series:
            continue                      # 图里根本没有可数的编号 → 不猜

        # 只要有任意一条序列对得上图注,就认定一致(无罪推定,压误报)
        if any(k == n for _label, k in series):
            continue
        # 图注正文里别处还提到过这个数,也认定一致(例:「八道关卡。左边四道…」)
        other = {int(x) for x in re.findall(r"\d{1,2}", cap)}
        for ch in cap:
            if ch in CN_NUM:
                other.add(CN_NUM[ch])
        if any(k in other for _label, k in series):
            continue

        best = series[0]
        out.append({
            "line": cap_line,
            "kind": "fig-count",
            "msg": (f'图注开头声称「{qm.group(0)}」= {n},但{best[0]}只数出 {best[1]} 个'),
            "detail": (f'图注原文:{cap[:60]}…\n'
                       f'我数的是:{best[0]}(要求是从 0 或 1 起步、中间不断档、'
                       f'长度 >= 3 的一串编号),共 {best[1]} 个;图注开头写的是 {n}。\n'
                       f'两者不符时,要么图注写错,要么图里漏画/多画了一格。\n'
                       f'置信度:中 —— 我数的是「编号徽标」而不是「视觉上的方框」,'
                       f'若图里同时存在两套编号(比如节点用一套、结论列表又用一套),'
                       f'可能数错;请人工看一眼再定。'),
            "conf": "中",
        })
    return out


# --------------------------------------------------------------------------
# 对外接口
# --------------------------------------------------------------------------

def check(path: str, html: str) -> list[dict]:
    """返回问题列表,每项 {"line": int, "kind": str, "msg": str, "detail": str}"""
    out = _check_svg_refs(path, html)
    out += _check_captions(path, html)
    out.sort(key=lambda d: (d["line"], d["kind"]))
    return out


# --------------------------------------------------------------------------
# 自测
# --------------------------------------------------------------------------

_b = 基线目录()
BAD_DIR = Path(_b) if _b else None      # 只读!缺失时为 None
GOOD_DIR = Path(已修目录())


def _run_file(p: Path) -> list[dict]:
    return check(str(p), p.read_text(encoding="utf-8", errors="replace"))


def _report(title: str, p: Path) -> int:
    res = _run_file(p)
    print(f"\n--- {title}: {p} ---")
    if not res:
        print("    (无检出)")
    for r in res:
        print(f"    L{r['line']:<5} [{r['kind']}] {r['msg']}")
    return len(res)


# 合成夹具:证明规则真的会响,而不是永远沉默。
FIXTURE = """<!doctype html><html><body>
<figure>
<svg viewBox="0 0 100 100" role="img" aria-label="夹具一">
  <defs>
    <marker id="m1"><polygon points="0 0, 8 3, 0 6"/></marker>
    <marker id="m1"><polygon points="0 0, 8 3, 0 6"/></marker>
    <linearGradient id="g1"><stop offset="0"/></linearGradient>
    <marker id="dead"><polygon points="0 0, 8 3, 0 6"/></marker>
  </defs>
  <line x1="0" y1="0" x2="9" y2="9" marker-end="url(#m9)"/>
  <line x1="0" y1="0" x2="9" y2="9" marker-end="url(#g1)"/>
  <line x1="0" y1="0" x2="9" y2="9" marker-end="#m1"/>
  <rect fill="url(#m1)" x="0" y="0" width="9" height="9"/>
  <text>&#9312; 甲</text><text>&#9313; 乙</text><text>&#9314; 丙</text>
</svg>
<figcaption>五道防线,故意写错的图注</figcaption>
</figure>
<figure>
<svg viewBox="0 0 100 100" role="img" aria-label="夹具二 · 无图注"><rect/></svg>
</figure>
</body></html>"""


def _mutation_tests() -> int:
    坏 = 0
    """在**真实页面**上做内存内变异,证明规则不是只对玩具夹具有效。
    只读原文件,绝不写回。"""
    src = (GOOD_DIR / "01-overview.html").read_text(encoding="utf-8", errors="replace")

    cases = [
        ("把 defs 里的 id=\"a2g\" 改名 → 引用悬空",
         src.replace('<marker id="a2g"', '<marker id="a2gRENAMED"', 1),
         "svg-ref-missing"),
        ("把第二张图的 url(#a2) 改成引用第一张图的 #a1 → 跨图引用",
         src.replace('marker-end="url(#a2)" fill="none"',
                     'marker-end="url(#a1)" fill="none"', 1),
         "svg-ref-cross-svg"),
        ("把 marker-end 引用改指向渐变(先在 defs 里补一个渐变)→ 类型不匹配",
         src.replace('<marker id="a2g"',
                     '<linearGradient id="a2g"><stop offset="0"/></linearGradient>'
                     '<marker id="a2gX"', 1),
         "svg-ref-type"),
    ]
    for title, mutated, want in cases:
        hits = [r for r in check("<mutated 01-overview>", mutated) if r["kind"] == want]
        坏 += 0 if hits else 1
        flag = "命中" if hits else "!! 没报出来 !!"
        print(f"  [{flag}] {title}")
        for r in hits[:2]:
            print(f"           L{r['line']} [{r['kind']}] {r['msg']}")

    cap_src = (GOOD_DIR / "19-gate-demo.html").read_text(encoding="utf-8", errors="replace")
    mutated = cap_src.replace("<figcaption>六幕证据链", "<figcaption>八幕证据链", 1)
    hits = [r for r in check("<mutated 19>", mutated) if r["kind"] == "fig-count"]
    坏 += 0 if hits else 1
    print(f"  [{'命中' if hits else '!! 没报出来 !!'}] 把图注「六幕证据链」改成「八幕证据链」")
    for r in hits:
        print(f"           L{r['line']} [{r['kind']}] {r['msg']}")
    # 反向:原文不该报
    back = [r for r in check("<orig 19>", cap_src) if r["kind"] == "fig-count"]
    坏 += 0 if not back else 1
    print(f"  [{'正确沉默' if not back else '!! 误报 !!'}] 原版 19-gate-demo 图注「六幕」")
    return 坏


def _selftest() -> int:
    坏 = 0
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    print("=" * 74)
    print("0) 合成夹具 —— 验证每条规则都会响")
    print("=" * 74)
    _fx = check("<fixture>", FIXTURE)
    for r in _fx:
        print(f"    L{r['line']:<4} [{r['kind']}] {r['msg']}")
    # 夹具里埋了重复 id、悬空引用、类型不匹配、缺图注 —— 一条都不报,
    # 说明 check() 已经废了(哪怕它「零问题」跑得飞快)。
    坏 += 0 if len(_fx) >= 4 else 1
    print(f"    合成夹具检出 {len(_fx)} 条(至少应有 4 条)"
          f"{'' if len(_fx) >= 4 else '  <== 失败'}")

    print()
    print("=" * 74)
    print("0b) 真实页面变异测试 —— 证明规则在真markup上也能响")
    print("=" * 74)
    坏 += _mutation_tests()

    print()
    print("=" * 74)
    print("1) 真值样本对照(缺陷 A:01-overview 七步图缺回程边 / 缺陷 B:02 的 grid 图注)")
    print("=" * 74)
    if BAD_DIR is None:
        print("  (无只读基线,真值对照段跳过 —— 不算通过)")
        坏 += 1
    else:
        for name in ("01-overview.html", "02-python-setup.html"):
            _report(f"坏样本 {name}", BAD_DIR / name)
            _report(f"好样本 {name}", GOOD_DIR / name)

    print()
    print("=" * 74)
    print("2) 已修版全书 23 页")
    print("=" * 74)
    total = 0
    pages = sorted(p for p in GOOD_DIR.glob("*.html") if p.name[0].isdigit())
    for p in pages:
        res = _run_file(p)
        total += len(res)
        if res:
            print(f"\n  {p.name}")
            for r in res:
                print(f"    L{r['line']:<5} [{r['kind']}] {r['msg']}")
                for ln in r["detail"].splitlines():
                    print(f"           {ln}")
    print(f"\n  页数 = {len(pages)},总检出 = {total}"
          f"{'  (超过 40,规则太松!)' if total > 40 else '  (在 40 以内)'}")

    print()
    print("=" * 74)
    print("3) 旧版全书(只读对照)")
    print("=" * 74)
    tot2 = 0
    old = sorted(p for p in BAD_DIR.glob("*.html") if p.name[0].isdigit()) if BAD_DIR else []
    for p in old:
        res = _run_file(p)
        tot2 += len(res)
        if res:
            print(f"\n  {p.name}")
            for r in res:
                print(f"    L{r['line']:<5} [{r['kind']}] {r['msg']}")
    print(f"\n  页数 = {len(old)},总检出 = {tot2}")
    return 1 if 坏 else 0


if __name__ == "__main__":
    sys.exit(_selftest())
