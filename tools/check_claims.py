# -*- coding: utf-8 -*-
"""口径巡检 —— 同一个事实,全站只许有一种数。

为什么要有这个东西:
    版式有 check_layout,链接有 check_site,但「13 页改成 6 处修正之后,
    00 页的卡片、总数、分组还停在旧数」这类问题,此前没有任何东西在看。
    两轮评审里的「18 条测试 vs 19」「224 行 vs 374」「47 处的卡片过期」
    全是这一类 —— 它们需要的不是重读,是**重数**。

做法:每条断言都现场重新实数(页脚求和、def test_ 计数、wc -l),
再和各处引用比对。跑不平就非零退出。加一条新断言 = 在 CLAIMS 里加一行。
"""

import glob
import io
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AW = os.path.join(ROOT, "ai-workflow")


def read(p):
    return io.open(p, encoding="utf-8", errors="replace").read()


def pages():
    return sorted(p for p in glob.glob(os.path.join(AW, "[0-2]*.html")))


失败 = []


def check(名, 期望, 实际):
    ok = 期望 == 实际
    print("  %s  %-34s 期望 %s / 实际 %s" % ("通过" if ok else "失败!!", 名, 期望, 实际))
    if not ok:
        失败.append(名)


print("=" * 64)
print("口径巡检(每条都现场重数,不引用任何转述)")
print("=" * 64)

# ---------------- 1. 修正数:页脚求和 = 00 页总数 = 分组和 = 各卡片 ----------------
页脚 = {}
for p in pages():
    m = re.search(r"本页含 (\d+) 处", read(p))
    if m:
        页脚[os.path.basename(p)[:2]] = int(m.group(1))
合计 = sum(页脚.values())
A组 = sum(v for k, v in 页脚.items() if k <= "14")
B组 = sum(v for k, v in 页脚.items() if k >= "15")

t00 = read(os.path.join(AW, "00-index.html"))
表 = re.findall(r"<td><strong>(\d+)</strong></td>", t00)
check("00 页总数 = 页脚合计", str(合计), 表[3] if len(表) > 3 else "?")
check("00 页 A 组", str(A组), 表[0] if 表 else "?")
check("00 页 B 组", str(B组), 表[1] if len(表) > 1 else "?")
# 先数一遍卡片,再逐张比对。
# 为什么要单独数:下面是个 re.finditer 循环 —— 00 页的标记一旦改动导致正则零匹配,
# 循环体一次都不执行、一条断言都不产生,脚本照样打印「全部口径一致」并退出 0。
# 那正是「检查器静默变成空操作,而门禁报绿」的教科书形态,而且它就藏在一个
# 专门用来防口径漂移的脚本里。所以:卡片数必须等于有页脚的页数,少一张就是错。
卡片 = [(m.group(1), int(m.group(2))) for m in
        re.finditer(r'href="(\d\d)-[a-z-]+\.html">(?:(?!</a>).)*?含 (\d+) 处修正', t00, re.S)]
check("00 页至少匹配到一张修正卡片", True, len(卡片) > 0)
check("卡片指向的页都有页脚", [], sorted({p for p, _ in 卡片} - set(页脚)))
for pg, n in 卡片:
    if pg in 页脚:
        check("00 页卡片 %s" % pg, 页脚[pg], n)
check("「%d 处发现有误」出现次数" % 合计, 2, len(re.findall(r"%d 处发现有误" % 合计, t00)))

# ---------------- 2. myshop:测试条数 / 源码行数 ----------------
# myshop/ 是独立仓库,不进教程仓库(.gitignore)。CI 的 checkout 里没有它,
# 这一节只在本机(目录存在时)跑 —— 跳过时明说,不装作检查过。
MS = os.path.join(ROOT, "myshop")
if not os.path.isdir(MS):
    print("  跳过  myshop 实数比对(目录不存在 —— CI 环境;本机提交前已跑)")
else:
    条 = sum(len(re.findall(r"^def test_", read(f), re.M))
            for f in glob.glob(os.path.join(MS, "tests", "**", "*.py"), recursive=True))
    # 不写死字面量:期望值由实数产出,测试真增减时不用改检查器。
    # 只查「声称全项目总数」的那两处 —— 全书还有「5 条测试」
    # 「7 个用例」这类分层小计,它们是另一回事,不能一并拿去对。
    for 页, 模式 in [("09-partial-vs-full.html", r"它有 (\d+) 条测试"),
                    ("17-quality-gate.html", r"只有 (\d+) 个用例")]:
        m_ = re.search(模式, read(os.path.join(AW, 页)))
        check("%s 页宣称的总测试数 = 实数" % 页[:2], str(条), m_.group(1) if m_ else "?")

    核心 = sum(len(io.open(os.path.join(MS, "myshop", f + ".py"), encoding="utf-8").readlines())
              for f in ("price", "order", "api"))
    全部 = 核心 + len(io.open(os.path.join(MS, "myshop", "web.py"), encoding="utf-8").readlines())
    t06_ = read(os.path.join(AW, "06-myshop.html"))
    宣称行 = re.search(r"核心三模块合计</strong></td><td><strong>(\d+) 行", t06_)
    check("06 页宣称的核心行数 = 实数", str(核心), 宣称行.group(1) if 宣称行 else "?")
    check("06 页含全量 %d 口径" % 全部, True, ("%d 行" % 全部) in t06_)

# 教程侧旧口径残留检查(不依赖 myshop 目录,CI 也跑)
check("旧口径「18 条测试/18 个用例」残留", 0,
      sum(len(re.findall(r"18 (?:条测试|个用例)", read(p))) for p in pages()))

# ---------------- 3. 关键词全书口径唯一 ----------------
def 全书(词):
    return sum(len(re.findall(词, read(p))) for p in pages())

check("「四道防线」残留(应只在讲『前四道』的语境外为 0)", 0, 全书(r"四道防线卡|共四道防线"))
check("「六道防线」残留", 0, 全书(r"六道防线"))
check("「260 倍」残留", 0, 全书(r"260 倍"))
check("「17 passed」= 不装 playwright 口径(允许存在)", True, 全书(r"17 passed") > 0)
check("「19 passed」= 装后口径(允许存在)", True, 全书(r"19 passed") > 0)
check("daijie27 残留", 0, 全书(r"daijie27"))
check("「8 source files」残留", 0, 全书(r"8 source files"))

# ---------------- 4. kb:知识点计数 = 实际章节数 ----------------
kb0 = read(os.path.join(ROOT, "kb", "00-index.html"))
声明 = [int(m) for m in re.findall(r"(\d+) 个知识点", kb0)]
实际 = [len(re.findall(r'<h2 id="s\d+"', read(f)))
       for f in sorted(glob.glob(os.path.join(ROOT, "kb", "0[1-7]*.html")))]
check("kb 总数 = 各页实际和", sum(实际), 声明[0] if 声明 else -1)
check("kb 各卡片 = 各页实际", 实际, 声明[1:])

print()
if 失败:
    print("口径不平 %d 处:%s" % (len(失败), "、".join(失败)))
    sys.exit(1)
print("全部口径一致。")
sys.exit(0)
