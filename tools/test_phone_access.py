"""验证「手机访问」一键开启的后端逻辑。

用一个临时 .env，不动用户的真实配置。
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 先准备好临时目录，让 launcher 的 ENV_FILE 指过去。
# launcher 在 import 时就定死 ENV_FILE = ROOT/".env"，所以直接改模块属性。
os.environ["WEB_PANEL"] = "false"

spec = importlib.util.spec_from_file_location("launcher_under_test", ROOT / "launcher.py")
L = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(L)
except SystemExit:
    pass

FAILS: list[str] = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{(' — ' + str(detail)) if detail and not cond else ''}")
    if not cond:
        FAILS.append(name)


def main() -> int:
    tmpdir = Path(tempfile.mkdtemp(prefix="qai-phone-"))
    env_file = tmpdir / ".env"
    env_file.write_text(
        "# 测试用 .env\nWEB_PANEL=true\nWEB_PANEL_PORT=8081\n", encoding="utf-8"
    )
    L.ENV_FILE = env_file
    L.ENV = L._read_env()

    print("=" * 62)
    print("QAI 手机访问 · 后端逻辑验证")
    print("=" * 62)

    try:
        print("\n[1] 初始状态（未开启）")
        st = L.phone_access_status()
        check("enabled=False", st["enabled"] is False, st["enabled"])
        check("has_password=False", st["has_password"] is False)
        check("有 URL", st["url"].startswith("http://"), st["url"])
        print(f"      探测到地址：{st['url']}")

        print("\n[2] 一键开启")
        st = L.phone_access_enable()
        check("enabled=True", st["enabled"] is True, st["enabled"])
        check("生成了密码", bool(st.get("password")), st.get("password"))
        pwd = st.get("password", "")
        check("密码格式 x3-x3-x3-x3", len(pwd.split("-")) == 4, pwd)
        check("密码不含易混字符", not any(c in pwd for c in "ilo01"), pwd)

        env = L._read_env()
        check(".env 写入 WEB_PANEL_HOST=0.0.0.0",
              env.get("WEB_PANEL_HOST") == "0.0.0.0", env.get("WEB_PANEL_HOST"))
        check(".env 写入了哈希", env.get("WEB_PANEL_PASSWORD_HASH", "").startswith("scrypt$"))
        check(".env 未写入明文密码", not env.get("WEB_PANEL_PASSWORD"))
        check("注释被保留", "# 测试用 .env" in env_file.read_text(encoding="utf-8"))

        print("\n[3] 哈希可验证")
        import base64
        h = env["WEB_PANEL_PASSWORD_HASH"]
        scheme, n, r, p, salt_b64, hash_b64 = h.split("$")
        import hashlib
        dk = hashlib.scrypt(pwd.encode(), salt=base64.b64decode(salt_b64),
                            n=int(n), r=int(r), p=int(p), dklen=32)
        check("密码与哈希匹配", dk == base64.b64decode(hash_b64))
        wrong = hashlib.scrypt(b"wrong", salt=base64.b64decode(salt_b64),
                               n=int(n), r=int(r), p=int(p), dklen=32)
        check("错误密码不匹配", wrong != base64.b64decode(hash_b64))

        print("\n[4] 再次开启不重置密码")
        st2 = L.phone_access_enable()
        check("无新密码返回", not st2.get("password"), st2.get("password"))
        check("哈希未变", L._read_env().get("WEB_PANEL_PASSWORD_HASH") == h)

        print("\n[5] 重置密码")
        st3 = L.phone_access_reset_password()
        check("返回新密码", bool(st3.get("password")))
        check("新密码与旧的不同", st3.get("password") != pwd)
        check("哈希已更新", L._read_env().get("WEB_PANEL_PASSWORD_HASH") != h)

        print("\n[6] 关闭")
        st4 = L.phone_access_disable()
        check("enabled=False", st4["enabled"] is False)
        env4 = L._read_env()
        check("WEB_PANEL_HOST 回到 127.0.0.1",
              env4.get("WEB_PANEL_HOST") == "127.0.0.1", env4.get("WEB_PANEL_HOST"))
        check("密码保留（下次开启不用重设）", bool(env4.get("WEB_PANEL_PASSWORD_HASH")))

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    print("\n" + "=" * 62)
    if FAILS:
        print(f"失败 {len(FAILS)} 项：")
        for f in FAILS:
            print("  - " + f)
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
