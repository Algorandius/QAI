"""真实启动验证：按 .env 的配置绑定 0.0.0.0，从本机网卡地址访问。

这是最接近「手机连过来」的验证：
  1. 服务真的监听在 0.0.0.0
  2. 用本机局域网 IP（不是 127.0.0.1）访问
  3. 此时服务端认为来源是远程，必须登录
"""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["WEB_PANEL"] = "false"
os.environ["WEB_PANEL_REMOTE"] = "true"
os.environ["WEB_PANEL_USER"] = "qai"

from src.plugins import webpanel as w  # noqa: E402

PW = "lan-test-7788"


def lan_ip() -> str:
    """取本机在局域网中的地址（不会真的发包）。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def req(url, method="GET", payload=None, cookie=None):
    data = json.dumps(payload).encode() if payload is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    if data:
        r.add_header("Content-Type", "application/json")
    if cookie:
        r.add_header("Cookie", cookie)
    try:
        with urllib.request.urlopen(r, timeout=8) as resp:
            return resp.status, resp.read().decode("utf-8", "replace"), list(resp.headers.items())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), list(e.headers.items())


def main() -> int:
    w.PASSWORD_HASH = w._hash_password(PW)
    w.PASSWORD_PLAIN = ""
    ip = lan_ip()
    print("=" * 62)
    print(f"局域网 IP：{ip}")
    print("=" * 62)

    srv = ThreadingHTTPServer(("0.0.0.0", 0), w._Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://{ip}:{port}"
    print(f"\n监听 0.0.0.0:{port}，从 {base} 访问（等价于手机连过来）\n")

    fails = []
    try:
        st, html, _ = req(base + "/")
        c1 = st == 200 and "QAI · 登录" in html
        print(f"  {'PASS' if c1 else 'FAIL'}  未登录首页 = 登录页 (status={st})")
        if not c1: fails.append("登录页")

        c2 = 'name="qai-token"' not in html
        print(f"  {'PASS' if c2 else 'FAIL'}  未登录拿不到令牌")
        if not c2: fails.append("令牌泄露")

        st, _, _ = req(base + "/api/status")
        c3 = st == 401
        print(f"  {'PASS' if c3 else 'FAIL'}  未登录 API 401 (status={st})")
        if not c3: fails.append("API未拦截")

        st, _, hdrs = req(base + "/api/login", "POST", {"user": "qai", "password": PW})
        cookie = ""
        for k, v in hdrs:
            if k.lower() == "set-cookie" and "qai_session=" in v:
                cookie = v.split(";")[0]
        c4 = st == 200 and bool(cookie)
        print(f"  {'PASS' if c4 else 'FAIL'}  登录成功并拿到 Cookie (status={st})")
        if not c4: fails.append("登录")

        st, html, _ = req(base + "/", cookie=cookie)
        c5 = st == 200 and "remoteBanner" in html and 'content="1"' in html
        print(f"  {'PASS' if c5 else 'FAIL'}  登录后进入控制台且标记为远程")
        if not c5: fails.append("控制台")

        st, _, _ = req(base + "/api/status", cookie=cookie)
        c6 = st == 200
        print(f"  {'PASS' if c6 else 'FAIL'}  登录后 API 可用 (status={st})")
        if not c6: fails.append("API可用")
    finally:
        srv.shutdown()

    print("\n" + ("全部通过" if not fails else f"失败：{fails}"))
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
