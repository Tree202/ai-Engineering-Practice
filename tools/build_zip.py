# -*- coding: utf-8 -*-
"""白名单重打 myshop-source.zip —— 防止发布物再次漂移。

上一次漂移就是手工打包的后果:补了 web.py 之后 zip 没跟着重打,
下载的人拿不到第 4 层。此后 zip 一律由本脚本产出,白名单就是清单,
少一个文件直接报错,不会静默缺件。

用法:
    python tools/build_zip.py            重打
    python tools/build_zip.py --check    只校验,不写盘;发布物与 myshop/ 不一致就退 1

为什么需要 --check:
    2026-09-03 的全量复核发现,这个脚本要防的事故**又发生了一次** ——
    myshop 那边修完 Windows 问题(README 三处语法错误、两处编码崩溃)之后,
    zip 没跟着重打,14 个条目里 4 个过期(README.md / ci.yml / api.py / web.py)。
    读者按 index.html 那条路径拿到的,是修 Windows **之前**的版本。
    只有「重打」而没有「校验」,就只能靠人记得跑;有了 --check 才能挂进门禁。
"""

import hashlib
import os
import sys
import zipfile

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MS = os.path.join(ROOT, "myshop")
OUT = os.path.join(ROOT, "myshop-source.zip")

白名单 = [
    "pyproject.toml", ".gitignore", "README.md",
    ".github/workflows/ci.yml",
    "myshop/__init__.py", "myshop/price.py", "myshop/order.py", "myshop/api.py", "myshop/web.py",
    "tests/test_price.py", "tests/test_order.py", "tests/test_api.py",
    "tests/e2e/conftest.py", "tests/e2e/test_checkout_e2e.py",
]
禁止 = (".venv", "__pycache__", ".git/", ".db", ".pytest_cache", ".mypy_cache", ".ruff_cache")


def check_zip(ms: str = None) -> int:
    """只读校验:zip 里每个条目的内容,是否与 myshop/ 磁盘上的当前文件逐字节相同。

    这是门禁调用的入口 —— 它不写盘,只回答一个问题:
    「读者现在下载到的那个包,是不是仓库的当前状态?」
    """
    # myshop/ 被 .gitignore,CI 的 checkout 里根本没有它 —— 所以要能从命令行
    # 指到 clone 下来的那一份。**目录不在就退 2,不许静默说「一致」**:
    # 第一版没有这个分支,CI 里每个文件都 os.path.exists() 为假、全部 continue,
    # 最后打印「发布物与 myshop/ 一致」退 0 —— 我在修「静默报绿」这一族的过程中,
    # 又新造了一个同族缺陷。2026-09-03 加完门禁当场自查发现。
    ms = ms or MS
    if not os.path.isdir(ms):
        print("找不到配套项目目录,无法校验发布物:", ms)
        print("CI 里请传 clone 下来的那一份:python tools/build_zip.py --check _myshop")
        return 2
    if not os.path.exists(OUT):
        print("发布物不存在:", OUT)
        return 1
    坏 = []
    with zipfile.ZipFile(OUT) as z:
        names = set(z.namelist())
        期望 = {"myshop/" + rel for rel in 白名单}
        缺 = sorted(期望 - names)
        多 = sorted(names - 期望)
        for x in 缺:
            坏.append("zip 里缺条目:%s" % x)
        for x in 多:
            坏.append("zip 里多了条目:%s" % x)
        for rel in 白名单:
            名 = "myshop/" + rel
            src = os.path.join(ms, rel.replace("/", os.sep))
            if not os.path.exists(src):
                坏.append("配套项目里缺文件:%s" % rel)
                continue
            if 名 not in names:
                continue
            a = hashlib.sha256(z.read(名)).hexdigest()
            b = hashlib.sha256(open(src, "rb").read()).hexdigest()
            if a != b:
                坏.append("内容不一致:%s(zip %d 字节 / 磁盘 %d 字节)"
                          % (rel, z.getinfo(名).file_size, os.path.getsize(src)))
    if 坏:
        print("发布物已与 myshop/ 漂移 %d 处:" % len(坏))
        for x in 坏:
            print("  !", x)
        print("跑 python tools/build_zip.py 重打。")
        return 1
    print("发布物与 myshop/ 一致(%d 条目)。" % len(白名单))
    return 0


def main() -> int:
    if "--check" in sys.argv[1:]:
        其余 = [a for a in sys.argv[1:] if a != "--check"]
        return check_zip(其余[0] if 其余 else None)
    # 先写临时文件,校验通过后才原子改名顶掉旧包。
    # 旧写法直接写 OUT:少一个文件就 return 1,而那时旧包已经被清空了 ——
    # 「拒绝打包」实际留下的是一个合法但残缺的短包。第三轮核验指出的。
    tmp = OUT + ".tmp"
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            for rel in 白名单:
                src = os.path.join(MS, rel.replace("/", os.sep))
                if not os.path.exists(src):
                    print("缺文件,拒绝打包:", rel)
                    return 1
                # 固定时间戳 → 可复现打包:内容不变,重打得到同一个 SHA。
                # z.write 会把源文件 mtime 写进条目,touch 一下就换哈希,没法对账。
                zi = zipfile.ZipInfo("myshop/" + rel, date_time=(1980, 1, 1, 0, 0, 0))
                zi.compress_type = zipfile.ZIP_DEFLATED
                zi.external_attr = 0o644 << 16
                # 不钉 create_system 的话,同样的内容在 Linux 上重打会得到
                # 另一个 SHA(Windows=0 / Unix=3),跨机对账就对不上。
                zi.create_system = 3
                with open(src, "rb") as fh:
                    z.writestr(zi, fh.read())
        with zipfile.ZipFile(tmp) as z:
            names = z.namelist()
            # 不用 assert:python -O 会把 assert 整条删掉,自检就静默消失了。
            if len(names) != len(白名单):
                print("条目数不符,拒绝发布:%d != %d" % (len(names), len(白名单)))
                return 1
            脏 = [n for n in names for b in 禁止 if b in n]
            if 脏:
                print("打进了禁止项,拒绝发布:", 脏)
                return 1
        os.replace(tmp, OUT)          # 同目录改名,原子;失败时旧包原样保留
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    h = hashlib.sha256(open(OUT, "rb").read()).hexdigest()
    print("myshop-source.zip:%d 条目 · %d 字节" % (len(白名单), os.path.getsize(OUT)))
    print("SHA-256:", h.upper())
    return 0


if __name__ == "__main__":
    sys.exit(main())
