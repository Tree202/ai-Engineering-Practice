# -*- coding: utf-8 -*-
"""跨仓库一致性巡检 —— 教程说配套项目长什么样,配套项目就得真长那样。

为什么要有这个东西:
    教程(`ai-Engineering-Practice`)和配套项目(`myshop-gate-demo`)是**两个独立仓库**。
    教程里写死了一堆关于配套项目的事实:测试有几条、源码几行、CI 的任务名叫什么、
    门禁跑哪三道、基线输出是什么。**任何一边单独改,另一边不会有任何反应。**

    `check_claims.py` 里那一节 myshop 实数比对,在云端是恒跳过的
    (myshop 被 .gitignore,CI 的 checkout 里根本没有它)—— 它诚实地打印了「跳过」,
    但这意味着那几条断言**在门禁上零覆盖**,只有作者本机提交前才跑。
    这条缺口登记了好几轮,一直叫「跨仓库同步没有云端原子门禁」。

    这个脚本就是补它:CI 里 clone 一份公开的 myshop,把两边对着核。

用法:  python tools/check_crossrepo.py <myshop 目录>
       没给参数时找仓库同级的 myshop/(作者本机的布局)。

退出码:0 = 一致;1 = 有不一致;2 = 找不到配套项目(不当作通过)。
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
    with io.open(p, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def run_checks(ms):
    """ms = myshop 仓库目录。返回 0 / 1。"""
    失败 = []

    def check(名, 期望, 实际):
        ok = 期望 == 实际
        print("  %s  %-38s 期望 %s / 实际 %s" % ("通过" if ok else "失败!!", 名, 期望, 实际))
        if not ok:
            失败.append(名)

    print("=" * 66)
    print("跨仓库一致性(教程 ↔ 配套项目 myshop)")
    print("=" * 66)

    # ---------- 1. 测试条数:教程宣称的总数 = myshop 里 def test_ 的实数 ----------
    条 = sum(len(re.findall(r"^def test_", read(f), re.M))
             for f in glob.glob(os.path.join(ms, "tests", "**", "*.py"), recursive=True))
    for 页, 模式 in [("09-partial-vs-full.html", r"它有 (\d+) 条测试"),
                     ("17-quality-gate.html", r"只有 (\d+) 个用例")]:
        m = re.search(模式, read(os.path.join(AW, 页)))
        check("%s 页宣称的总测试数" % 页[:2], str(条), m.group(1) if m else "(没匹配到)")

    # ---------- 2. 源码行数:第 6 页那张表 ----------
    def 行数(name):
        """文件缺了就返回 None —— 缺文件要报成一条失败,不能让脚本崩在这里。
        (变异验证时删掉 web.py,原来这里直接 FileNotFoundError,
         连后面「配套项目有哪些文件」那一组都跑不到。)"""
        f = os.path.join(ms, "myshop", name + ".py")
        return len(read(f).split("\n")) - 1 if os.path.isfile(f) else None

    每个 = {f: 行数(f) for f in ("price", "order", "api", "web")}
    缺 = [f for f, v in 每个.items() if v is None]
    if 缺:
        check("核心源码文件齐全", [], 缺)
    else:
        核心 = 每个["price"] + 每个["order"] + 每个["api"]
        全部 = 核心 + 每个["web"]
        t06 = read(os.path.join(AW, "06-myshop.html"))
        m = re.search(r"核心三模块合计</strong></td><td><strong>(\d+) 行", t06)
        check("06 页宣称的核心三模块行数", str(核心), m.group(1) if m else "(没匹配到)")
        check("06 页含全量 %d 行口径" % 全部, True, ("%d 行" % 全部) in t06)

    # ---------- 3. CI 的任务名:教程多处写死,改了会和分支保护脱钩 ----------
    ci = os.path.join(ms, ".github", "workflows", "ci.yml")
    if not os.path.isfile(ci):
        check("配套项目有 .github/workflows/ci.yml", True, False)
    else:
        y = read(ci)
        m = re.search(r"^\s*name:\s*(\S+)\s*$", y, re.M | re.U)
        job名 = re.search(r"^    name:\s*(.+?)\s*$", y, re.M)
        check("CI 的任务名 = 教程各处写的「三道检查」", "三道检查",
              job名.group(1) if job名 else "(没匹配到)")
        # 教程第 19 页整条证据链都挂在这个名字上
        t19 = read(os.path.join(AW, "19-gate-demo.html"))
        check("第 19 页仍在引用这个任务名", True, "三道检查" in t19)

        # ---------- 4. 门禁跑的命令:教程第 18 页说是 ruff / mypy / pytest ----------
        # 判据必须锚在行首的 run: 且工具名紧跟其后 —— 写成 run:.*\bmypy\b 会把
        # `run: pip install pytest mypy ruff` 那一行也算上,于是**把门禁整步删掉都测不出来**。
        # 这是变异验证当场抓到的:删掉 mypy 那一步,旧判据照样报绿。
        #
        # 还要再锚一层「命令形状」:只查工具名的话,把 `ruff check .` 换成
        # `ruff --version` 照样报绿 —— 门禁形同虚设却全是绿灯。所以每个工具都要求
        # 它后面跟的是**真的在检查**的那个形态,并显式排除 --version / --help。
        # 这条是 2026-09-03 外部评审指出的。
        形状 = {
            "ruff": r"ruff\s+check\b",          # ruff check .
            "mypy": r"mypy\s*(?:$|[^-\s])",      # mypy 或 mypy <路径>,但不是 mypy --version
            "pytest": r"pytest\b(?!\s*--(?:version|help))",
        }
        for 工具, 形 in 形状.items():
            行 = [L for L in y.split("\n")
                 if re.match(r"^\s*(?:-\s+)?run:\s*(?:python\s+-m\s+)?%s\b" % 工具, L)]
            真查 = any(re.search(形, L) and not re.search(r"--(?:version|help)\b", L) for L in 行)
            check("CI 里真的把 %s 当门禁跑(不是 --version)" % 工具, True, 真查)

    # ---------- 5. 四层测试文件都在 ----------
    for rel in ("myshop/price.py", "myshop/order.py", "myshop/api.py", "myshop/web.py",
                "tests/test_price.py", "tests/test_order.py", "tests/test_api.py",
                "tests/e2e/test_checkout_e2e.py", "tests/e2e/conftest.py"):
        check("配套项目有 %s" % rel, True, os.path.isfile(os.path.join(ms, rel)))

    print()
    if 失败:
        print("跨仓库不一致 %d 处:%s" % (len(失败), "、".join(失败)))
        return 1
    print("教程与配套项目一致。")
    return 0


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    ms = argv[0] if argv else os.path.join(ROOT, "myshop")
    if not os.path.isdir(os.path.join(ms, "myshop")):
        print("!! 找不到配套项目:%s" % ms)
        print("   这个脚本靠比对两个仓库工作,拿不到配套项目就什么都没查 ——")
        print("   所以退 2,不装作通过。CI 里请先 clone。")
        return 2
    print("配套项目:%s\n" % os.path.abspath(ms))
    return run_checks(ms)


if __name__ == "__main__":
    sys.exit(main())
