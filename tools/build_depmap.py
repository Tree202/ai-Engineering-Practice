# -*- coding: utf-8 -*-
"""依赖关系的唯一事实源 —— 改依赖关系只改这一个文件。

跑一次会同时产出两样东西:
    ai-workflow/_depmap.js   运行时地图(23 页共用的那个外链脚本)
    references/deps.md       给人看的依赖表(含裁定理由)

00-index.html 里那张静态图也由同一份数据生成,见 tools/patch_pages.py。

用法:  python tools/build_depmap.py
"""
import io
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---------------------------------------------------------------------------
# 节点表:页号 -> [文件名, 图上标签, x, y, 宽, 填充色, 描边色, 描边粗细, 所属部分]
# 坐标与配色沿用 00-index 原图,改版面时改这里
# ---------------------------------------------------------------------------
NODES = {
    "01": ["01-overview", "01 全景(无依赖)", 362, 12, 176, "plan-soft", "plan", 1.5, "一 · 打地基"],
    "02": ["02-python-setup", "02 Python 环境", 362, 62, 176, "ok-soft", "ok", 2.0, "一 · 打地基"],
    "03": ["03-test-basics", "03 测试是什么", 206, 112, 148, "info-soft", "info", 1.5, "一 · 打地基"],
    "04": ["04-static-check", "04 静态检查", 546, 112, 148, "info-soft", "info", 1.5, "一 · 打地基"],
    "05": ["05-pyramid", "05 金字塔", 362, 162, 176, "warn-soft", "warn", 1.5, "二 · 测试体系"],
    "06": ["06-myshop", "06 myshop", 26, 162, 148, "raised", "rule2", 1.5, "二 · 测试体系"],
    "07": ["07-four-layers-code", "07 四层代码", 206, 212, 148, "warn-soft", "warn", 1.5, "二 · 测试体系"],
    "08": ["08-break-it", "08 改坏实验", 206, 262, 148, "bad-soft", "bad", 1.5, "二 · 测试体系"],
    "09": ["09-partial-vs-full", "09 局部vs全量", 206, 312, 148, "bad-soft", "bad", 1.5, "二 · 测试体系"],
    "10": ["10-flaky-e2e", "10 E2E / flaky", 546, 212, 148, "warn-soft", "warn", 1.5, "二 · 测试体系"],
    "11": ["11-git-basics", "11 git 基础", 726, 112, 160, "raised", "rule2", 1.5, "三 · 存档与撤销"],
    "12": ["12-git-undo", "12 git 回退", 726, 162, 160, "raised", "rule2", 1.5, "三 · 存档与撤销"],
    "13": ["13-claude-code", "13 Claude Code", 726, 262, 160, "plan-soft", "plan", 1.5, "四 · 工具与流程"],
    "14": ["14-workflow", "14 七步流程实战(汇总)", 420, 312, 220, "ok-soft", "ok", 2.2, "四 · 工具与流程"],
    "15": ["15-team-roles", "15 岗位工具", 300, 362, 150, "raised", "rule2", 1.5, "五 · 放大到团队"],
    "16": ["16-pipeline", "16 流水线", 470, 362, 150, "raised", "rule2", 1.5, "五 · 放大到团队"],
    "17": ["17-quality-gate", "17 门禁概念", 300, 412, 150, "warn-soft", "warn", 2.2, "六 · 质量门禁"],
    "18": ["18-gate-items", "18 门禁检查项", 470, 412, 150, "warn-soft", "warn", 2.2, "六 · 质量门禁"],
    "19": ["19-gate-demo", "19 门禁实战", 640, 412, 150, "warn-soft", "warn", 2.2, "六 · 质量门禁"],
    "20": ["20-ai-boundary", "20 AI 边界", 120, 470, 150, "raised", "rule2", 1.5, "七 · 收尾"],
    "21": ["21-automation", "21 固化流程", 290, 470, 150, "raised", "rule2", 1.5, "七 · 收尾"],
    "22": ["22-cheatsheet", "22 速查(独立)", 460, 470, 150, "sunk", "rule", 1.5, "七 · 收尾"],
}

# ---------------------------------------------------------------------------
# 直接前置 —— 经人工裁定(理由见 references/deps.md 第三节)
# ---------------------------------------------------------------------------
PRE = {
    "01": [], "02": ["01"], "03": ["02"], "04": ["02", "03"], "05": ["03", "04"],
    "06": ["02", "05"],                       # 裁定①
    "07": ["03", "05", "06"], "08": ["04", "06", "07"], "09": ["08"], "10": ["05", "07"],
    "11": ["02"], "12": ["11"],
    "13": ["01", "11", "12"],                 # 裁定②
    "14": ["09", "13"],                       # 裁定③
    "15": ["05", "14"], "16": ["14", "15"],
    "17": ["16"],                             # 裁定④
    "18": ["16", "17"], "19": ["17", "18"],
    "20": ["08", "13", "18"],                 # 裁定⑤
    "21": ["13", "14", "20"], "22": [],
}

# 裁定记录:页号 -> (图上原本画的, kicker 原本声明的, 采用值, 理由)
RULINGS = [
    ("06", "无入边(孤立根节点)", "02、05", "02、05",
     "图画错了。06 页讲的是在 myshop 上解剖代码,不先有 Python 环境无从谈起;"
     "而该页核心论点「三个源码文件的依赖递增顺序正好是金字塔层级」直接建立在 05 之上。"),
    ("13", "12", "01、11", "01、11、12",
     "三者并存,不是互斥。01 建立五道防线框架(13 页讲其中的防线 ①③)、11 建立 git 概念,"
     "这两条是必要前置;13 页讲「一键撤销的死角」时要与 12 页的 git 回退作对比,故 12 也保留。"),
    ("14", "09、13", "01–13(全部)", "09、13(文案另注「汇总 01–13」)",
     "两者都对,用途不同:kicker 说的是内容上汇总了前面全部,图说的是拓扑上的直接汇合点。"
     "若照 kicker 字面建数据,01–13 每页的「解锁」都要挂上 14,一跳邻域会被冲垮。"),
    ("17", "15", "16", "16",
     "16 页自己的 kicker 就写着「门禁的前置」,且 17 页的三层拦截、四要素全部建立在"
     "流水线的八道关卡之上;15 页讲岗位与工具选型,不是理解门禁的必要条件。"
     "图上跳过 16 直连 15 是漏画了一条边。"),
    ("20", "17", "8、13、18", "08、13、18",
     "20 页的七种作弊手法直接引用 08 页的改坏实验、13 页的能力边界、18 页的门禁项清单,"
     "三条都是实打实的内容依赖。图上那条 17→20 更像是为了让节点不悬空而随手连的。"
     "顺带:kicker 原文写「第 8 页」,是全书唯一一处不带前导零,已统一为「08」。"),
]

# 另有 8 处是「一方漏写」而非互斥,按并集合并,不需要裁定
MERGED = [
    ("04", "图缺 03"), ("08", "图缺 04、06"), ("10", "图缺 07"), ("15", "图缺 05"),
    ("16", "图缺 15"), ("18", "kicker 缺 16"), ("19", "图缺 17"), ("21", "图缺 13、20"),
]


# ---------------------------------------------------------------------------
def ancestors(p, memo=None):
    """p 的全部祖先(含间接)"""
    if memo is None:
        memo = {}
    if p in memo:
        return memo[p]
    s = set()
    memo[p] = s
    for q in PRE[p]:
        s.add(q)
        s |= ancestors(q, memo)
    return s


def backbone():
    """传递约简:一条边 p→c,若 p 已是 c 的另一个前置的祖先,说明有更长的路径能推出它,不必画"""
    memo = {}
    out = []
    for c in sorted(PRE):
        for p in PRE[c]:
            if any(p in ancestors(o, memo) for o in PRE[c] if o != p):
                continue
            out.append((p, c))
    return out


def successors():
    suc = {p: [] for p in NODES}
    for c in sorted(PRE):
        for p in PRE[c]:
            suc[p].append(c)
    return suc


# ---------------------------------------------------------------------------
def build_js():
    rows = []
    for p in sorted(NODES):
        f, lab, x, y, w, fi, st, sw, mod = NODES[p]
        rows.append("%s|%s|%s|%d|%d|%d|%s|%s|%s|%s" % (p, f, lab, x, y, w, fi, st, sw, mod))
    data = "\\n".join(rows)
    pres = ",".join('"%s":"%s"' % (p, "".join(PRE[p])) for p in sorted(PRE) if PRE[p])

    tpl = io.open(os.path.join(ROOT, "tools", "depmap.tpl.js"), encoding="utf-8").read()
    js = tpl.replace("__DATA__", data).replace("__PRES__", pres)
    out = os.path.join(ROOT, "ai-workflow", "_depmap.js")
    io.open(out, "w", encoding="utf-8", newline="\n").write(js)
    return out, len(js.encode("utf-8"))


def build_md():
    bone = set(backbone())
    suc = successors()
    L = []
    L.append("# 依赖关系真相表")
    L.append("")
    L.append("全站唯一事实源。**改依赖关系只改 `tools/build_depmap.py`,再跑一次 "
             "`python tools/build_depmap.py`**,不要手改下面任何一份产物。")
    L.append("")
    L.append("消费这份数据的有三处:")
    L.append("")
    L.append("| 消费方 | 用到什么 | 怎么生成 |")
    L.append("|---|---|---|")
    L.append("| `ai-workflow/_depmap.js` | 全部节点 + 全部直接前置 | `build_depmap.py` |")
    L.append("| `ai-workflow/00-index.html` 的静态图 | 节点 + 主干边 | `patch_pages.py` |")
    L.append("| 22 页各自的 `kicker`「前置:第 N 页」 | 直接前置 | 人工核对(见第四节) |")
    L.append("")
    L.append("---")
    L.append("")
    L.append("## 一、22 页依赖全表")
    L.append("")
    L.append("| 页 | 文件 | 所属部分 | 直接前置 | 直接后继(解锁) |")
    L.append("|---|---|---|---|---|")
    for p in sorted(NODES):
        f = NODES[p][0]
        mod = NODES[p][8]
        pre = "、".join(PRE[p]) or "—"
        su = "、".join(suc[p]) or "—(支线终点)"
        L.append("| %s | `%s.html` | %s | %s | %s |" % (p, f, mod, pre, su))
    L.append("")
    L.append("直接依赖共 **%d** 条。" % sum(len(v) for v in PRE.values()))
    L.append("")
    L.append("## 二、主干边(传递约简后)")
    L.append("")
    L.append("39 条直接依赖里,有 18 条能由更长的路径推出来。全画出来是一团意大利面,")
    L.append("所以图上常显的是**传递约简**后的 **%d 条主干边**;" % len(bone))
    L.append("被约简掉的那些没有丢——打开某一页的地图时,该页的跨层依赖会以虚线单独画出。")
    L.append("")
    L.append("```")
    L.append("测试线  01→02→03→04→05→06→07→08→09 ┐")
    L.append("                          └→10        ├→ 14 →15→16→17→18→19")
    L.append("git 线  02→11→12→13 ────────────────┘              └→20→21")
    L.append("")
    L.append("22 速查:独立,无前置无后继")
    L.append("```")
    L.append("")
    L.append("逐条列出:")
    L.append("")
    L.append("| 主干边 | | 主干边 | | 主干边 |")
    L.append("|---|---|---|---|---|")
    bl = ["%s → %s" % (p, c) for p, c in backbone()]
    for i in range(0, len(bl), 3):
        row = bl[i:i + 3]
        while len(row) < 3:
            row.append("")
        L.append("| %s | | %s | | %s |" % tuple(row))
    L.append("")
    L.append("## 三、5 处互斥冲突的裁定")
    L.append("")
    L.append("改版前,目录页那张图的 24 条边与 22 页 `kicker` 声明的前置**有 13 页对不上**,")
    L.append("其中 5 处是两边说法互斥(另 8 处是一方漏写,见第四节)。")
    L.append("这些矛盾此前一直藏在两个不同的位置——图在目录页、前置在各页顶部——没人对照过。")
    L.append("")
    for i, (p, g, k, take, why) in enumerate(RULINGS, 1):
        L.append("### 裁定%s · 第 %s 页" % ("①②③④⑤"[i - 1], p))
        L.append("")
        L.append("| | |")
        L.append("|---|---|")
        L.append("| 图上原本画的 | %s |" % g)
        L.append("| kicker 原本声明的 | %s |" % k)
        L.append("| **采用** | **%s** |" % take)
        L.append("")
        L.append(why)
        L.append("")
    L.append("## 四、8 处漏写(按并集合并,无需裁定)")
    L.append("")
    L.append("| 页 | 情况 | 合并后的前置 |")
    L.append("|---|---|---|")
    for p, note in MERGED:
        L.append("| %s | %s | %s |" % (p, note, "、".join(PRE[p])))
    L.append("")
    L.append("其余 9 页(01、02、03、05、07、09、11、12、22)两边本来就一致。")
    L.append("")
    L.append("## 五、改了之后要做什么")
    L.append("")
    L.append("```bash")
    L.append("python tools/build_depmap.py      # 重新生成 _depmap.js 与本文件")
    L.append("python tools/patch_pages.py       # 重建 00-index 的静态图(幂等)")
    L.append("python tools/check_site.py        # 站点自检:链接/锚点/重复 id/导航")
    L.append("```")
    L.append("")
    L.append("如果动的是某页的直接前置,记得同步该页 `kicker` 里的「前置:第 N 页」——")
    L.append("那一处目前仍是人工维护的,也正是当初 13 页不一致的来源。")
    L.append("")
    out = os.path.join(ROOT, "references", "deps.md")
    io.open(out, "w", encoding="utf-8", newline="\n").write("\n".join(L))
    return out, len(bone)


if __name__ == "__main__":
    js, n = build_js()
    md, nb = build_md()
    print("已生成 %s (%d 字节)" % (js, n))
    print("已生成 %s (%d 条主干边)" % (md, nb))
