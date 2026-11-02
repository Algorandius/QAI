"""交叉验证：launcher 生成的哈希，webpanel 必须能验证。

两个模块各自实现了一份 scrypt，参数一旦漂移，用户就会遇到
「密码明明对，却一直说错误」——这种 bug 最难查，所以单独锁死。
"""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["WEB_PANEL"] = "false"

from src.plugins import webpanel as w  # noqa: E402

spec = importlib.util.spec_from_file_location("lau", ROOT / "launcher.py")
L = importlib.util.module_from_spec(spec)
spec.loader.exec_module(L)

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{(' — ' + str(detail)) if detail and not cond else ''}")
    if not cond:
        FAILS.append(name)


def main() -> int:
    print("=" * 62)
    print("launcher ↔ webpanel 密码哈希交叉验证")
    print("=" * 62)

    pw = "cross-check-pw-9182"

    print("\n[1] launcher 生成 → webpanel 验证")
    h = L._make_password_hash(pw)
    w.PASSWORD_HASH = h
    w.PASSWORD_PLAIN = ""
    check("webpanel 接受", w._verify_password(pw), h[:30])
    check("错误密码拒绝", not w._verify_password(pw + "x"))

    print("\n[2] webpanel 生成 → launcher 逻辑验证")
    h2 = w._hash_password(pw)
    got = L._read_env  # 只为确认模块可访问
    # 用同样的解析逻辑验证
    scheme, n, r, p, salt_b64, hash_b64 = h2.split("$")
    check("scheme=scrypt", scheme == "scrypt", scheme)
    dk = hashlib.scrypt(pw.encode(), salt=base64.b64decode(salt_b64),
                        n=int(n), r=int(r), p=int(p), dklen=32)
    check("参数可互相解析", dk == base64.b64decode(hash_b64))

    print("\n[3] 参数一致性")
    lh = L._make_password_hash("abc123")
    wh = w._hash_password("abc123")
    lp = lh.split("$")[1:4]
    wp = wh.split("$")[1:4]
    check(f"scrypt 参数一致 launcher={lp} webpanel={wp}", lp == wp)

    print("\n[4] 双向互换")
    for label, hh in (("launcher 生成的", lh), ("webpanel 生成的", wh)):
        w.PASSWORD_HASH = hh
        check(f"{label}哈希，webpanel 可验证", w._verify_password("abc123"))
        # launcher 侧按同样格式解
        _, n2, r2, p2, s2, h2b = hh.split("$")
        dk2 = hashlib.scrypt(b"abc123", salt=base64.b64decode(s2),
                             n=int(n2), r=int(r2), p=int(p2), dklen=32)
        check(f"{label}哈希，独立解析可验证", dk2 == base64.b64decode(h2b))

    print("\n[5] 生成的随机密码强度")
    import string
    for _ in range(5):
        pw2 = L._random_password()
        parts = pw2.split("-")
        ok = (len(parts) == 4 and all(len(x) == 3 for x in parts)
              and pw2.islower() or pw2.replace("-", "").islower())
        check(f"{pw2} 格式正确", len(parts) == 4 and all(len(x) == 3 for x in parts), pw2)
        w.PASSWORD_HASH = L._make_password_hash(pw2)
        check(f"{pw2} 可登录", w._verify_password(pw2))

    print("\n" + "=" * 62)
    if FAILS:
        print(f"失败 {len(FAILS)} 项")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
