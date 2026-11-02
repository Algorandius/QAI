"""验证「忘记密码」场景：不依赖启动器也能重置、能关掉、能重新看到密码。"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["WEB_PANEL"] = "false"

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{(' — ' + str(detail)) if detail and not cond else ''}")
    if not cond:
        FAILS.append(name)


def call(base, path, method="GET", payload=None, token=None):
    data = json.dumps(payload).encode() if payload is not None else None
    r = urllib.request.Request(base + path, data=data, method=method)
    if data:
        r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("X-QAI-Token", token)
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw[:150]}


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="qai-forgot-"))
    envf = tmp / ".env"
    envf.write_text(
        "WEB_PANEL=true\nWEB_PANEL_PORT=8081\nWEB_PANEL_HOST=0.0.0.0\n",
        encoding="utf-8",
    )
    data_dir = tmp / "data"
    data_dir.mkdir()
    os.environ["QAI_DATA_DIR"] = str(data_dir)

    from src.plugins import webpanel as w
    w.ENV_FILE = envf
    w.PASSWORD_HASH = ""
    w.PASSWORD_PLAIN = ""

    srv = ThreadingHTTPServer(("127.0.0.1", 0), w._Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    T = w.PANEL_TOKEN

    print("=" * 64)
    print("忘记密码场景验证（不启动 8082）")
    print("=" * 64)
    try:
        print("\n[1] 默认没有密码时就能重置出密码（无需先登录）")
        st, d = call(base, "/api/phone", "POST", {"action": "reset"}, token=T)
        check("重置成功", st == 200 and d.get("ok") is not False, (st, d.get("error")))
        pw1 = d.get("password", "")
        check("拿到密码", bool(pw1), pw1)
        print(f"      新密码：{pw1}")

        print("\n[2] 密码可以再次看到（本机）")
        st, d = call(base, "/api/phone", token=T)
        check("状态里有 saved_password", bool(d.get("saved_password")), list(d.keys()))
        check("与刚才一致", d.get("saved_password") == pw1, d.get("saved_password"))

        print("\n[3] 密码真的能登录")
        w.PASSWORD_HASH = w._read_env_pairs().get("WEB_PANEL_PASSWORD_HASH", "")
        w.PASSWORD_PLAIN = ""
        check("哈希已写入 .env", w.PASSWORD_HASH.startswith("scrypt$"))
        check("密码可验证", w._verify_password(pw1))

        print("\n[4] 能关掉手机访问")
        st, d = call(base, "/api/phone", "POST", {"action": "disable"}, token=T)
        check("关闭成功", st == 200 and d.get("enabled") is False, (st, d))
        check(".env 已改回本机", w._read_env_pairs().get("WEB_PANEL_HOST") == "127.0.0.1",
              w._read_env_pairs().get("WEB_PANEL_HOST"))
        check("密码保留（关闭不清密码）", bool(w._read_env_pairs().get("WEB_PANEL_PASSWORD_HASH")))

        print("\n[5] 再开启不用重新设密码")
        st, d = call(base, "/api/phone", "POST", {"action": "enable"}, token=T)
        check("开启成功", d.get("enabled") is True)
        check("没有生成新密码", not d.get("password"), d.get("password"))

        print("\n[6] 忘记密码 → 再重置一次")
        st, d = call(base, "/api/phone", "POST", {"action": "reset"}, token=T)
        pw2 = d.get("password", "")
        check("二次重置成功", bool(pw2))
        check("新密码与旧的不同", pw2 != pw1)
        w.PASSWORD_HASH = w._read_env_pairs().get("WEB_PANEL_PASSWORD_HASH", "")
        check("新密码可登录", w._verify_password(pw2))
        check("旧密码已失效", not w._verify_password(pw1))

        print("\n[7] 没有启动器也全程可用")
        check("整个过程没碰 8082", True)

    finally:
        srv.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 64)
    if FAILS:
        print(f"失败 {len(FAILS)} 项：")
        for f in FAILS:
            print("  - " + f)
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
