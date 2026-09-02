"""站点自检脚本(仅标准库):检查教程站点的链接、锚点、重复 id、导航与标题。

用法:  python tools/check_site.py [站点目录]
默认站点目录为脚本上一级的 ai-workflow/。
_v1/ 与 legacy-v1/ 历史页单独报告,不计入正式页的错误统计。

2026-09-03 修了三个「检查器没在查它声称在查的东西」的缺陷:

  ① 空目录报绿。原来 site.glob("*.html") 在目录不存在或为空时返回空迭代器且不抛异常,
     于是四条规则全部空转、打印「检查 0 页 -> 0 个问题」、RESULT: PASS、退出 0。
     把 ai-workflow/ 改个名就是静默空操作。现在必备的组为空即报错。

  ② 跨目录锚点从未被检查。原来 scans.get(Path(target).name) 只在**当前这一组**里
     找目标页,而 index.html / kb/ / ai-workflow/ 是分三组扫的,所以
     index.html → ai-workflow/xx.html#anchor 这类链接的锚点恒被跳过。
     顺带还有个同名文件互相误认的隐患(只比 basename)。
     现在先把全站每个 HTML 的 id 建成一张按**真实路径**索引的表,再校验。

  ③ 「导航三件套」是空头支票。原来 nav_targets = [h for h in s.links] 只是把全部
     链接原样拷一份,判断退化成「页面任意位置有一个指向 00-index 的链接即可」——
     上一页/下一页缺失、pager 整块消失,全测不到。现在真去解析 <div class="pager">,
     并按页面顺序校验上一页/下一页指向正确的相邻页。
"""

import sys

try:  # Windows 默认 cp1252 控制台下,中文输出会直接崩
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
from html.parser import HTMLParser
from pathlib import Path


class PageScan(HTMLParser):
    """收集一页里的 id、链接、<title>,以及 <div class="pager"> 里的链接。"""

    def __init__(self) -> None:
        super().__init__()
        self.ids = []          # 出现过的所有 id(含重复)
        self.links = []        # 所有 <a href>
        self.pager = []        # 只有 pager 块里的 <a href>,顺序保留
        self.title = None
        self._in_title = False
        self._pager_depth = 0  # >0 表示当前在 pager 块内;用深度计数处理嵌套标签

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if "id" in d:
            self.ids.append(d["id"])
        if self._pager_depth:
            self._pager_depth += 1
        elif "pager" in (d.get("class") or "").split():
            self._pager_depth = 1
        if tag == "a" and "href" in d:
            self.links.append(d["href"])
            if self._pager_depth:
                self.pager.append(d["href"])
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if self._pager_depth:
            self._pager_depth -= 1

    def handle_data(self, data):
        if self._in_title:
            self.title = (self.title or "") + data.strip()


def build_id_index(root: Path) -> dict:
    """把全站每个 HTML 的 id 集合建成一张表,键是**解析后的真实路径**。

    必须全站建一张,不能按组建 —— 见文件头缺陷 ②。
    """
    index = {}
    for p in root.rglob("*.html"):
        try:
            s = PageScan()
            s.feed(p.read_text(encoding="utf-8", errors="replace"))
            index[p.resolve()] = set(s.ids)
        except OSError:
            continue
    return index


def scan_dir(site: Path, label: str, pages: list, id_index: dict,
             required: bool = False, nav_order: list = None,
             require_index_link: bool = True) -> int:
    """检查一组页面,返回错误数。

    required=True        这一组一个页面都没有就是错误(缺陷 ①)
    nav_order            给定时按此顺序做严格的上一页/下一页校验(缺陷 ③)
    require_index_link   pager 里是否必须有回目录的链接。
                         主线 23 页每页都有,姊妹课只在首尾有 —— 两课的导航约定不同,
                         所以这一条要能关掉,而相邻页校验对两课都适用。
    """
    errors = []

    # 0) 这一组不该是空的
    if required and not pages:
        errors.append(f"[{label}] 这一组一个页面都没有 —— 目录被改名/移动了?"
                      f"(找的是 {site})")
        print(f"[{label}] 检查 0 页 -> {len(errors)} 个问题")
        for e in errors:
            print("  ! " + e)
        return len(errors)

    scans = {}
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

    # 2) 本地链接与锚点(锚点走全站 id 表,跨目录也算数)
    for name, s in scans.items():
        for href in s.links:
            if href.startswith(("http://", "https://", "mailto:")) or href == "#":
                continue
            target, _, frag = href.partition("#")
            if target:
                tp = site / target
                if not tp.exists():
                    errors.append(f"{name}: 链接目标不存在 \"{href}\"")
                    continue
                if frag:
                    ids = id_index.get(tp.resolve())
                    if ids is None:
                        errors.append(f"{name}: 锚点目标没被扫到,无法校验 \"{href}\"")
                    elif frag not in ids:
                        errors.append(f"{name}: 锚点不存在 \"{href}\"")
            elif frag and frag not in s.ids:
                errors.append(f"{name}: 页内锚点不存在 \"#{frag}\"")

    # 3) 导航:真去看 <div class="pager"> 里到底链到了哪儿
    if nav_order:
        pos = {n: i for i, n in enumerate(nav_order)}
        for name, s in scans.items():
            if name not in pos:
                continue
            i = pos[name]
            got = [Path(h.partition("#")[0]).name for h in s.pager
                   if h and not h.startswith(("http://", "https://", "mailto:"))]
            if not got:
                errors.append(f"{name}: 找不到 pager 导航块(或块里一个链接都没有)")
                continue
            if require_index_link and "00-index.html" not in got:
                errors.append(f"{name}: pager 里没有回目录页的链接")
            if i > 0 and nav_order[i - 1] not in got:
                errors.append(f"{name}: pager 里缺上一页 \"{nav_order[i - 1]}\"")
            if i + 1 < len(nav_order) and nav_order[i + 1] not in got:
                errors.append(f"{name}: pager 里缺下一页 \"{nav_order[i + 1]}\"")

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
    root = site.parent
    id_index = build_id_index(root)

    formal = sorted(p for p in site.glob("*.html"))
    # 正式页的阅读顺序就是文件名顺序(00-…22-),pager 的上一页/下一页按它校验
    nav_order = [p.name for p in formal if p.name[:2].isdigit()]

    n = scan_dir(site, "正式页", formal, id_index, required=True, nav_order=nav_order)

    legacy = sorted(p for p in (site / "_v1").glob("*.html")) if (site / "_v1").exists() else []
    if legacy:
        scan_dir(site / "_v1", "_v1 历史页(不计入)", legacy, id_index)

    # 站点其余部分:总入口 + 姊妹课 + 第一版存档
    entry = root / "index.html"
    if entry.exists():
        n += scan_dir(root, "总入口", [entry], id_index)
    else:
        print("[总入口] index.html 不存在 -> 1 个问题")
        print("  ! 站点入口页缺失")
        n += 1

    kb = root / "kb"
    if kb.exists():
        kb_pages = sorted(kb.glob("*.html"))
        kb_order = [q.name for q in kb_pages if q.name[:2].isdigit()]
        n += scan_dir(kb, "姊妹课", kb_pages, id_index, required=True,
                      nav_order=kb_order, require_index_link=False)

    old = root / "legacy-v1"
    if old.exists():
        scan_dir(old, "第一版存档(逐字节存档,只报不计入)", sorted(old.glob("*.html")), id_index)

    print("RESULT:", "PASS" if n == 0 else f"FAIL({n})")
    return 0 if n == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
