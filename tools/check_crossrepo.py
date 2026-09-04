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
import html
import io
import os
import re
import sys
import tomllib

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AW = os.path.join(ROOT, "ai-workflow")


def read(p):
    with io.open(p, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def 检查照抄的配置(ms, check):
    """第 6 节单独成函数,是为了让 `tools/tests` 能拿合成夹具直接调用它。

    写在 run_checks 里的话,想测它就得把 1–5 节的夹具全造一遍 —— 结果就是
    「不好测」变成「不测」。本仓库栽在「检查器自己没人测」上不止一次,
    所以这里宁可多一层函数。check 是打印兼记账的回调,由调用方传进来。
    """
    # ---------- 6. 教程里那两份「照抄进项目」的配置 ↔ 真实文件 ----------
    # 为什么要有这一节:第 2 页第五节的 pyproject.toml 写着「把下面的内容**整个粘进去**」,
    # 第 11 页第四节的 .gitignore 写着「在项目根目录建 .gitignore」—— 两份都是
    # **读者会照抄成项目文件**的东西,却一直没有任何东西在比它们。
    # 2026-09-03 那次人肉全量比对,一口气从这个缺口里挖出四处不一致(「待修清单」C3/C4):
    # 教程更新过 .gitignore 四处、项目没跟;项目的 pyproject 多两段教程没有的配置。
    #
    # 比的是**语义**,不是逐字:
    #   · pyproject 两边各用 tomllib 解析一遍,比展平后的键值 ——
    #     注释怎么写、键怎么排、缩进几格,都不该报(教程那份的注释是去术语化过的)。
    #   · .gitignore 没有解析器,就比「去掉注释与空行之后的模式序列」。
    #     **按序**比:`!` 反向放行必须排在对应忽略规则之后,顺序在这里是有意义的。
    def 取代码块(页, 关键词):
        """取出该页里唯一同时含全部关键词的 <pre><code> 块,反转义后返回纯文本。

        命中 0 个或 2 个以上都返回 None —— 让调用方报成失败。
        「正则没匹配到就当没问题」是本仓库修过好几轮的那一族缺陷。"""
        块 = re.findall(r"<pre[^>]*>\s*<code[^>]*>(.*?)</code>\s*</pre>",
                       read(os.path.join(AW, 页)), re.S)
        中 = [b for b in 块 if all(k in b for k in 关键词)]
        if len(中) != 1:
            return None
        return html.unescape(re.sub(r"<[^>]*>", "", 中[0]))

    def 展平(d, 前=""):
        出 = {}
        for k, v in d.items():
            键 = 前 + "." + k if 前 else k
            if isinstance(v, dict):
                出.update(展平(v, 键))
            else:
                出[键] = v
        return 出

    # --- 6a. pyproject.toml(第 2 页第五节 ↔ 项目根)---
    教程份 = 取代码块("02-python-setup.html",
                    ["[project]", "[tool.pytest.ini_options]", "[tool.ruff]"])
    真实份 = os.path.join(ms, "pyproject.toml")
    if 教程份 is None:
        check("02 页第五节还能找到那份 pyproject.toml", True, False)
    elif not os.path.isfile(真实份):
        check("配套项目有 pyproject.toml", True, False)
    else:
        try:
            教, 实 = 展平(tomllib.loads(教程份)), 展平(tomllib.loads(read(真实份)))
        except tomllib.TOMLDecodeError as e:
            check("两份 pyproject.toml 都解析得动", True, "解析失败:%s" % e)
        else:
            check("02 页 pyproject 没漏掉真实文件里的配置项", [],
                  sorted(k for k in 实 if k not in 教))
            check("02 页 pyproject 没多出真实文件没有的配置项", [],
                  sorted(k for k in 教 if k not in 实))
            check("02 页 pyproject 每个配置项的值都与真实文件相同", [],
                  sorted(k for k in set(教) & set(实) if 教[k] != 实[k]))

    # --- 6b. .gitignore(第 11 页第四节 ↔ 项目根)---
    教程份 = 取代码块("11-git-basics.html", ["__pycache__/", ".ruff_cache/"])
    真实份 = os.path.join(ms, ".gitignore")
    if 教程份 is None:
        check("11 页第四节还能找到那份 .gitignore", True, False)
    elif not os.path.isfile(真实份):
        check("配套项目有 .gitignore", True, False)
    else:
        def 模式序列(文本):
            return [L.strip() for L in 文本.split("\n")
                    if L.strip() and not L.strip().startswith("#")]

        教, 实 = 模式序列(教程份), 模式序列(read(真实份))
        check("11 页 .gitignore 的模式序列 = 真实文件", 实, 教)

        # 行尾注释是**失效写法**:.gitignore 只认「整行以 # 开头」的注释,
        # `myshop.db  # 本项目跑测试会生成它` 会被当成一个带空格和井号的**文件名**,
        # 那条忽略规则等于白写。2026-09-04 在临时仓库实测定罪 ——
        # 教程那一份原来就是这么写的,而 myshop.db 真的没被忽略(git status 里是 ??)。
        # 两侧都查:任何一侧写回去,这里都要红。
        for 侧, 行们 in (("11 页那份", 教), ("真实 .gitignore", 实)):
            check("%s 里没有行尾注释(那是失效写法)" % 侧, [],
                  [L for L in 行们 if re.search(r"\s#", L)])


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

    # ---------- 4b. 第 18 页第九节那份 YAML ↔ 真实 ci.yml 的骨架 ----------
    # 为什么要有这一节:上面第 3、4 节只查了任务名和三条门禁命令,
    # 而第 18 页第九节是「可以直接抄」的完整配置 —— 读者抄下来的那份,
    # 应该和第 19 页真机演示跑的那份是同一个骨架。
    #
    # 这个洞是用户问「教程跟上了吗」时暴露的:给真实 ci.yml 加了
    # permissions / concurrency / timeout-minutes 之后,第 18 页那份三样都没有,
    # 而所有工具都报绿 —— 因为在此之前没有任何东西在比这两份配置。
    #
    # 只比**骨架**,不比逐字。两边刻意不同的地方有两处,都不该报:
    #   · 第 18 页多一道「门禁 3 · P0 核心用例」(第 19 页用「三道检查版」这个限定语说明了)
    #   · step 的 name 措辞不同(教程「门禁 1 · 代码规范」,真实「门禁 1 · ruff 代码规范」)
    t18 = read(os.path.join(AW, "18-gate-items.html"))
    m18 = re.search(r"# 文件:&lt;你的项目&gt;/\.github/workflows/ci\.yml(.*?)</code></pre>",
                    t18, re.S)
    if not m18:
        check("第 18 页第九节还能找到那份 ci.yml", True, False)
    else:
        y18 = (m18.group(1).replace("&lt;", "<").replace("&gt;", ">")
               .replace("&amp;", "&").replace("&quot;", '"'))
        y18 = re.sub(r"<[^>]+>", "", y18)

        def 骨架(文本):
            """抽出配置的骨架事实;先去掉行尾注释,再逐项取值。"""
            净 = "\n".join(re.sub(r"\s+#.*$", "", L) for L in 文本.split("\n"))
            取 = lambda p, g=1: (re.search(p, 净, re.M).group(g)
                                 if re.search(p, 净, re.M) else None)
            return {
                "顶层键": tuple(sorted(set(re.findall(r"^([a-z_-]+):", 净, re.M)))),
                "令牌只读": bool(re.search(r"^permissions:\s*$\n\s*contents:\s*read\s*$", 净, re.M)),
                "并发取消": bool(re.search(r"^concurrency:", 净, re.M))
                            and bool(re.search(r"cancel-in-progress:\s*true", 净)),
                "并发分组": bool(re.search(r"group:\s*\$\{\{\s*github\.workflow\s*\}\}", 净)),
                "超时分钟": 取(r"^\s{4}timeout-minutes:\s*(\d+)"),
                "任务名": 取(r"^\s{4}name:\s*(.+?)\s*$"),
                "跑在": 取(r"^\s{4}runs-on:\s*(\S+)"),
                "触发": (bool(re.search(r"^\s*push:", 净, re.M)),
                        bool(re.search(r"^\s*pull_request:", 净, re.M)),
                        bool(re.search(r"branches:\s*\[main\]", 净))),
                "动作版本": tuple(sorted(re.findall(r"uses:\s*(actions/[\w-]+@[\w.]+)", 净))),
                "Python": 取(r"python-version:\s*'?([\d.]+)'?"),
                "装的工具": tuple(sorted((取(r"run:\s*pip install\s+(.+?)\s*$") or "").split())),
                "门禁命令": tuple(re.findall(
                    r"^\s*run:\s*((?:ruff check|mypy|pytest)\b.*?)\s*$", 净, re.M)),
            }

        教程侧, 真实侧 = 骨架(y18), 骨架(y)
        for 键 in ("顶层键", "令牌只读", "并发取消", "并发分组", "超时分钟",
                  "任务名", "跑在", "触发", "动作版本", "Python", "装的工具"):
            check("18 页配置 ↔ 真实 ci.yml:%s" % 键, 真实侧[键], 教程侧[键])

        # 门禁命令:真实那份跑的每一条,第 18 页都必须有;
        # 反过来第 18 页允许多出来的,只有那道刻意的 P0。
        check("18 页没漏掉真实门禁跑的命令", [],
              [c for c in 真实侧["门禁命令"] if c not in 教程侧["门禁命令"]])
        check("18 页比真实多出来的 = 只有那道 P0", ["pytest -m p0 -q"],
              [c for c in 教程侧["门禁命令"] if c not in 真实侧["门禁命令"]])

    检查照抄的配置(ms, check)

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
