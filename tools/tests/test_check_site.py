# -*- coding: utf-8 -*-
"""check_site.py 的合成夹具测试。

为什么要有独立测试(而不是像 det_*.py 那样只做内嵌自测):
    det_*.py 的自测靠的是「只读原版」这个真值样本 —— 那是现成的、带已知缺陷的对照物。
    check_site 没有这种对照物:它查的是链接、锚点、导航,而真实站点是**全绿**的,
    绿样本证明不了检查器在工作。所以只能反过来 —— 在临时目录里造一个小站点,
    **故意埋进每一类缺陷**,断言检查器逐个报出来。

    这一点很重要:2026-09-03 之前 check_site 有三个「它没在查它声称在查的东西」的
    缺陷(空目录报绿、跨目录锚点从未校验、导航检查名不副实),全都是靠人肉变异
    发现的。变异是一次性的,测试是常驻的。

只用标准库 unittest,不引入 pytest —— 教程仓库本身不装任何第三方包。
用法:  python -m unittest discover -s tools/tests
"""

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("check_site_under_test", TOOLS / "check_site.py")
check_site = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = check_site
_spec.loader.exec_module(check_site)


PAGES = ["00-index.html", "01-first.html", "02-second.html", "03-third.html"]


def page(i, extra="", pager=None, title=None):
    """造一页:带 pager 导航、唯一 title、一个可作锚点的 id。"""
    if pager is None:
        links = []
        if i > 0:
            links.append(f'<a href="{PAGES[i - 1]}">上一页</a>')
        links.append('<a href="00-index.html">目录</a>')
        if i + 1 < len(PAGES):
            links.append(f'<a href="{PAGES[i + 1]}">下一页</a>')
        pager = '<div class="pager">' + "".join(links) + "</div>"
    t = title or f"第 {i} 页"
    return (f'<!doctype html><html><head><title>{t}</title></head><body>'
            f'<h2 id="s1">一节</h2>{extra}{pager}</body></html>')


def build(root: Path, kb=True, index=True):
    """造一个最小站点:ai-workflow/ + index.html + kb/,结构与真实站点同形。"""
    aw = root / "ai-workflow"
    aw.mkdir(parents=True)
    for i, name in enumerate(PAGES):
        (aw / name).write_text(page(i), encoding="utf-8")
    if index:
        (root / "index.html").write_text(
            '<!doctype html><html><head><title>总入口</title></head><body>'
            '<a href="ai-workflow/01-first.html#s1">进主线</a></body></html>',
            encoding="utf-8")
    if kb:
        k = root / "kb"
        k.mkdir()
        for i, name in enumerate(["00-index.html", "01-a.html"]):
            links = ['<a href="01-a.html">下一页</a>'] if i == 0 else ['<a href="00-index.html">上一页</a>']
            (k / name).write_text(
                f'<!doctype html><html><head><title>kb 第 {i} 页</title></head><body>'
                f'<h2 id="s1">一节</h2><div class="pager">{"".join(links)}</div></body></html>',
                encoding="utf-8")
    return aw


class Base(unittest.TestCase):
    def site(self, **kw):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        aw = build(root, **kw)
        return root, aw

    def run_it(self, aw):
        """跑一遍,返回 (退出码, 全部输出)。"""
        import io as _io
        import contextlib
        buf = _io.StringIO()
        argv = sys.argv
        sys.argv = ["check_site.py", str(aw)]
        try:
            with contextlib.redirect_stdout(buf):
                rc = check_site.main()
        finally:
            sys.argv = argv
        return rc, buf.getvalue()


class 正常站点(Base):
    def test_干净站点应该全绿(self):
        _, aw = self.site()
        rc, out = self.run_it(aw)
        self.assertEqual(rc, 0, out)
        self.assertIn("RESULT: PASS", out)


class 缺陷一_空目录(Base):
    """这一条是本文件存在的首要理由:改名目录曾经能拿到 RESULT: PASS。"""

    def test_正式页目录整个消失要报错(self):
        root, aw = self.site()
        aw.rename(root / "ai-workflow-RENAMED")
        rc, out = self.run_it(aw)
        self.assertEqual(rc, 1, out)
        self.assertIn("一个页面都没有", out)

    def test_姊妹课目录整个消失要报错(self):
        root, aw = self.site()
        for f in (root / "kb").iterdir():
            f.unlink()
        rc, out = self.run_it(aw)
        self.assertEqual(rc, 1, out)

    def test_入口页缺失要报错(self):
        root, aw = self.site(index=False)
        rc, out = self.run_it(aw)
        self.assertEqual(rc, 1, out)
        self.assertIn("入口页缺失", out)


class 缺陷二_跨目录锚点(Base):
    """index.html → ai-workflow/xx.html#frag 这类锚点,曾经恒被跳过。"""

    def test_跨目录锚点不存在要报错(self):
        root, aw = self.site()
        (root / "index.html").write_text(
            '<!doctype html><html><head><title>总入口</title></head><body>'
            '<a href="ai-workflow/01-first.html#NOPE">进主线</a></body></html>',
            encoding="utf-8")
        rc, out = self.run_it(aw)
        self.assertEqual(rc, 1, out)
        self.assertIn("锚点不存在", out)

    def test_跨目录锚点存在时不该误报(self):
        _, aw = self.site()
        rc, out = self.run_it(aw)
        self.assertEqual(rc, 0, out)


class 缺陷三_导航(Base):
    """「导航三件套」曾经只是「页面上随便哪儿有个指向目录页的链接」。"""

    def test_pager_整块消失要报错(self):
        _, aw = self.site()
        (aw / "01-first.html").write_text(page(1, pager=""), encoding="utf-8")
        rc, out = self.run_it(aw)
        self.assertEqual(rc, 1, out)
        self.assertIn("找不到 pager", out)

    def test_缺下一页要报错(self):
        _, aw = self.site()
        (aw / "01-first.html").write_text(
            page(1, pager='<div class="pager">'
                          '<a href="00-index.html">上一页</a>'
                          '<a href="00-index.html">目录</a></div>'),
            encoding="utf-8")
        rc, out = self.run_it(aw)
        self.assertEqual(rc, 1, out)
        self.assertIn("缺下一页", out)

    def test_下一页指到隔壁页要报错(self):
        _, aw = self.site()
        (aw / "01-first.html").write_text(
            page(1, pager='<div class="pager">'
                          '<a href="00-index.html">上一页</a>'
                          '<a href="00-index.html">目录</a>'
                          '<a href="03-third.html">下一页</a></div>'),
            encoding="utf-8")
        rc, out = self.run_it(aw)
        self.assertEqual(rc, 1, out)
        self.assertIn("缺下一页", out)

    def test_pager_里没有回目录的链接要报错(self):
        _, aw = self.site()
        (aw / "01-first.html").write_text(
            page(1, pager='<div class="pager">'
                          '<a href="00-index.html">上一页</a>'
                          '<a href="02-second.html">下一页</a></div>'),
            encoding="utf-8")
        rc, out = self.run_it(aw)
        # 上一页恰好就是 00-index,所以这一条对第 1 页不成立 —— 换第 2 页试
        _, aw2 = self.site()
        (aw2 / "02-second.html").write_text(
            page(2, pager='<div class="pager">'
                          '<a href="01-first.html">上一页</a>'
                          '<a href="03-third.html">下一页</a></div>'),
            encoding="utf-8")
        rc2, out2 = self.run_it(aw2)
        self.assertEqual(rc2, 1, out2)
        self.assertIn("没有回目录页的链接", out2)


class 其余规则(Base):
    def test_断链要报错(self):
        _, aw = self.site()
        (aw / "01-first.html").write_text(page(1, '<a href="不存在.html">x</a>'), encoding="utf-8")
        rc, out = self.run_it(aw)
        self.assertEqual(rc, 1, out)
        self.assertIn("链接目标不存在", out)

    def test_页内锚点不存在要报错(self):
        _, aw = self.site()
        (aw / "01-first.html").write_text(page(1, '<a href="#nope">x</a>'), encoding="utf-8")
        rc, out = self.run_it(aw)
        self.assertEqual(rc, 1, out)
        self.assertIn("页内锚点不存在", out)

    def test_重复_id_要报错(self):
        _, aw = self.site()
        (aw / "01-first.html").write_text(page(1, '<p id="s1">又一个</p>'), encoding="utf-8")
        rc, out = self.run_it(aw)
        self.assertEqual(rc, 1, out)
        self.assertIn("重复 id", out)

    def test_缺_title_要报错(self):
        _, aw = self.site()
        (aw / "01-first.html").write_text(
            '<!doctype html><html><head></head><body><h2 id="s1">一节</h2>'
            '<div class="pager"><a href="00-index.html">上一页</a>'
            '<a href="00-index.html">目录</a>'
            '<a href="02-second.html">下一页</a></div></body></html>',
            encoding="utf-8")
        rc, out = self.run_it(aw)
        self.assertEqual(rc, 1, out)
        self.assertIn("缺少 <title>", out)

    def test_title_重复要报错(self):
        _, aw = self.site()
        (aw / "01-first.html").write_text(page(1, title="第 2 页"), encoding="utf-8")
        rc, out = self.run_it(aw)
        self.assertEqual(rc, 1, out)
        self.assertIn("title 重复", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
