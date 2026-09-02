# -*- coding: utf-8 -*-
"""把地图接进教程:23 页各加一行 script、kicker 与真相表对齐、重建 00-index 的静态依赖图。

数据全部来自 tools/build_depmap.py(唯一事实源),本脚本不含任何依赖关系数据。
幂等:已处理过的文件会跳过,可以反复跑。

用法:  python tools/patch_pages.py [--dry-run]
"""


def main():
    import io
    import os
    import re
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    HERE = os.path.dirname(os.path.abspath(__file__))
    ROOT = os.path.dirname(HERE)
    sys.path.insert(0, HERE)
    from build_depmap import NODES, backbone  # noqa: E402

    DIR = os.path.join(ROOT, "ai-workflow")
    DRY = "--dry-run" in sys.argv
    SCRIPT = '<script src="_depmap.js" defer></script>\n'


    def read(p):
        return io.open(p, encoding="utf-8", newline="").read()


    def write(p, s):
        if not DRY:
            io.open(p, "w", encoding="utf-8", newline="").write(s)


    def edge(a, b):
        """连线:父底 → 子顶;同一行则按左右关系决定从哪条边出发。与 _depmap.js 里的算法一致"""
        A, B = NODES[a], NODES[b]
        ax, bx, m = A[2] + A[4] / 2, B[2] + B[4] / 2, 12
        if A[3] == B[3]:
            if A[2] < B[2]:
                return (A[2] + A[4], A[3] + 16, B[2], B[3] + 16)
            return (A[2], A[3] + 16, B[2] + B[4], B[3] + 16)
        cl = lambda v, lo, hi: lo if v < lo else (hi if v > hi else v)  # noqa: E731
        return (cl(bx, A[2] + m, A[2] + A[4] - m), A[3] + 32,
                cl(ax, B[2] + m, B[2] + B[4] - m), B[3])


    def static_svg():
        """00-index 用的静态图:与 JS 版同源同数据,节点可点,无当前页高亮(它没有「当前页」)"""
        s = ['<svg viewBox="0 0 900 520" role="list" aria-label="22 个内容页之间的依赖关系图,方块可点">']
        s.append('<defs><marker id="ar2" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto">'
                 '<polygon points="0 0, 8 3, 0 6" fill="var(--faint)"/></marker></defs>')
        s.append('<g stroke="var(--faint)" stroke-width="1.2" marker-end="url(#ar2)" fill="none" opacity=".72">')
        for p, c in backbone():
            s.append('<path d="M %g %g L %g %g"/>' % edge(p, c))
        s.append("</g>")
        s.append('<g class="st" font-size="12" text-anchor="middle">')
        for p in sorted(NODES):
            f, lab, x, y, w, fi, st, sw, _mod = NODES[p]
            s.append('<a href="%s.html" aria-label="第 %s 页 %s">' % (f, p, lab[3:]))
            s.append('<rect x="%d" y="%d" width="%d" height="32" rx="9" fill="var(--%s)" '
                     'stroke="var(--%s)" stroke-width="%s"/>' % (x, y, w, fi, st, sw))
            s.append('<text x="%g" y="%d" font-weight="700" fill="var(--%s)">%s</text>'
                     % (x + w / 2, y + 21, st, lab))
            s.append("</a>")
        s.append("</g></svg>")
        return "\n".join(s)


    # kicker 与真相表对齐(见 references/deps.md 第三节的裁定)
    KICKER_FIX = [
        ("13-claude-code", "前置:第 01、11 页", "前置:第 01、11、12 页"),
        ("18-gate-items", "前置:第 17 页", "前置:第 16、17 页"),
        ("20-ai-boundary", "前置:第 8、13、18 页", "前置:第 08、13、18 页"),
    ]

    log = []

    # ---------- 1) 23 页各加一行 script ----------
    for name in ["00-index"] + [NODES[p][0] for p in sorted(NODES)]:
        path = os.path.join(DIR, name + ".html")
        h = read(path)
        if "_depmap.js" in h:
            log.append("跳过(已有) %s" % name)
        elif "</body>" not in h:
            log.append("!! 找不到 </body>: %s" % name)
        else:
            write(path, h.replace("</body>", SCRIPT + "</body>", 1))
            log.append("加脚本 %s" % name)

    # ---------- 2) kicker 对齐 ----------
    for name, old, new in KICKER_FIX:
        path = os.path.join(DIR, name + ".html")
        h = read(path)
        if new in h:
            log.append("跳过(已改) kicker %s" % name)
        elif old not in h:
            log.append("!! kicker 原文未命中: %s (%s)" % (name, old))
        else:
            write(path, h.replace(old, new, 1))
            log.append("改 kicker %s → %s" % (name, new))

    # ---------- 3) 重建 00-index 的静态依赖图 ----------
    p0 = os.path.join(DIR, "00-index.html")
    h = read(p0)
    svgs = re.findall(r"<svg[^>]*aria-label=\"[^\"]*依赖关系图[^\"]*\".*?</svg>", h, re.S)
    if not svgs:
        log.append("!! 00-index 找不到依赖图 SVG")
    else:
        new_svg = static_svg()
        if svgs[0] == new_svg:
            log.append("跳过(已是最新) 00-index 依赖图")
        else:
            h = h.replace(svgs[0], new_svg, 1)
            h = h.replace(
                "箭头方向 = 「先读这个」。第 14 页是汇总页;17→18→19 必须按顺序读",
                "箭头方向 = 「先读这个」·<strong>点任意方块直接跳过去</strong>。"
                "第 14 页是汇总页,测试线与 git 线在这里汇合;17→18→19 必须按顺序读", 1)
            if "figure svg a{" not in h:
                h = h.replace("figure svg{width:100%;height:auto;display:block}",
                              "figure svg{width:100%;height:auto;display:block}\n"
                              "  figure svg a{cursor:pointer}figure svg a:hover rect{stroke-width:2.8}\n"
                              "  figure svg a:focus-visible rect{stroke:var(--accent);stroke-width:2.8}", 1)
            write(p0, h)
            log.append("重建 00-index 依赖图(%d 条主干边,22 个节点可点)" % len(backbone()))

    print("\n".join(log))
    print()
    print("DRY-RUN,未写入" if DRY else "已写入")


if __name__ == "__main__":
    main()
