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


if __name__ == "__main__":
    unittest.main()
