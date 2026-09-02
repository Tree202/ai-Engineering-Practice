# -*- coding: utf-8 -*-
"""白名单重打 myshop-source.zip —— 防止发布物再次漂移。

上一次漂移就是手工打包的后果:补了 web.py 之后 zip 没跟着重打,
下载的人拿不到第 4 层。此后 zip 一律由本脚本产出,白名单就是清单,
少一个文件直接报错,不会静默缺件。

用法:  python tools/build_zip.py
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


def main() -> int:
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
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
            with open(src, "rb") as fh:
                z.writestr(zi, fh.read())
    with zipfile.ZipFile(OUT) as z:
        names = z.namelist()
        assert len(names) == len(白名单), names
        assert not any(b in n for n in names for b in 禁止), "打进了禁止项"
    h = hashlib.sha256(open(OUT, "rb").read()).hexdigest()
    print("myshop-source.zip:%d 条目 · %d 字节" % (len(白名单), os.path.getsize(OUT)))
    print("SHA-256:", h.upper())
    return 0


if __name__ == "__main__":
    sys.exit(main())
