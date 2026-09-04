# -*- coding: utf-8 -*-
"""check_crossrepo.py 第 6 节(照抄进项目的那两份配置)的合成夹具测试。

第 6 节比的是「读者会照抄成项目文件的东西」——
第 2 页第五节那份 `pyproject.toml`(原话:「把下面的内容**整个粘进去**」)、
第 11 页第四节那份 `.gitignore`(原话:「在项目根目录建 .gitignore」)。
2026-09-03 之前没有任何东西在比它们,一次人肉全量比对就从这个缺口挖出四处不一致。

覆盖三类:
  ① 两侧配置真的不一样 —— 该报的
  ② **行尾注释**那条真 bug 的回归锁 —— `.gitignore` 只认整行 `#` 注释,
     写成 `myshop.db  # 说明` 会被当成一个带空格和井号的文件名,规则白写。
     实测定罪过:那样写时 `git status` 里 myshop.db 仍是 `??`。
  ③ **代码块找不到时不许静默通过** —— 正则零匹配要报成失败,不是没事发生。

变异验证(改完请手工确认一次):把 `检查照抄的配置` 改成直接 `return`,
下面除 test_两侧一致 外全红。

用法:  python -m unittest discover -s tools/tests
"""

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "check_crossrepo_under_test", TOOLS / "check_crossrepo.py")
cr = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = cr
_spec.loader.exec_module(cr)


PYPROJECT = """[project]
name = "myshop"
requires-python = "&gt;=3.9"

[tool.pytest.ini_options]
testpaths = ["tests"]
markers = [
  "p0: 核心链路,挂了就是生产事故",
]

[tool.ruff]
line-length = 100
"""

GITIGNORE = """# ---- Python 垃圾 ----
__pycache__/
.ruff_cache/

# ---- 本地数据库 ----
*.sqlite3
myshop.db
"""


def 页(块):
    return "<html><body><pre><code>" + 块 + "</code></pre></body></html>"


class 夹具:
    """假的 ai-workflow/ 与假的 myshop/。"""

    def __init__(self, 目录, 教程py=PYPROJECT, 教程gi=GITIGNORE,
                 项目py=None, 项目gi=None, 建02=True, 建11=True):
        self.dir = Path(目录)
        aw, ms = self.dir / "ai-workflow", self.dir / "ms"
        aw.mkdir(parents=True, exist_ok=True)
        ms.mkdir(parents=True, exist_ok=True)
        if 建02:
            (aw / "02-python-setup.html").write_text(页(教程py), encoding="utf-8")
        else:
            (aw / "02-python-setup.html").write_text("<html>没有代码块</html>", encoding="utf-8")
        if 建11:
            (aw / "11-git-basics.html").write_text(页(教程gi), encoding="utf-8")
        else:
            (aw / "11-git-basics.html").write_text("<html>没有代码块</html>", encoding="utf-8")
        # 项目侧:默认与教程一致(pyproject 要把 HTML 实体还原回去)
        (ms / "pyproject.toml").write_text(
            项目py if 项目py is not None else 教程py.replace("&gt;", ">"), encoding="utf-8")
        (ms / ".gitignore").write_text(
            项目gi if 项目gi is not None else 教程gi, encoding="utf-8")
        self.aw, self.ms = str(aw), str(ms)


def 跑(f):
    """返回 [(名, 期望, 实际, 是否通过), ...]"""
    记 = []

    def check(名, 期望, 实际):
        记.append((名, 期望, 实际, 期望 == 实际))

    旧 = cr.AW
    cr.AW = f.aw
    try:
        cr.检查照抄的配置(f.ms, check)
    finally:
        cr.AW = 旧
    return 记


def 失败项(记):
    return [r[0] for r in 记 if not r[3]]


class TestCopiedConfig(unittest.TestCase):

    def test_两侧一致时不报(self):
        with tempfile.TemporaryDirectory() as d:
            记 = 跑(夹具(d))
        self.assertTrue(记, "一条都没查?那才是最危险的情况")
        self.assertEqual([], 失败项(记), 记)

    def test_项目多一个配置项要报(self):
        with tempfile.TemporaryDirectory() as d:
            多 = PYPROJECT.replace("&gt;", ">") + '\n[tool.mypy]\nstrict = true\n'
            记 = 跑(夹具(d, 项目py=多))
        self.assertIn("02 页 pyproject 没漏掉真实文件里的配置项", 失败项(记))

    def test_教程多一个配置项要报(self):
        with tempfile.TemporaryDirectory() as d:
            多 = PYPROJECT + '\n[tool.mypy]\nstrict = true\n'
            # 项目侧必须显式给基准那份 —— 夹具默认让项目跟着教程走,
            # 只改教程的话两边又一致了,这条测试就成了空跑(第一版正是这么写错的)。
            记 = 跑(夹具(d, 教程py=多, 项目py=PYPROJECT.replace("&gt;", ">")))
        self.assertIn("02 页 pyproject 没多出真实文件没有的配置项", 失败项(记))

    def test_同一个键值不同要报(self):
        with tempfile.TemporaryDirectory() as d:
            改 = PYPROJECT.replace("&gt;", ">").replace("line-length = 100", "line-length = 88")
            记 = 跑(夹具(d, 项目py=改))
        self.assertIn("02 页 pyproject 每个配置项的值都与真实文件相同", 失败项(记))

    def test_注释与键序不同不该报(self):
        """教程那份的注释是去术语化过的,键序也可能不同 —— 这两样都不该报。"""
        with tempfile.TemporaryDirectory() as d:
            换 = """[project]
# 完全不同的注释
requires-python = ">=3.9"
name = "myshop"

[tool.ruff]
line-length = 100   # 行尾注释

[tool.pytest.ini_options]
markers = [
    "p0: 核心链路,挂了就是生产事故",
]
testpaths = ["tests"]
"""
            记 = 跑(夹具(d, 项目py=换))
        self.assertEqual([], 失败项(记), 记)

    def test_gitignore_内容不同要报(self):
        with tempfile.TemporaryDirectory() as d:
            记 = 跑(夹具(d, 项目gi=GITIGNORE.replace("*.sqlite3", "*.db")))
        self.assertIn("11 页 .gitignore 的模式序列 = 真实文件", 失败项(记))

    def test_gitignore_顺序不同要报(self):
        """`!` 反向放行必须排在对应忽略规则之后,顺序在这里是有意义的。"""
        with tempfile.TemporaryDirectory() as d:
            乱 = "__pycache__/\n.ruff_cache/\nmyshop.db\n*.sqlite3\n"
            记 = 跑(夹具(d, 项目gi=乱))
        self.assertIn("11 页 .gitignore 的模式序列 = 真实文件", 失败项(记))

    def test_行尾注释要报_两侧都查(self):
        """这条是那个真 bug 的回归锁:哪一侧写回去都要红。"""
        坏 = GITIGNORE.replace("myshop.db\n", "myshop.db          # 跑测试会生成它\n")
        with tempfile.TemporaryDirectory() as d:
            记 = 跑(夹具(d, 教程gi=坏, 项目gi=坏))
        名 = 失败项(记)
        self.assertIn("11 页那份 里没有行尾注释(那是失效写法)", 名)
        self.assertIn("真实 .gitignore 里没有行尾注释(那是失效写法)", 名)

    # ---------- 不许静默通过 ----------

    def test_02页找不到代码块要报(self):
        with tempfile.TemporaryDirectory() as d:
            记 = 跑(夹具(d, 建02=False))
        self.assertIn("02 页第五节还能找到那份 pyproject.toml", 失败项(记))

    def test_11页找不到代码块要报(self):
        with tempfile.TemporaryDirectory() as d:
            记 = 跑(夹具(d, 建11=False))
        self.assertIn("11 页第四节还能找到那份 .gitignore", 失败项(记))

    def test_项目缺文件要报(self):
        with tempfile.TemporaryDirectory() as d:
            f = 夹具(d)
            Path(f.ms, "pyproject.toml").unlink()
            Path(f.ms, ".gitignore").unlink()
            记 = 跑(f)
        名 = 失败项(记)
        self.assertIn("配套项目有 pyproject.toml", 名)
        self.assertIn("配套项目有 .gitignore", 名)

    def test_教程那份TOML写坏了要报而不是崩(self):
        with tempfile.TemporaryDirectory() as d:
            记 = 跑(夹具(d, 教程py=PYPROJECT + "\n这行不是合法 TOML\n"))
        self.assertIn("两份 pyproject.toml 都解析得动", 失败项(记))


HELPER = '''"""模块 docstring。"""

import pytest


@pytest.mark.p0
def test_甲(x: int) -> None:
    """函数 docstring。"""
    # 一句注释
    assert x == 1


def test_乙() -> None:
    assert True
'''


class 代码块夹具:
    """假的 ai-workflow/(几页 HTML)+ 假的 myshop/(几个真文件)。"""

    def __init__(self, 目录, 页面, 文件):
        self.dir = Path(目录)
        aw, ms = self.dir / "aw", self.dir / "ms"
        aw.mkdir(parents=True, exist_ok=True)
        for 名, 内容 in 页面.items():
            (aw / 名).write_text(内容, encoding="utf-8")
        for rel, 内容 in 文件.items():
            p = ms / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(内容, encoding="utf-8")
        self.aw, self.ms = str(aw), str(ms)


def 代码块页(头, 体):
    return "<html><pre><code>" + 头 + "\n" + body_escape(体) + "</code></pre></html>"


def body_escape(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def 跑块(f, 哪个="块"):
    记 = []

    def check(名, 期望, 实际):
        记.append((名, 期望, 实际, 期望 == 实际))

    旧 = cr.AW
    cr.AW = f.aw
    try:
        (cr.检查照抄的代码块 if 哪个 == "块" else cr.检查跳过行的行号)(f.ms, check)
    finally:
        cr.AW = 旧
    return 记


class TestCodeBlocks(unittest.TestCase):
    """第 7 节:教程贴出来的「文件」 ↔ 配套项目里的真文件。"""

    def test_一致时不报(self):
        with tempfile.TemporaryDirectory() as d:
            f = 代码块夹具(d, {"07-x.html": 代码块页("# 文件:tests/t.py", HELPER)},
                          {"tests/t.py": HELPER})
            记 = 跑块(f)
        self.assertTrue(记)
        self.assertEqual([], 失败项(记), 记)

    def test_少一行可执行代码要报(self):
        """C2 的回归锁:07 页那块当年就是少了一行 @pytest.mark.p0。"""
        with tempfile.TemporaryDirectory() as d:
            少 = HELPER.replace("@pytest.mark.p0\n", "")
            f = 代码块夹具(d, {"07-x.html": 代码块页("# 文件:tests/t.py", 少)},
                          {"tests/t.py": HELPER})
            记 = 跑块(f)
        self.assertTrue(any("逐行相等" in n for n in 失败项(记)), 失败项(记))

    def test_注释与docstring不同不该报(self):
        with tempfile.TemporaryDirectory() as d:
            改 = (HELPER.replace('"""模块 docstring。"""', '"""换个说法。"""')
                       .replace("# 一句注释", "# 完全不同的注释")
                       .replace('"""函数 docstring。"""', '"""去术语化的说法。"""'))
            f = 代码块夹具(d, {"07-x.html": 代码块页("# 文件:tests/t.py", 改)},
                          {"tests/t.py": HELPER})
            记 = 跑块(f)
        self.assertEqual([], 失败项(记), 记)

    def test_节选里出现真实文件没有的代码要报(self):
        """探路时真抓到过:07 页 test_api 节选把类型标注删了,于是不是子序列。"""
        with tempfile.TemporaryDirectory() as d:
            改 = HELPER.replace("def test_甲(x: int) -> None:", "def test_甲(x):")
            f = 代码块夹具(d, {"07-x.html": 代码块页("# 文件:tests/t.py (节选)", 改)},
                          {"tests/t.py": HELPER})
            记 = 跑块(f)
        self.assertTrue(any("按序" in n for n in 失败项(记)), 失败项(记))

    def test_节选少东西不报(self):
        with tempfile.TemporaryDirectory() as d:
            少 = HELPER.replace("def test_乙() -> None:\n    assert True\n", "")
            f = 代码块夹具(d, {"07-x.html": 代码块页("# 文件:tests/t.py (节选)", 少)},
                          {"tests/t.py": HELPER})
            记 = 跑块(f)
        self.assertEqual([], 失败项(记), 记)

    def test_diff块不当文件比(self):
        with tempfile.TemporaryDirectory() as d:
            diff = "- return 1\n+ return 2\n"
            f = 代码块夹具(d, {"08-x.html": 代码块页("# 文件:tests/t.py", diff)},
                          {"tests/t.py": HELPER})
            记 = 跑块(f)
        # diff 块不比对,但「扫到了代码块」那条仍要通过
        self.assertEqual([], 失败项(记), 记)

    def test_找不到真实文件要报(self):
        with tempfile.TemporaryDirectory() as d:
            f = 代码块夹具(d, {"07-x.html": 代码块页("# 文件:tests/查无此人.py", HELPER)},
                          {"tests/t.py": HELPER})
            记 = 跑块(f)
        self.assertTrue(any("找得到" in n for n in 失败项(记)), 失败项(记))

    def test_解析失败要报而不是崩(self):
        with tempfile.TemporaryDirectory() as d:
            f = 代码块夹具(d, {"07-x.html": 代码块页("# 文件:tests/t.py", "def 坏(:\n")},
                          {"tests/t.py": HELPER})
            记 = 跑块(f)
        self.assertTrue(any("解析得动" in n for n in 失败项(记)), 失败项(记))

    def test_一块都没扫到要报(self):
        """头行写法一改、正则失配,旧写法会一声不吭地全绿。"""
        with tempfile.TemporaryDirectory() as d:
            f = 代码块夹具(d, {"07-x.html": "<html>没有任何代码块</html>"},
                          {"tests/t.py": HELPER})
            记 = 跑块(f)
        self.assertTrue(any("扫到了" in n for n in 失败项(记)), 失败项(记))


E2E = '''import pytest

# 这一行是注释,里面故意写了 importorskip 这个词
pytest.importorskip(
    "playwright.sync_api",
    reason="E2E 需要",
)
'''


class TestSkipLineNo(unittest.TestCase):
    """第 7b 节:SKIPPED 行号 ↔ importorskip 的真实行号。"""

    def 造(self, d, 行号):
        return 代码块夹具(
            d,
            {"07-x.html": "<html><pre>SKIPPED [1] tests/e2e/test_checkout_e2e.py:%d: E2E</pre></html>" % 行号},
            {"tests/e2e/test_checkout_e2e.py": E2E})

    def test_行号对时不报(self):
        with tempfile.TemporaryDirectory() as d:
            记 = 跑块(self.造(d, 4), "行号")      # ast:调用起于第 4 行
        self.assertEqual([], 失败项(记), 记)

    def test_行号过期要报(self):
        """C1 的回归锁:教程写 :23,而真实早就挪到 :28 了。"""
        with tempfile.TemporaryDirectory() as d:
            记 = 跑块(self.造(d, 23), "行号")
        self.assertTrue(失败项(记), 记)

    def test_不能被含关键词的注释骗走(self):
        """陷阱:第 3 行是含 importorskip 字样的注释,grep|head -1 会数成 3。"""
        with tempfile.TemporaryDirectory() as d:
            记 = 跑块(self.造(d, 3), "行号")
        self.assertTrue(失败项(记), "被注释那行骗走了")

    def test_真实文件缺失要报(self):
        with tempfile.TemporaryDirectory() as d:
            f = 代码块夹具(d, {"07-x.html": "<html></html>"}, {"别的.py": "x = 1\n"})
            记 = 跑块(f, "行号")
        self.assertTrue(失败项(记), 记)


if __name__ == "__main__":
    unittest.main()
