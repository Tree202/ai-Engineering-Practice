# -*- coding: utf-8 -*-
"""把正文里的「第 N 页」变成可点链接。

读者真正迷路的时刻,是读到一半撞见「详见第 05 页」——那一刻需要的是就地一个跳转,
而不是回目录页找卡片。本脚本把这些文字引用改成链接。

安全边界(这些地方一律不动):
    <pre> <code>   终端输出与代码,注入 HTML 会破坏演示
    <a>            已经是链接,再套一层是非法嵌套
    <script> <style> <title> <svg>
    自指           第 05 页的正文里写「第 05 页」,链到自己没有意义
    非法页号       只有 01–22 是内容页

幂等:改完的数字已经在 <a> 里,再跑一次会被跳过。

用法:  python tools/link_pagerefs.py [--dry-run]
"""
import io
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from build_depmap import NODES  # noqa: E402

DIR = os.path.join(ROOT, "ai-workflow")
DRY = "--dry-run" in sys.argv

SKIP_TAG = re.compile(r"</?(pre|code|a|script|style|title|svg)\b", re.I)
TAG = re.compile(r"(<[^>]+>)")
# 「第 5 页」「第 05 页」「第 16、17 页」「第 17–19 页」「第 3、5、6 页」
REF = re.compile(r"第\s*\d{1,2}(?:\s*[、,，]\s*\d{1,2})*(?:\s*[–—\-~]\s*\d{1,2})?\s*页")
NUM = re.compile(r"\d{1,2}")


def link_text(text, self_page, stat):
    """把一段纯文本里的页码引用改成链接"""
    def fix_ref(m):
        def fix_num(n):
            p = n.group(0).zfill(2)
            if p not in NODES:                       # 不是内容页(如「共 23 页」里的数字)
                stat["非页号"] += 1
                return n.group(0)
            if p == self_page:                       # 自指
                stat["自指"] += 1
                return n.group(0)
            stat["已链接"] += 1
            return '<a class="pg" href="%s.html">%s</a>' % (NODES[p][0], n.group(0))
        return NUM.sub(fix_num, m.group(0))
    return REF.sub(fix_ref, text)


def process(path, self_page, stat):
    html = io.open(path, encoding="utf-8", newline="").read()
    out, depth = [], 0
    for tok in TAG.split(html):
        if tok.startswith("<"):
            m = SKIP_TAG.match(tok)
            if m:
                if tok.startswith("</"):
                    depth = max(0, depth - 1)
                elif not tok.rstrip().endswith("/>"):
                    depth += 1
            out.append(tok)
        else:
            out.append(tok if depth else link_text(tok, self_page, stat))
    new = "".join(out)
    if new != html and not DRY:
        io.open(path, "w", encoding="utf-8", newline="").write(new)
    return new != html


total = {"已链接": 0, "自指": 0, "非页号": 0}
touched = []
for name in ["00-index"] + [NODES[p][0] for p in sorted(NODES)]:
    path = os.path.join(DIR, name + ".html")
    self_page = name[:2] if name != "00-index" else "00"
    stat = {"已链接": 0, "自指": 0, "非页号": 0}
    changed = process(path, self_page, stat)
    for k in total:
        total[k] += stat[k]
    if stat["已链接"]:
        touched.append("%-26s +%d 个链接%s" % (
            name + ".html", stat["已链接"],
            "(自指跳过 %d)" % stat["自指"] if stat["自指"] else ""))

print("\n".join(touched))
print()
print("合计:改成链接 %d 处 · 自指跳过 %d 处 · 非页号跳过 %d 处"
      % (total["已链接"], total["自指"], total["非页号"]))
print("DRY-RUN,未写入" if DRY else "已写入")
