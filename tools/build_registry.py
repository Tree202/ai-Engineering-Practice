# -*- coding: utf-8 -*-
"""从清点数据生成 references/sources.md 的登记表部分。

表格全部由数据生成,不经过语言模型转写 —— 避免任何编造出处的可能。
叙述部分(覆盖范围、已核实清单、维护建议)由人写,见脚本末尾的常量。
"""
import collections
import io
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")
# 输入数据随仓库发布(references/),不再依赖会话临时目录 —— 453 条可审计、sources.md 可重生成
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRATCH = os.path.join(ROOT, "references")

items = json.load(io.open(os.path.join(SCRATCH, "registry_items.json"), encoding="utf-8"))

# ---------------------------------------------------------------- 类别归一
# 代理是自由填写的,出现了约 170 种写法。按关键词归并成 8 个标准类别。
def norm_kind(k):
    k = (k or "").strip()
    if re.search(r"产品\s*UI|产品行为|快捷键|Claude Code", k):
        return "产品 UI / 行为"
    if re.search(r"第三方|GitHub|gh CLI|平台|数据库|SQLite|Playwright", k):
        return "第三方服务 / 库"
    if re.search(r"版本", k):
        return "版本号"
    if re.search(r"官方文档|引文|术语", k):
        return "官方文档转述"
    if re.search(r"实测|输出|数字|估算|经验数字|量化", k):
        return "实测数字 / 输出"
    if re.search(r"社区做法|阈值建议|约定", k):
        return "社区做法 / 建议"
    if re.search(r"命令|退出码|shell|POSIX", k):
        return "命令行为"
    if re.search(r"配置|默认值|工具行为|语义|标准库|语言", k):
        return "工具配置 / 语义"
    return "其他"


def norm_risk(r):
    r = (r or "").strip()
    return r[0] if r and r[0] in "高中低" else "?"


def norm_marked(m):
    """注意顺序:「无标记(位于 🔄 修正框内)」含 🔄 但不是修正条目,必须先排掉。"""
    m = (m or "").strip()
    if not m or m in ("无", "无标记") or "无标记" in m:
        return "—"
    # 代理常写「非『待验证』标记」这种否定句,不能直接子串匹配
    if "待验证" in m and not re.search(r"非\s*[「『\"']?待验证", m):
        return "⚠ 待验证"
    if "🔄" in m or "&#128260;" in m:
        return "🔄 经核实修正"
    if "🔥" in m:
        return "🔥 重点强调(非修正)"
    return "—"


for it in items:
    it["_k"] = norm_kind(it.get("kind"))
    it["_r"] = norm_risk(it.get("risk"))
    it["_m"] = norm_marked(it.get("marked"))
    it["_p"] = (it.get("page") or "??").strip().zfill(2)

# ---------------------------------------------------------------- 统计
by_kind = collections.Counter(i["_k"] for i in items)
by_risk = collections.Counter(i["_r"] for i in items)
by_page = collections.Counter(i["_p"] for i in items)
marked = collections.Counter(i["_m"] for i in items)
high = [i for i in items if i["_r"] == "高"]

L = []
A = L.append

A("# 断言来源登记表")
A("")
A("> 生成于 2026-09-02 · 由 `tools/` 下的清点流程产出,表格直接来自数据,未经转写。")
A("")

# ---- 第 1 节
A("## 一、这份表是什么、不是什么")
A("")
A("**它不是对教程自称那 193 条清单的复原。**")
A("")
A("目录页写着「193 条断言经官方文档或真机核实,47 处发现有误已修正,1 处标待验证」。")
A("但**教程从未列出那 193 条的完整清单** —— 它只标出了出错的 47 处。原始清单无从复原,")
A("所以这份表是**从成品页面里重新清点**出来的:凡是可被证伪的技术断言(命令行为、配置默认值、")
A("版本号、官方文档转述、第三方服务行为、实测数字)都登记,教学口语、类比、价值判断不登记。")
A("")
A("清点结果是 **%d 条**。" % len(items))
A("")
A("这个数字与 193 **既不是同一批,也不是它的子集或超集** —— 条目边界不同、")
A("拆分粒度不同、纳入门槛也不同(本表以「能不能被一个反例推翻」为门槛,")
A("教程那 193 条的门槛未知)。**拿本表的条数去对 193,一定对不上,这不是任何一方的错误。**")
A("")
A("### 三个标记体例的坑(已逐页 grep 核实)")
A("")
A("这三条不弄清楚,计数一定会错:")
A("")
A("| 标记 | 实际含义 | 常被误读为 |")
A("|---|---|---|")
A("| 🔄 | 经核实修正 —— 真正的修正标记 | — |")
A("| ⚠ | **绝大多数是警告框图标**(「实测发现的坑」「AI 协作专属陷阱」) | 误当成「待验证」 |")
A("| 🔥 | **重点强调**(「这是全书最重要的…」),全书 52 处、跨 14 页 | 误当成修正标记 |")
A("")
A("**全书真正的「待验证」只有 1 条**,在 17 页第八节 ——")
A("「把判定写在脚本里、只看退出码」属社区常见做法,查不到官方推荐的原文。")
A("教程自己也标了「全书唯一待验证项」—— 这一点属实。")
A("")
A("**最重要的一条**:除第三节明确列出的以外,本表其余条目**只做了登记,没有独立复核**。")
A("「教程自称依据」一栏原样转述教程自己的说法 —— 教程写「真机实测」就记「真机实测」,")
A("没标出处就记「未标注」。**没有替它补任何外部来源,也没有编造任何官方链接或引文。**")
A("")

A("## 二、总览")
A("")
A("| 类别 | 条数 |")
A("|---|---|")
for k, v in by_kind.most_common():
    A("| %s | %d |" % (k, v))
A("| **合计** | **%d** |" % len(items))
A("")
A("| 过时风险 | 条数 | 判据 |")
A("|---|---|---|")
A("| 高 | %d | 产品 UI、第三方服务行为、具体版本号 —— 半年内可能变 |" % by_risk.get("高", 0))
A("| 中 | %d | 工具默认值、配置项语义 —— 随大版本变 |" % by_risk.get("中", 0))
A("| 低 | %d | 语言与命令的稳定语义 —— 多年不变 |" % by_risk.get("低", 0))
A("")
# 标记计数直接数 HTML 源码 —— 不用提取结果,因为逐条标注不够准
import glob as _g
_raw = {}
for _f in sorted(_g.glob(os.path.join(ROOT, "ai-workflow", "[0-2][0-9]-*.html"))):
    _t = io.open(_f, encoding="utf-8").read()
    _pg = os.path.basename(_f)[:2]
    _raw[_pg] = (_t.count("&#128260;"), _t.count("&#128293;"))
_fix = sum(v[0] for v in _raw.values())
_hot = sum(v[1] for v in _raw.values())
A("教程自身的标记(**直接数 HTML 源码**,不经提取环节):")
A("")
A("| 标记 | 全书出现 | 涵盖页数 |")
A("|---|---|---|")
A("| 🔄 经核实修正 | %d 处 | %d 页 |" % (_fix, len([1 for v in _raw.values() if v[0]])))
A("| 🔥 重点强调(**非修正**) | %d 处 | %d 页 |" % (_hot, len([1 for v in _raw.values() if v[1]])))
A("| ⚠ 待验证 | **1 条**(17 页第八节) | 1 页 |")
A("")
A("> **为什么是 %d 而不是 47。** 页脚合计声明 47 处修正,但源码里有 %d 个 🔄" % (_fix, _fix))
A("> —— 同一处修正常在正文和页末「知识点回顾」表里各标一次,少数还跨页复述。")
A("> 两个数字都对,只是口径不同。")
A("")
A("> **逐条表里的「标记」一栏不要拿去对账。** 那一栏是清点时逐条归的,")
A("> 会把一个 🔄 框里的多条独立断言各算一条,也偶有误标。**以上表为准。**")
A("")
A("断言密度最高的页面:")
A("")
A("| 页 | 条数 | | 页 | 条数 |")
A("|---|---|---|---|---|")
top = by_page.most_common(16)
for i in range(0, len(top), 2):
    a = top[i]
    b = top[i + 1] if i + 1 < len(top) else ("", "")
    A("| 第 %s 页 | %s | | %s | %s |" % (a[0], a[1], ("第 %s 页" % b[0]) if b[0] else "", b[1]))
A("")

# ---- 第 3 节(人写,见常量)
VERIFIED = """## 三、已独立核实(有一手出处)

以下是 2026-09-01 至 09-02 这轮工作中**实际查过一手来源**的部分 —— 官方文档原文、
或本机实测。其余条目一律归入第四节。

### 对照 code.claude.com 官方文档

| 断言 | 出处 | 结论 |
|---|---|---|
| 权限模式共**六种**:`default` / `acceptEdits` / `plan` / `auto` / `dontAsk` / `bypassPermissions` | `permission-modes.md` 模式表 | ✅ 教程原文正确 |
| `auto` 是独立模式(后台分类器审查),状态栏 `⏵⏵ auto mode on` | 同上,`auto` 有独立章节 | ✅ 正确;v2.1 曾误改为「不是模式名」,已改回 |
| Pro / Max / Team 计划新开会话从 `auto` 起步 | `permission-modes.md` | ✅ 正确 |
| 从 `auto` 到 `plan` 要按三下 Shift+Tab | 同上(第一下回 `default`,再 `default → acceptEdits → plan`) | ✅ 正确 |
| `acceptEdits` 还自动批准 `mkdir` `touch` **`rm`** `rmdir` `mv` `cp` `sed` | 同上 | ⚠️ 教程原写「运行命令仍要问你」不准,已修正 |
| plan mode 的英文定义引文 | `permission-modes.md` | ✅ **逐字**出自官方,不是转述 |
| 保留**最近 100 个**检查点 | `checkpointing.md` | ✅ 正确 |
| 检查点随会话 **30 天**后清除 | 同上 | ✅ 正确 |
| 撤销菜单的两个「恢复代码」选项仅在有跟踪到文件改动时出现 | 同上 | ⚠️ 教程原未说明,已补 |
| 后台 `/code-review --fix` 的编辑不可回退 | 同上 | ✅ 正确 |
| 符号链接 / 硬链接路径不还原 | 同上(官方第四条限制) | ⚠️ 教程原缺此条,已补 |
| `CLAUDE.md` 支持 `@path/to/import` 导入 | `memory.md` | ✅ 正确(某次核查曾误报「查无依据」) |

### 对照 GitHub 仓库

| 断言 | 出处 | 结论 |
|---|---|---|
| `actions/checkout@v7` | 仓库 releases,最新 v7.0.1 | ✅ 正确 |
| `actions/setup-python@v7` | 仓库 releases,最新 v7.0.0 | ✅ 正确 |

### 本机实测

| 断言 | 验证方式 | 结论 |
|---|---|---|
| `git switch` 遇不存在的分支退出码 **128** | 本机 git 实测(`die()` 一律 128) | ⚠️ 教程原标 1,已修正 |
| pytest `python_functions` 默认值是 `["test"]` | pytest 行为 | ✅ 正确 |
| myshop 基线 `17 passed, 1 skipped` | Windows + Python 3.12.10 实跑 | ✅ 复现(快照时点;后装 playwright 后为 19 passed,见变更清单 E2E 节) |
| 08 页五次改坏实验的红绿计数(3/2、8/9/1、6/11/1、1/16/1、3/14/1) | 同上,逐次复现 | ✅ 全部吻合 |
| `mypy` 输出 `8 source files` | 同上 | ✅ 复现(快照时点;后增 web.py/conftest 后为 10) |
| API 契约:`POST /orders` 返回 `"total_text": "¥25.00"` | curl 实测 | ✅ 复现 |
| 导航栏高度:桌面 54.3px、375px 手机 100.9px | 浏览器实测 | ⚠️ 据此修掉写死的 `scroll-padding-top:78px` |
| `&#9311;`(U+245F)渲染为空方块,`&#9450;`(U+24EA)才是 ⓪ | 渲染对照 | ⚠️ 05/15 页 8 处已修正 |

### 已定罪为错、且**不该照抄**的原文

| 页 | 原文 | 问题 |
|---|---|---|
| 22 | 「144 = 128+16 是被手动终止」 | 信号 16 与手动终止无关;应为 130(SIGINT)/143(SIGTERM),且限 POSIX |
| 14 | 步骤 4 用 `git diff --staged --name-only` | 该时点暂存区必空,什么都列不出;已改 `git status --short` |

"""
A(VERIFIED)

# ---- 第 4 节:未复核清单
A("## 四、仅登记、未独立复核")
A("")
A("按页号排列。**「教程自称依据」是教程原文的说法,不代表已验证。**")
A("")
for p in sorted(by_page):
    grp = [i for i in items if i["_p"] == p]
    A("<details><summary><b>第 %s 页 —— %d 条</b></summary>" % (p, len(grp)))
    A("")
    A("| 位置 | 断言 | 类别 | 教程自称依据 | 标记 | 风险 |")
    A("|---|---|---|---|---|---|")
    for i in grp:
        sec = (i.get("section") or "").replace("|", "/")[:34]
        cl = (i.get("claim") or "").replace("|", "/").replace("\n", " ")[:150]
        src = (i.get("stated_source") or "未标注").replace("|", "/")[:24]
        A("| %s | %s | %s | %s | %s | %s |" % (sec, cl, i["_k"], src, i["_m"], i["_r"]))
    A("")
    A("</details>")
    A("")

# ---- 第 5 节:高风险
A("## 五、高风险清单(建议优先复核)")
A("")
A("共 **%d 条**。这些是产品 UI、第三方服务行为和具体版本号 —— 教程内容本身没问题时," % len(high))
A("它们也会因为外部变化而过时。**建议每半年复核一次**,顺序按下表。")
A("")
hk = collections.Counter(i["_k"] for i in high)
A("| 类别 | 条数 | 为什么优先 |")
A("|---|---|---|")
why = {
    "产品 UI / 行为": "菜单项、快捷键、模式名随产品迭代变,且无版本锚点时读者无从判断",
    "第三方服务 / 库": "GitHub / CI / 库的行为由外部决定,教程无法控制",
    "版本号": "写死的版本号必然过时,且照抄会导致构建失败",
    "官方文档转述": "文档改版后转述可能与原意脱节",
    "实测数字 / 输出": "换环境即变,需注明测量条件",
    "工具配置 / 语义": "默认值随大版本调整",
    "社区做法 / 建议": "非官方约定,可能被新实践取代",
    "命令行为": "多数稳定,少数随版本变",
    "其他": "—",
}
for k, v in hk.most_common():
    A("| %s | %d | %s |" % (k, v, why.get(k, "—")))
A("")
A("**风险高度集中**:13 页一页就占掉其中 %d 条 —— 该页几乎全是产品 UI。" % len([i for i in high if i["_p"] == "13"]))
A("好消息是那一页已经对着官方文档逐条核过(见第三节)。")
A("")
A("按页分布:")
A("")
hp = collections.Counter(i["_p"] for i in high)
A("| 页 | 高风险条数 | | 页 | 高风险条数 |")
A("|---|---|---|---|---|")
ht = hp.most_common()
for i in range(0, len(ht), 2):
    a = ht[i]
    b = ht[i + 1] if i + 1 < len(ht) else ("", "")
    A("| 第 %s 页 | %s | | %s | %s |" % (a[0], a[1], ("第 %s 页" % b[0]) if b[0] else "", b[1]))
A("")
A("<details><summary><b>展开全部 %d 条高风险断言</b></summary>" % len(high))
A("")
A("| 页 | 断言 | 类别 | 风险说明 |")
A("|---|---|---|---|")
for i in sorted(high, key=lambda x: (x["_p"], x["_k"])):
    cl = (i.get("claim") or "").replace("|", "/").replace("\n", " ")[:130]
    rk = (i.get("risk") or "").replace("|", "/")[:60]
    A("| %s | %s | %s | %s |" % (i["_p"], cl, i["_k"], rk))
A("")
A("</details>")
A("")

MAINT = """## 六、维护建议

**复核节奏**

| 类别 | 建议周期 | 触发式复核 |
|---|---|---|
| 版本号 | 每 3 个月 | 每次有人反馈「照抄跑不通」 |
| 产品 UI / 行为 | 每 6 个月 | 产品发大版本时 |
| 第三方服务 / 库 | 每 6 个月 | GitHub Actions 或 CI 报废弃警告时 |
| 官方文档转述 | 每 6 个月 | 文档站改版时 |
| 实测数字 / 输出 | 换基准环境时 | 升级 Python / pytest 大版本时 |
| 工具配置 / 语义 | 每 12 个月 | 工具发大版本时 |
| 命令行为 · 社区做法 | 按需 | — |

**三条规矩**(都是这轮踩出来的)

1. **不要转手。** 让别人(包括模型)去查,拿回来的可能是**编造的引文**。这轮就出现过:
   一个核查代理为了给结论配依据,写了一句 `checkpointing.md` 里根本不存在的「官方原文」。
   凡是要写进正文的结论,自己抓一次原文。
2. **改之前先确认原文是错的。** v2.1 那轮把 13 页正确的「权限模式共六种」改成了「四种」,
   就是因为信了审读结论没去核。**修正本身也需要被核实。**
3. **区分「已核实」与「未复核」。** 本表第三节和第四节的分界不能模糊 ——
   教程最大的资产是「不用猜的内容糊过去」,一旦把未复核的写成已核实,这个资产就没了。

**近期需盯的三条**(2026-09-02 登记)

- pytest 9.1 将把 importorskip 遇 ImportError 的警告升级为错误:pytest_playwright 与 playwright 版本不匹配时可能击穿 17 passed, 1 skipped 基线。
- 11 页「switch/restore 自 git 2.51 起摘掉实验性标签」未二次核实。
- 16/18 页 actions@v7:2026-09-01 核过(checkout v7.0.1 / setup-python v7.0.0),下次复核按半年节奏。

**这份表怎么更新**

清点流程会重新扫描全书正文,提取可证伪断言并分类。复核结论要手工填进第三节 ——
**那一节永远只放有一手出处的条目**。
"""
A(MAINT)

def main():
    out = os.path.join(ROOT, "references", "sources.md")
    # 注意:必须读 git HEAD 里那份原件,不能读 out 本身 —— 否则重跑会把上一版登记表整个吞成附录
    orig = os.path.join(SCRATCH, "sources_v21_orig.md")
    old = io.open(orig, encoding="utf-8").read() if os.path.exists(orig) else ""
    # 保留原有的「本次修正依据」小表,附在末尾
    keep = ""
    if "## 工具版本" in old:
        keep = "\n---\n\n## 附:v2.1 那轮修正所依据的来源\n\n" + old.split("# 本次修正依据的来源登记", 1)[-1].split("\n", 2)[-1]
    io.open(out, "w", encoding="utf-8", newline="\n").write("\n".join(L) + keep)
    print("已写入 %s" % out)
    print("  登记条目 %d 条 · 高风险 %d 条 · 类别归一为 %d 类" % (len(items), len(high), len(by_kind)))
    print("  文件大小 %d 字节" % os.path.getsize(out))


if __name__ == "__main__":
    main()
