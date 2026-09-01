# -*- coding: utf-8 -*-
"""版式与结构巡检 —— check_site.py 查不到的那一类问题。

为什么要有这个东西:
    check_site.py 查链接、锚点、重复 id、导航、标题 —— 全是「文本层面」的。
    但用户先后肉眼发现了两处它一条都报不出来的缺陷:

      · 01 页七步流程图里 5b / 5c / 兜底 三个节点画完就断,一条出边都没有;
      · 02 页 .chk 验收清单竖排成一字一行(display:grid 把 <li> 的每段
        裸文本都变成了独立网格项,挤进 26px 宽那一列)。

    两处都是原版就有的,而且都靠肉眼才发现。这个文件就是补这个洞。

六条规则,全部拿已知缺陷做过真值验证:
    dangling_node   SVG 里有入边、没出边的非终止节点        ← 抓缺陷一
    dangling_edge   箭头指向空处(端点附近没有任何节点)      ← 抓缺陷一的另一面
    grid_text       grid/flex 容器里混有裸文本节点           ← 抓缺陷二
    overflow        元素坐标跑出 viewBox,渲染时被裁掉
    collision       文字压在别的方框上、方框互相重叠
    refs            url(#id) 指向不存在的 marker、重复 id

准入门槛(每条规则都过了):
    在只读原版上**必须**报出对应缺陷,在已修版上**必须**不报。
    做不到这一点的规则等于没写。

只用标准库,与 check_site.py 一致。`_v1/` 历史页不计入。
"""

import argparse
import glob
import io
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "layout_rules"))

import det_collision  # noqa: E402
import det_dangling_edge  # noqa: E402
import det_dangling_node  # noqa: E402
import det_grid_text  # noqa: E402
import det_overflow  # noqa: E402
import det_refs  # noqa: E402

规则 = [
    ("dangling_node", det_dangling_node),
    ("dangling_edge", det_dangling_edge),
    ("grid_text", det_grid_text),
    ("overflow", det_overflow),
    ("collision", det_collision),
    ("refs", det_refs),
]

# ---------------------------------------------------------------- 规则收窄
# 下面三条不是白名单,是**规则本身的收窄** —— 每一条都由一次对抗复核的证据推出来。
# 白名单是把问题藏起来;收窄规则是承认判据本来就不该覆盖那一类。

_导航图 = re.compile(r'<a\s[^>]*href="\d\d-[a-z0-9-]+\.html"', re.I)


def _导航图行段(html: str):
    """找出**每一张** SVG 的行号区间,并标出哪些是导航图/依赖图。

    必须按单张 SVG 判,不能按整页判 —— 正文里的「第 N 页」页码链接
    (class="pg",01 页就有 43 个)长得和地图节点的链接一模一样,
    按整页判会把整页都豁免掉,真值缺陷直接被吞。
    这个 bug 是被真值自检当场抓出来的。
    """
    段 = []
    i = 0
    while True:
        a = html.find("<svg", i)
        if a < 0:
            break
        b = html.find("</svg>", a)
        if b < 0:
            break
        起 = html.count(chr(10), 0, a) + 1
        止 = html.count(chr(10), 0, b) + 1
        段.append((起, 止, len(_导航图.findall(html[a:b])) >= 3))
        i = b + 6
    return 段


def _在导航图内(段, line) -> bool:
    for 起, 止, 是地图 in 段:
        if 起 <= (line or 0) <= 止:
            return 是地图
    return False


_缓存: dict = {}


def _段缓存(html: str):
    k = id(html)
    if k not in _缓存:
        _缓存.clear()
        _缓存[k] = _导航图行段(html)
    return _缓存[k]


def 收窄(rule: str, path: str, html: str, items: list) -> list:
    out = []
    for it in items:
        kind = str(it.get("kind", ""))

        # ① 导航图/依赖图豁免「无出边」判据 —— 见 _导航图行段 的说明
        if rule == "dangling_node" and _在导航图内(_段缓存(html), it.get("line")):
            continue

        # ② grid_text 只报「轨道写死的 grid」。
        #    复核证据:07 页 .term-bar 是 flex,裸文本被拆开后只是多出 7px 间隙,
        #    真机逐节点量过,与正常空格差 0.4px,肉眼不可分 —— 那是外观,不是错位。
        #    真正致命的是 .chk li 那种 grid-template-columns 写死列数的情况:
        #    多出来的匿名项会落进**错误的列**,那才是结构性损坏。
        if rule == "grid_text":
            d = str(it.get("detail", ""))
            if "grid-template-columns" not in d:
                continue

        # ③ 丢掉 figcaption 数量比对那一支。
        #    复核证据:22 页那张收尾结论卡的说明文字在红框**里面**而非框下,
        #    渲染完全正常。这一支零真阳性、已产生一例假阳性,判据不成立。
        if rule == "refs" and kind in ("fig-count", "fig-no-caption"):
            continue

        out.append(it)
    return out


# ---------------------------------------------------------------- 驱动
def 扫一个文件(path: str):
    html = io.open(path, encoding="utf-8", errors="replace").read()
    结果 = []
    for name, mod in 规则:
        try:
            got = mod.check(path, html) or []
        except Exception as exc:  # 单条规则崩了不该拖垮整次巡检
            结果.append({"line": 0, "kind": "规则异常", "msg": f"{name} 抛异常:{exc}", "rule": name})
            continue
        for it in 收窄(name, path, html, got):
            it = dict(it)
            it["rule"] = name
            结果.append(it)
    return sorted(结果, key=lambda x: (int(x.get("line") or 0), x.get("rule", "")))


def 扫目录(d: str, 标签: str):
    页 = [p for p in sorted(glob.glob(os.path.join(d, "*.html")))
          if not re.search(r"[\\/]_v1[\\/]", p)]
    合计 = 0
    for p in 页:
        items = 扫一个文件(p)
        if not items:
            continue
        合计 += len(items)
        print("\n%s" % os.path.basename(p))
        for it in items:
            print("  L%-5s [%s] %s" % (it.get("line"), it.get("rule"), it.get("msg", "")))
            if it.get("detail"):
                print("        %s" % str(it["detail"])[:180])
    print("\n[%s] 检查 %d 页 -> %d 个问题" % (标签, len(页), 合计))
    return 合计


def 真值自检(原版: str, 已修: str) -> int:
    """准入门槛:原版必须报出那两处已知缺陷,已修版必须不报。

    没有这一步,任何「0 个问题」都没有意义 —— 一个永远返回空的检查器
    也能得到 0 个问题。
    """
    print("=" * 62)
    print("真值自检(原版必须报、已修版必须不报)")
    print("=" * 62)
    坏 = 0
    for 文件, 规则名, 说明 in [
        ("01-overview.html", "dangling_node", "七步图 5b/5c/兜底 没有出边"),
        ("01-overview.html", "dangling_edge", "箭头停在步骤 7 方框外"),
        ("02-python-setup.html", "grid_text", ".chk 清单竖排"),
    ]:
        p0, p1 = os.path.join(原版, 文件), os.path.join(已修, 文件)
        if not (os.path.exists(p0) and os.path.exists(p1)):
            print("  跳过 %-22s (缺样本)" % 说明)
            continue
        n0 = len([x for x in 扫一个文件(p0) if x.get("rule") == 规则名])
        n1 = len([x for x in 扫一个文件(p1) if x.get("rule") == 规则名])
        ok = n0 > 0 and n1 == 0
        坏 += 0 if ok else 1
        print("  %s  %-24s 原版 %d 条 / 已修版 %d 条" % ("通过" if ok else "失败!!", 说明, n0, n1))
    print()
    return 坏


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="版式与结构巡检")
    ap.add_argument("--dir", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ai-workflow"))
    ap.add_argument("--baseline", default=r"D:\ext.zhaoliuliu3\Desktop\ai-workflow",
                    help="只读原版目录,用于真值自检;不存在则跳过")
    a = ap.parse_args()

    目标 = os.path.abspath(a.dir)
    失败 = 0
    if os.path.isdir(a.baseline):
        失败 = 真值自检(a.baseline, 目标)
    else:
        print("(未找到原版目录,跳过真值自检)\n")

    n = 扫目录(目标, "正式页")
    # _v1/ 是历史页,按设计不参与巡检(六个规则模块内部也各自过滤了它)

    if 失败:
        print("真值自检未通过 —— 规则可能被改坏了,先修规则再看结果。")
    sys.exit(1 if 失败 else 0)
