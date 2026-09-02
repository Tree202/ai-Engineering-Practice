# -*- coding: utf-8 -*-
"""版式与结构巡检 —— check_site.py 查不到的那一类问题。

为什么要有这个东西:
    check_site.py 查链接、锚点、重复 id、导航、标题 —— 全是「文本层面」的。
    但用户先后肉眼发现了两处它一条都报不出来的缺陷:

      · 01 页七步流程图里 5b / 5c / 兜底 三个节点画完就断,一条出边都没有;
      · 02 页 .chk 验收清单竖排成一字一行(display:grid 把 <li> 的每段
        裸文本都变成了独立网格项,挤进 26px 宽那一列)。

    两处都是原版就有的,而且都靠肉眼才发现。这个文件就是补这个洞。

六条规则(真值验证覆盖:3 条拿已知缺陷做门禁、1 条合成变异、2 条无真值,见 layout_rules/README):
    dangling_node   SVG 里有入边、没出边的非终止节点        ← 抓缺陷一
    dangling_edge   箭头指向空处(端点附近没有任何节点)      ← 抓缺陷一的另一面
    grid_text       grid/flex 容器里混有裸文本节点           ← 抓缺陷二
    overflow        元素坐标跑出 viewBox,渲染时被裁掉
    collision       文字压在别的方框上、方框互相重叠
    refs            url(#id) 指向不存在的 marker、重复 id

准入门槛(适用于有真值样本的三条):
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

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

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


_节点 = re.compile(r'<rect[^>]*?x="([\d.]+)"[^>]*?y="([\d.]+)"[^>]*?width="([\d.]+)"[^>]*?height="([\d.]+)"')


def _终点列(html: str, line, detail: str) -> bool:
    """左右流向的图里,终点在最右一列 —— 而 dangling_node 的「终点=最下一排」
    判据假设自上而下,对这类图必然误报。

    证据(第三轮用户发现 kb/06 文字溢出、顺带把 kb 纳入巡检后暴露):
      · kb/05 决策树:右列 x=596~708 共 4 个节点,全是答案终点
      · kb/06:右列 x=562~722 共 2 个「只回传最终结果」,都是终点
      · 而 01 页原版真值样本里的「兜底」虽也在最右,那一列**只有它自己**

    所以判据是:节点位于最右一列,且**该列有 2 个以上节点**。
    单独一个节点在最右不算 —— 那正是真值样本要抓的漏画回程边。
    """
    import re as _re
    m = _re.search(r"x=([\d.]+) y=([\d.]+) w=([\d.]+) h=([\d.]+)", detail or "")
    if not m:
        return False
    x2 = float(m.group(1)) + float(m.group(3))
    段 = _导航图行段(html)
    for 起, 止, _ in 段:
        if not (起 <= (line or 0) <= 止):
            continue
        a = html.find("<svg", sum(len(x) + 1 for x in html.split(chr(10))[:起 - 1]) - 1)
        b = html.find("</svg>", a)
        节点 = [(float(g[0]) + float(g[2]), float(g[2]), float(g[3]))
                for g in _节点.findall(html[a:b])]
        节点 = [n for n in 节点 if n[1] >= 34 and n[2] >= 20]
        if not 节点:
            return False
        最右 = max(n[0] for n in 节点)
        同列 = [n for n in 节点 if abs(n[0] - 最右) <= 12]
        return abs(x2 - 最右) <= 12 and len(同列) >= 2
    return False


def 收窄(rule: str, path: str, html: str, items: list) -> list:
    out = []
    for it in items:
        kind = str(it.get("kind", ""))

        # ① 导航图/依赖图豁免「无出边」判据 —— 见 _导航图行段 的说明
        if rule == "dangling_node" and _在导航图内(_导航图行段(html), it.get("line")):
            continue

        # ①b 左右流向的图:最右一列若有 2 个以上节点,那是终点列 —— 见 _终点列
        if rule == "dangling_node" and _终点列(html, it.get("line"), it.get("detail", "")):
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


def _猜基线():
    """基线目录是只读原件,不随仓库发布。按约定找:
    仓库同级的 ai-workflow/ → 环境变量 AIWF_BASELINE → 都没有则由主流程报错退 2。
    此前这里写死作者机器的绝对路径,换台机器必然找不到。"""
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cand = os.path.join(os.path.dirname(_root), "ai-workflow")
    return cand if os.path.isdir(cand) else ""


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="版式与结构巡检")
    ap.add_argument("--dir", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ai-workflow"))
    ap.add_argument("--baseline", default=os.environ.get("AIWF_BASELINE") or _猜基线(),
                    help="只读原版目录,用于真值自检")
    ap.add_argument("--allow-missing-baseline", action="store_true",
                    help="基线不存在时继续巡检(退出码仍会标记未自检)")
    a = ap.parse_args()

    目标 = os.path.abspath(a.dir)
    自检失败 = 0
    缺基线 = not os.path.isdir(a.baseline)
    if 缺基线:
        print("!! 未找到原版目录 %s —— 真值自检没跑。" % a.baseline)
        print("   没有自检的「0 个问题」不可信:一个永远返回空的检查器也能得到它。")
        if not a.allow_missing_baseline:
            print("   要在无基线环境巡检,加 --allow-missing-baseline(退出码仍为 2)。")
            sys.exit(2)
    else:
        自检失败 = 真值自检(a.baseline, 目标)

    n = 扫目录(目标, "正式页")
    # _v1/ 是历史页,按设计不参与巡检(六个规则模块内部也各自过滤了它)

    # 姊妹课 kb/ 也有手写 SVG,此前从未被版式巡检覆盖 ——
    # 用户肉眼在 kb/06 发现一处文字溢出节点框,才暴露这个盲区。
    _kb = os.path.join(os.path.dirname(目标), "kb")
    if os.path.isdir(_kb):
        n += 扫目录(_kb, "姊妹课")

    if 自检失败:
        print("真值自检未通过 —— 规则可能被改坏了,先修规则再看结果。")
        sys.exit(1)
    if n:
        sys.exit(1)          # 巡检发现问题也要非零退出,否则不能当门禁用
    sys.exit(2 if 缺基线 else 0)
