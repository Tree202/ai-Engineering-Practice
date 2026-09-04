# -*- coding: utf-8 -*-
"""check_fingerprints.py 的合成夹具测试。

它守的是 `.gitattributes` 里那条写明的契约:「成品文件的字节必须与变更清单里的
SHA-256 一致」。2026-09-04 之前没有任何东西核它,于是一轮改完 15 行指纹全过期、
七道门禁照样全绿。

这份夹具重点覆盖三类,**后两类是这个仓库反复栽过的那一族**:
  ① 指纹对不上 / 覆盖不全 —— 检查器该报的
  ② **表解析不出来时不许报绿** —— 正则失配、表被截断,必须退 2 而不是退 0
  ③ **拿不到 git 跟踪清单时不许报绿** —— 覆盖检查做不了就不能算做过

变异验证(改完请手工确认一次):把 run() 改成 `return 0`,下面除 test_干净 外全红。

用法:  python -m unittest discover -s tools/tests
"""

import contextlib
import hashlib
import importlib.util
import io as _io
import os
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "check_fingerprints_under_test", TOOLS / "check_fingerprints.py")
cf = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = cf
_spec.loader.exec_module(cf)


def sha(b):
    return hashlib.sha256(b).hexdigest().upper()


class 夹具:
    """一个假仓库:几个成品文件 + 一份带指纹表的变更清单。"""

    def __init__(self, 目录, 文件, 跟踪=None, 表=None):
        self.dir = Path(目录)
        self.文件 = {}
        for rel, 内容 in 文件.items():
            p = self.dir / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(内容.encode("utf-8"))
            self.文件[rel] = 内容.encode("utf-8")
        行 = 表 if 表 is not None else [
            (rel, sha(b)) for rel, b in self.文件.items()]
        md = ["# 变更清单", "", "## 附录:指纹", "",
              "| 文件 | 旧 | SHA-256 |", "|---|---|---|"]
        md += ["| %s | — | %s |" % (rel, h) for rel, h in 行]
        md += ["", "注:变更清单.md 自身除外。", ""]
        (self.dir / "变更清单.md").write_text("\n".join(md), encoding="utf-8")
        self.跟踪 = set(跟踪) if 跟踪 is not None else set(self.文件) | {"变更清单.md"}


@contextlib.contextmanager
def 装上(f, 最少行数=2):
    """把模块的三个外部依赖换成夹具:仓库根、清单路径、git 跟踪清单。"""
    旧 = (cf.ROOT, cf.清单, cf.跟踪清单, cf.最少行数)
    cf.ROOT = str(f.dir)
    cf.清单 = str(f.dir / "变更清单.md")
    cf.跟踪清单 = (lambda: None) if f.跟踪 is None else (lambda: set(f.跟踪))
    cf.最少行数 = 最少行数
    try:
        yield
    finally:
        cf.ROOT, cf.清单, cf.跟踪清单, cf.最少行数 = 旧


def 跑(f, 最少行数=2, fix=False):
    buf = _io.StringIO()
    with 装上(f, 最少行数), contextlib.redirect_stdout(buf):
        码 = cf.run(fix=fix)
    return 码, buf.getvalue()


基本 = {"a.py": "print(1)\n", "b/c.md": "文档\n", "d.js": "var x=1\n"}


class TestFingerprints(unittest.TestCase):

    def test_干净时退0(self):
        with tempfile.TemporaryDirectory() as d:
            码, 出 = 跑(夹具(d, 基本))
        self.assertEqual(0, 码, 出)
        self.assertIn("完全相符", 出)

    def test_指纹过期要退1(self):
        with tempfile.TemporaryDirectory() as d:
            f = 夹具(d, 基本)
            (f.dir / "a.py").write_text("print(2)\n", encoding="utf-8")   # 改了字节,没改表
            码, 出 = 跑(f)
        self.assertEqual(1, 码, 出)
        self.assertIn("a.py", 出)

    def test_表里有行磁盘没文件要退1(self):
        with tempfile.TemporaryDirectory() as d:
            f = 夹具(d, 基本)
            os.remove(f.dir / "d.js")
            f.跟踪.discard("d.js")
            码, 出 = 跑(f)
        self.assertEqual(1, 码, 出)
        self.assertIn("磁盘上没这个文件", 出)

    def test_跟踪了却没登记要退1(self):
        """新增成品文件、忘了登记 —— 只比对已登记行的话,这里会静默全绿。"""
        with tempfile.TemporaryDirectory() as d:
            f = 夹具(d, 基本)
            (f.dir / "新来的.py").write_text("x=1\n", encoding="utf-8")
            f.跟踪.add("新来的.py")
            码, 出 = 跑(f)
        self.assertEqual(1, 码, 出)
        self.assertIn("没登记进指纹表", 出)

    def test_登记了却没被跟踪要退1(self):
        """表里留着一条 git 根本不跟踪的路径(比如别的仓库里的文件)。"""
        with tempfile.TemporaryDirectory() as d:
            f = 夹具(d, 基本)
            f.跟踪.discard("d.js")
            码, 出 = 跑(f)
        self.assertEqual(1, 码, 出)
        self.assertIn("git 没跟踪", 出)

    # ---------- 下面两条是「不许静默通过」的锁 ----------

    def test_表解析不出来要退2而不是退0(self):
        with tempfile.TemporaryDirectory() as d:
            f = 夹具(d, 基本)
            码, 出 = 跑(f, 最少行数=99)          # 模拟正则失配 / 表被截断
        self.assertEqual(2, 码, 出)
        self.assertIn("少于下限", 出)

    def test_找不到清单要退2(self):
        with tempfile.TemporaryDirectory() as d:
            f = 夹具(d, 基本)
            os.remove(f.dir / "变更清单.md")
            码, 出 = 跑(f)
        self.assertEqual(2, 码, 出)

    def test_拿不到git清单要退2(self):
        with tempfile.TemporaryDirectory() as d:
            f = 夹具(d, 基本)
            f.跟踪 = None                        # git 不可用
            码, 出 = 跑(f)
        self.assertEqual(2, 码, 出)
        self.assertIn("做不了就不能算过", 出)

    # ---------- --fix ----------

    def test_fix能改正也能补登记(self):
        with tempfile.TemporaryDirectory() as d:
            f = 夹具(d, 基本)
            (f.dir / "a.py").write_text("print(2)\n", encoding="utf-8")
            (f.dir / "新来的.py").write_text("x=1\n", encoding="utf-8")
            f.跟踪.add("新来的.py")
            码, _ = 跑(f, fix=True)
            self.assertEqual(1, 码)              # --fix 本次仍报非 0,要求重跑确认
            码2, 出2 = 跑(f)                      # 重跑应干净
        self.assertEqual(0, 码2, 出2)


if __name__ == "__main__":
    unittest.main()
