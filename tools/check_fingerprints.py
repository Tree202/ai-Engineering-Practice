# -*- coding: utf-8 -*-
"""指纹登记表巡检 —— 变更清单附录那张 SHA-256 表,必须和磁盘上的字节对得上。

为什么要有这个东西:
    `.gitattributes` 里白纸黑字写着「这些是已完工的成品文件,字节必须与变更清单里的
    SHA-256 一致」,并据此禁掉了 git 的换行符转换。**这是一条写明的契约。**

    但在 2026-09-04 之前,**没有任何东西在核它**。后果当场兑现:那一轮改完 14 个
    成品文件、提交、推送,表里对应的 15 行(含 `tools/myshop.pin`)全部还是旧指纹 ——
    契约破了,七道门禁全绿,没有一处报警。

    这与本轮修掉的那一族是同一个病:
      · `check_layout` 缺样本时跳过、不计失败       -> 0 页扫完也退 0
      · `check_site` 整个 kb/ 目录消失,静默跳过
      · `build_zip --check` 在 CI 里每个文件都不存在 -> 全部 continue -> 打印「一致」
    共同点是**检查器没在查它以为在查的东西**。这一处更彻底:连检查器都没有。

    所以这个脚本自己首先要防住同一个坑 —— 见下面「不许静默通过」。

它查两件事:
    ① 表里每一行的 SHA-256,与磁盘上那个文件的真实字节一致;
    ② **覆盖完整**:表内条目 == git 跟踪的文件 − 下面 `不登记` 里那几个。
       只查①的话,新增一个成品文件却忘了登记,照样全绿 —— 那是同一个洞的另一半。

不许静默通过(这几条是这个脚本的重点,不是防御性编程):
    · 找不到变更清单、或表里一行都没解析出来      -> 退 2,不是退 0
    · 解析出的行数少于 `最少行数`                  -> 退 2(表被截断/正则失配)
    · 拿不到 git 跟踪清单                          -> 退 2(覆盖检查没法做,就别假装做了)
    任何一种「我没查成」都必须与「我查了、没问题」区分开。

用法:  python tools/check_fingerprints.py          # 只读巡检,CI 用这个
       python tools/check_fingerprints.py --fix    # 就地把过期/缺失的行改对

退出码:0 = 全部相符;1 = 有不符或覆盖不全;2 = 没查成(不当作通过)。
"""

import hashlib
import io
import os
import re
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
清单 = os.path.join(ROOT, "变更清单.md")

# 表行形如:  | 相对路径 | 任意 | 64 位十六进制 |
# 行尾要容一个 \r:本仓库的成品文件是 LF(.gitattributes 禁掉了 git 的换行符转换),
# 但别人在 Windows 上另存一次就可能变 CRLF —— 那时旧写法会解析出 **0 行**,
# 于是这个脚本报「表解析不出来」退 2 —— 不是报绿,但是个误报,而且很难查。
# 别图省事写成 `\|?$`(把收尾那根竖线变成可选):那会让半行残缺的表也算数。
行正则 = re.compile(r"^\| *([^|]+?) *\| *([^|]*?) *\| *([0-9A-Fa-f]{64}) *\|\r?$", re.M)

# 少于这个数就认为表没解析对(而不是「真的只有这么几行」)。
# 2026-09-04 实测 80 行;取一半做下限,既能挡住正则失配,又不会因正常增删而误报。
最少行数 = 40

# 跟踪了、但**故意**不登记的。每一条都要有理由 —— 这里不是白名单,是口径声明。
不登记 = {
    # 文件无法包含自己的最终指纹:算出来写进去,写进去指纹就变了。
    "变更清单.md",
}


def 读(p, 二进制=False):
    if 二进制:
        with io.open(p, "rb") as fh:
            return fh.read()
    with io.open(p, encoding="utf-8", newline="") as fh:
        return fh.read()


def 跟踪清单():
    """git 跟踪的文件。拿不到就返回 None —— 调用方据此退 2,不许当成空集合。

    core.quotePath=false:不然中文路径会被转义成 \\345\\276... 对不上表里的写法。
    """
    try:
        out = subprocess.run(
            ["git", "-C", ROOT, "-c", "core.quotePath=false", "ls-files"],
            capture_output=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return {L.strip() for L in out.stdout.decode("utf-8", "replace").split("\n") if L.strip()}


def run(fix=False):
    if not os.path.isfile(清单):
        print("!! 找不到变更清单:%s" % 清单)
        print("   指纹表就在它里面,拿不到就什么都没查 —— 退 2,不装作通过。")
        return 2

    文本 = 读(清单)
    行 = list(行正则.finditer(文本))
    if len(行) < 最少行数:
        print("!! 只从变更清单里解析出 %d 行指纹,少于下限 %d。" % (len(行), 最少行数))
        print("   要么表被截断了,要么表格式变了让正则失配 ——")
        print("   **正则零匹配时报绿**正是本仓库修过好几轮的那一族缺陷,所以这里退 2。")
        return 2

    print("=" * 66)
    print("指纹登记表巡检(变更清单附录 SHA-256 ↔ 磁盘字节)")
    print("=" * 66)

    不符, 缺文件, 新文本 = [], [], 文本
    for m in 行:
        rel, want = m.group(1), m.group(3).upper()
        p = os.path.join(ROOT, rel.replace("/", os.sep))
        if not os.path.isfile(p):
            缺文件.append(rel)
            print("  失败!!  表里有这一行,磁盘上没这个文件:%s" % rel)
            continue
        got = hashlib.sha256(读(p, 二进制=True)).hexdigest().upper()
        if got != want:
            不符.append(rel)
            print("  失败!!  %s" % rel)
            print("          表里 %s" % want)
            print("          实际 %s" % got)
            if fix:
                新文本 = 新文本.replace(m.group(0), m.group(0).replace(m.group(3), got))

    print("  逐行比对:%d 行,其中不符 %d、缺文件 %d" % (len(行), len(不符), len(缺文件)))

    # ---------- 覆盖:表内 == 跟踪文件 − 不登记 ----------
    跟踪 = 跟踪清单()
    if 跟踪 is None:
        print()
        print("!! 拿不到 git 跟踪清单(不在 git 仓库里,或 git 不可用)。")
        print("   覆盖检查做不了。**做不了就不能算过** —— 退 2。")
        return 2

    表内 = {m.group(1) for m in 行}
    应登记 = 跟踪 - 不登记
    漏登记 = sorted(应登记 - 表内)
    多登记 = sorted(表内 - 跟踪)          # 登记了却没被 git 跟踪 = 路径写错或已删

    for rel in 漏登记:
        print("  失败!!  被 git 跟踪、却没登记进指纹表:%s" % rel)
    for rel in 多登记:
        print("  失败!!  登记了、但 git 没跟踪这个文件:%s" % rel)
        print("          (myshop/ 是独立仓库,指纹由它自己的 git 历史承担,不该出现在这张表里)")

    if fix and (不符 or 漏登记):
        for rel in 漏登记:
            got = hashlib.sha256(读(os.path.join(ROOT, rel.replace("/", os.sep)),
                                    二进制=True)).hexdigest().upper()
            末行 = 行[-1].group(0)
            新文本 = 新文本.replace(末行, 末行 + "\n| %s | — | %s |" % (rel, got), 1)
            行 = list(行正则.finditer(新文本))
        io.open(清单, "w", encoding="utf-8", newline="").write(新文本)
        print()
        print("已就地更新:改正 %d 行、补登记 %d 行。请重跑一次确认。" % (len(不符), len(漏登记)))
        return 1

    print("  覆盖检查:跟踪 %d 个文件,应登记 %d 个,漏 %d、多 %d"
          % (len(跟踪), len(应登记), len(漏登记), len(多登记)))

    print()
    if 不符 or 缺文件 or 漏登记 or 多登记:
        print("指纹表与磁盘不一致 %d 处。" % (len(不符) + len(缺文件) + len(漏登记) + len(多登记)))
        print("改完成品文件就要同步这张表 —— `python tools/check_fingerprints.py --fix` 可以代劳。")
        return 1
    print("指纹表与磁盘完全相符(%d 个文件),覆盖完整。" % len(行))
    return 0


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    return run(fix="--fix" in argv)


if __name__ == "__main__":
    sys.exit(main())
