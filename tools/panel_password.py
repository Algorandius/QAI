"""生成管理台远程登录的密码哈希。

用法：
    .venv\\Scripts\\python.exe tools\\panel_password.py

把输出的那一行复制到 .env：

    WEB_PANEL_PASSWORD_HASH=scrypt$16384$8$1$...

为什么用哈希而不是明文：.env 可能被打包、被备份、被误发出去，
里面躺着一行明文密码就等于把控制台钥匙一起发出去了。
"""
from __future__ import annotations

import base64
import getpass
import hashlib
import secrets

N, R, P = 2 ** 14, 8, 1


def main() -> int:
    pw = getpass.getpass("设置管理台密码：")
    if len(pw) < 6:
        print("密码太短，至少 6 位。")
        return 1
    if pw != getpass.getpass("再输一次："):
        print("两次输入不一致。")
        return 1

    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(pw.encode("utf-8"), salt=salt, n=N, r=R, p=P, dklen=32)
    line = "scrypt${}${}${}${}${}".format(
        N, R, P,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(dk).decode("ascii"),
    )

    print()
    print("把下面这行写进 .env（替换已有的同名行）：")
    print()
    print(f"WEB_PANEL_PASSWORD_HASH={line}")
    print()
    print("同时建议加上：")
    print("WEB_PANEL_HOST=0.0.0.0")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
