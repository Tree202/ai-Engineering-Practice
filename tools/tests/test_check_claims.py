# -*- coding: utf-8 -*-
"""check_claims.py 的合成夹具测试。

它查的是「同一个事实全站只许有一种数」。夹具最贵的地方不是代码,是**标记复刻** ——
断言全靠正则去真实页面里捞数字,夹具的标记写得不像,正则全部落空,
而正则落空的后果恰恰是「一条断言都不产生、照样打印全部口径一致」。
所以这份夹具必须把页脚、卡片、总数表的标记逐字照抄真实页面的形状。

重点覆盖两类:
  ① 数字对不上 —— 检查器该报的
  ② **正则零匹配** —— 检查器曾经会静默放过的(2026-09-03 修复)

用法:  python -m unittest discover -s tools/tests
"""

import contextlib
import importlib.util
import io as _io
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("check_claims_under_test", TOOLS / "check_claims.py")
check_claims = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = check_claims
_spec.loader.exec_module(check_claims)          # 这一句本身就是回归测试:
                                                # 改造前它会因模块级 sys.exit 直接终止进程


def 正文页(n, 修正=None, 额外=""):
    """一页正文。有修正数时带页脚,格式与真实页面一致。"""
    foot = f'<div class="foot">本页含 {修正} 处经核实修正:…</div>' if 修正 else ""
    return (f'<!doctype html><html><head><title>第 {n} 页</title></head><body>'
            f'<h2 id="s1">一节</h2>{额外}{foot}</body></html>')


def 索引页(卡片, 合计, A, B, 卡片后缀=True):
    """00 页:总数表 + 卡片。卡片是 <a class="card" href="NN-x.html">…含 N 处修正</a>。"""
    cards = ""
    for pg, n in 卡片:
        lv = f'★★ · 知识点 1–9 · 含 {n} 处修正' if 卡片后缀 else '★★ · 知识点 1–9'
        cards += (f'<a class="card" href="{pg}-x.html">'
                  f'<div class="no">{pg} / x</div><div class="ti">标题</div>'
                  f'<div class="lv">{lv}</div></a>')
    return ('<!doctype html><html><head><title>目录</title></head><body>'
            f'<table><tr><td><strong>{A}</strong></td><td><strong>{B}</strong></td>'
            f'<td><strong>0</strong></td><td><strong>合计</strong></td></tr></table>'
            f'{cards}'
            f'<p>{合计} 处发现有误</p><p>{合计} 处发现有误</p>'
            '</body></html>').replace("<strong>合计</strong>", f"<strong>{合计}</strong>")


def _kb(n_list):
    """kb/:00 页声明总数与各卡片,01..0N 页各有若干 <h2 id="sN">。"""
    总 = sum(n_list)
    decl = f"<p>共 {len(n_list)} 章 · {总} 个知识点</p>"
    for n in n_list:
        decl += f"<div>{n} 个知识点</div>"
    idx = f'<!doctype html><html><head><title>kb 目录</title></head><body>{decl}</body></html>'
    pages = {}
    for i, n in enumerate(n_list, 1):
        hs = "".join(f'<h2 id="s{j}">节 {j}</h2>' for j in range(1, n + 1))
        pages[f"0{i}-x.html"] = (f'<!doctype html><html><head><title>kb {i}</title></head>'
                                 f'<body>{hs}</body></html>')
    return idx, pages


class Base(unittest.TestCase):
    def 造站(self, 页脚=((2, 3), (3, 4)), 卡片=None, 卡片后缀=True, kb=(2, 3)):
        """页脚 = [(页号, 修正数)];卡片默认与页脚一致。"""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        aw = root / "ai-workflow"
        aw.mkdir(parents=True)

        (aw / "00-index.html").write_text("PLACEHOLDER", encoding="utf-8")
        for pg, cnt in 页脚:
            (aw / f"{pg:02d}-x.html").write_text(正文页(pg, cnt), encoding="utf-8")
        # 让「17 passed / 19 passed」这两条允许存在的断言有落点
        (aw / "06-y.html").write_text(
            正文页(6, None, "<pre>17 passed, 1 skipped</pre><pre>19 passed</pre>"),
            encoding="utf-8")

        卡片 = 卡片 if 卡片 is not None else [(f"{p:02d}", c) for p, c in 页脚]
        合计 = sum(c for _, c in 页脚)
        A = sum(c for p, c in 页脚 if f"{p:02d}" <= "14")
        B = sum(c for p, c in 页脚 if f"{p:02d}" >= "15")
        (aw / "00-index.html").write_text(索引页(卡片, 合计, A, B, 卡片后缀), encoding="utf-8")

        k = root / "kb"
        k.mkdir()
        idx, pages = _kb(list(kb))
        (k / "00-index.html").write_text(idx, encoding="utf-8")
        for name, html in pages.items():
            (k / name).write_text(html, encoding="utf-8")
        return root

    def run_it(self, root):
        buf = _io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = check_claims.run_checks(str(root))
        return rc, buf.getvalue()


class 正常(Base):
    def test_数字全对时应该全平(self):
        rc, out = self.run_it(self.造站())
        self.assertEqual(rc, 0, out)
        self.assertIn("全部口径一致", out)

    def test_同一进程里跑两次结果一致(self):
        """改造前「失败」是模块级全局,第二次会累加上一次的结果。"""
        root = self.造站()
        rc1, _ = self.run_it(root)
        rc2, _ = self.run_it(root)
        self.assertEqual((rc1, rc2), (0, 0))


class 数字对不上(Base):
    def test_卡片数字与页脚不符要报错(self):
        root = self.造站(页脚=((2, 3), (3, 4)), 卡片=[("02", 3), ("03", 99)])
        rc, out = self.run_it(root)
        self.assertEqual(rc, 1, out)
        self.assertIn("00 页卡片 03", out)

    def test_kb_知识点总数不符要报错(self):
        root = self.造站(kb=(2, 3))
        kb0 = root / "kb" / "00-index.html"
        kb0.write_text(kb0.read_text(encoding="utf-8").replace("5 个知识点", "50 个知识点"),
                       encoding="utf-8")
        rc, out = self.run_it(root)
        self.assertEqual(rc, 1, out)
        self.assertIn("kb", out)


class 正则零匹配(Base):
    """这一类是本文件存在的首要理由:检查器曾经会静默放过。"""

    def test_卡片标记改掉导致零匹配要报错(self):
        root = self.造站()
        p = root / "ai-workflow" / "00-index.html"
        p.write_text(p.read_text(encoding="utf-8").replace("处修正", "处订正"), encoding="utf-8")
        rc, out = self.run_it(root)
        self.assertEqual(rc, 1, out)
        self.assertIn("至少匹配到一张修正卡片", out)

    def test_文件名带数字的页也要被卡片正则匹配到(self):
        """曾经的正则是 [a-z-]+,匹配不上 10-flaky-e2e.html 这种带数字的 slug。"""
        root = self.造站(页脚=((2, 3), (10, 4)), 卡片=[("02", 3), ("10", 4)])
        # 把 10 号页的文件名改成带数字的 slug,卡片 href 同步
        aw = root / "ai-workflow"
        (aw / "10-x.html").rename(aw / "10-flaky-e2e.html")
        p = aw / "00-index.html"
        p.write_text(p.read_text(encoding="utf-8").replace('href="10-x.html"', 'href="10-flaky-e2e.html"'),
                     encoding="utf-8")
        rc, out = self.run_it(root)
        self.assertEqual(rc, 0, out)
        self.assertIn("00 页卡片 10", out)   # 必须真的被检查到,而不是被正则漏掉

    def test_卡片指向没有页脚的页要报错(self):
        root = self.造站(页脚=((2, 3),), 卡片=[("02", 3), ("09", 5)])
        rc, out = self.run_it(root)
        self.assertEqual(rc, 1, out)
        self.assertIn("卡片指向的页都有页脚", out)


class 关键词残留(Base):
    def test_旧口径_18_条测试残留要报错(self):
        root = self.造站()
        p = root / "ai-workflow" / "02-x.html"
        p.write_text(p.read_text(encoding="utf-8").replace("一节</h2>", "一节</h2>它有 18 条测试"),
                     encoding="utf-8")
        rc, out = self.run_it(root)
        self.assertEqual(rc, 1, out)
        self.assertIn("18", out)

    def test_六道防线残留要报错(self):
        root = self.造站()
        p = root / "ai-workflow" / "02-x.html"
        p.write_text(p.read_text(encoding="utf-8").replace("一节</h2>", "一节</h2>六道防线"),
                     encoding="utf-8")
        rc, out = self.run_it(root)
        self.assertEqual(rc, 1, out)
        self.assertIn("六道防线", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
