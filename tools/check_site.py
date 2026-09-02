"""站点自检脚本(仅标准库):检查 ai-workflow 教程的链接、锚点、重复 id、导航与标题。

用法:  python tools/check_site.py [站点目录]
默认站点目录为脚本上一级的 ai-workflow/。
_v1/ 历史页单独报告,不计入正式页的错误统计。
"""

import sys

try:  # Windows 默认 cp1252 控制台下,中文输出会直接崩
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
from html.parser import HTMLParser
from pathlib import Path


class PageScan(HTMLParser):
    """收集一页里的 id、链接和 <title>。"""

    def __init__(self) -> None:
        super().__init__()
        self.ids = []          # 出现过的所有 id(含重复)
        self.links = []        # 所有 <a href>
        self.title = None
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if "id" in d:
            self.ids.append(d["id"])
        if tag == "a" and "href" in d:
            self.links.append(d["href"])
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title = (self.title or "") + data.strip()


def scan_dir(site: Path, label: str, pages: list) -> int:
    """检查一组页面,返回错误数。"""
    scans = {}
    errors = []
    for p in pages:
        s = PageScan()
        s.feed(p.read_text(encoding="utf-8", errors="replace"))
        scans[p.name] = s

    # 1) 重复 id
    for name, s in scans.items():
        seen, dup = set(), set()
        for i in s.ids:
            (dup if i in seen else seen).add(i)
        for i in sorted(dup):
            errors.append(f"{name}: 重复 id \"{i}\"")

    # 2) 本地链接与锚点
    for name, s in scans.items():
        for href in s.links:
            if href.startswith(("http://", "https://", "mailto:")) or href == "#":
                continue
            target, _, frag = href.partition("#")
            if target:
                tp = (site / target)
                if not tp.exists():
                    errors.append(f"{name}: 链接目标不存在 \"{href}\"")
                    continue
                tscan = scans.get(Path(target).name)
                if frag and tscan is not None and frag not in tscan.ids:
                    errors.append(f"{name}: 锚点不存在 \"{href}\"")
            elif frag and frag not in s.ids:
                errors.append(f"{name}: 页内锚点不存在 \"#{frag}\"")

    # 3) 导航三件套(仅正式内容页,00-index 的上一页允许是 #)
    for name, s in scans.items():
        if not name[0].isdigit():
            continue
        nav_targets = [h for h in s.links]
        if "00-index.html" not in [Path(t.partition("#")[0]).name for t in nav_targets if t and not t.startswith("http")] and name != "00-index.html":
            errors.append(f"{name}: 未找到指向目录页的链接")

    # 4) title 存在且唯一
    titles = {}
    for name, s in scans.items():
        if not s.title:
            errors.append(f"{name}: 缺少 <title>")
        else:
            titles.setdefault(s.title, []).append(name)
    for t, names in titles.items():
        if len(names) > 1:
            errors.append(f"title 重复 \"{t}\": {', '.join(names)}")

    print(f"[{label}] 检查 {len(pages)} 页 -> {len(errors)} 个问题")
    for e in errors:
        print("  ! " + e)
    return len(errors)


def main() -> int:
    site = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "ai-workflow"
    formal = sorted(p for p in site.glob("*.html"))
    legacy = sorted(p for p in (site / "_v1").glob("*.html")) if (site / "_v1").exists() else []
    n = scan_dir(site, "正式页", formal)
    if legacy:
        scan_dir(site / "_v1", "_v1 历史页(不计入)", legacy)

    # 站点其余部分:总入口 + 姊妹课 + 第一版存档(此前这 14 个 HTML 零工具覆盖)
    root = site.parent
    if (root / "index.html").exists():
        n += scan_dir(root, "总入口", [root / "index.html"])
    if (root / "kb").exists():
        n += scan_dir(root / "kb", "姊妹课", sorted((root / "kb").glob("*.html")))
    if (root / "legacy-v1").exists():
        scan_dir(root / "legacy-v1", "第一版存档(逐字节存档,只报不计入)",
                 sorted((root / "legacy-v1").glob("*.html")))

    print("RESULT:", "PASS" if n == 0 else f"FAIL({n})")
    return 0 if n == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
