"""验证「远程未登录」分支：不登录时必须拿不到任何数据。

本机测试永远走免登录分支，所以这里直接把 client_address 改成一个
局域网地址，让服务端认为请求来自远程——测的仍是真实代码路径。

同时验证：远程未登录时
  - 首页 → 登录页（不是控制台，拿不到令牌）
  - /api/* → 401
  - 登录后 → 正常
"""
from __future__ import annotations

import json
import os
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

PW = "remote-test-4242"
FAILS: list[str] = []


class RemoteHandler(w._Handler):
    """把来源伪装成局域网地址。"""

    def _client_is_local(self) -> bool:
        return False


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{(' — ' + str(detail)) if detail and not cond else ''}")
    if not cond:
        FAILS.append(name)


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

    srv = ThreadingHTTPServer(("127.0.0.1", 0), RemoteHandler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"

    print("=" * 66)
    print("QAI 管理台 · 远程未登录分支验证")
    print("=" * 66)
    try:
        print("\n[1] 未登录访问首页")
        st, html, _ = req(base + "/")
        check("首页返回 200", st == 200, st)
        check("返回的是登录页", "QAI · 登录" in html, html[:80])
        check("拿不到控制台内容", "remoteBanner" not in html)
        check("拿不到写接口令牌", 'name="qai-token"' not in html)

        print("\n[2] 未登录访问 API")
        for p in ("/api/status", "/api/personas", "/api/chatlog", "/api/settings"):
            st, body, _ = req(base + p)
            check(f"{p} 被拒 401", st == 401, st)
            check(f"{p} 带 login_required", "login_required" in body, body[:80])

        print("\n[3] 未登录写操作")
        st, body, _ = req(base + "/api/persona", "POST", {"pid": "x", "text": "hack"})
        check("写人设被拒", st in (401, 403), st)

        print("\n[4] 登录后")
        st, body, hdrs = req(base + "/api/login", "POST", {"user": "qai", "password": PW})
        check("登录成功", st == 200, st)
        cookie = ""
        for k, v in hdrs:
            if k.lower() == "set-cookie" and "qai_session=" in v:
                cookie = v.split(";")[0]
        check("拿到 Cookie", bool(cookie))

        st, html, _ = req(base + "/", cookie=cookie)
        check("登录后首页是控制台", "remoteBanner" in html, st)
        check("登录后令牌注入", 'name="qai-token"' in html)
        check("远程标记为 1", 'name="qai-remote" content="1"' in html, html[:200])

        st, body, _ = req(base + "/api/personas", cookie=cookie)
        check("登录后 API 可用", st == 200, st)

        print("\n[5] 伪造 Cookie")
        st, _, _ = req(base + "/api/personas", cookie="qai_session=forged-value")
        check("伪造 Cookie 被拒", st == 401, st)

        print("\n[6] 登出后")
        st, _, _ = req(base + "/api/logout", "POST", {}, cookie=cookie)
        check("登出成功", st == 200, st)
        st, _, _ = req(base + "/api/personas", cookie=cookie)
        check("登出后 API 被拒", st == 401, st)

    finally:
        srv.shutdown()

    print("\n" + "=" * 66)
    if FAILS:
        print(f"失败 {len(FAILS)} 项：")
        for f in FAILS:
            print("  - " + f)
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
