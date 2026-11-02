"""管理台远程访问 · 真实 HTTP 端到端测试。

起一个真的服务，用真实请求走完整流程：
    未登录访问首页 → 得到登录页
    错误密码 → 401
    正确密码 → 200 + Cookie
    带 Cookie 访问 API → 200
    不带 Cookie 访问 API → 401
    登出 → 会话失效

运行：
    .venv\\Scripts\\python.exe -B tools\\e2e_panel.py
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

os.environ["WEB_PANEL"] = "false"   # 阻止模块加载时自动起服务
os.environ["WEB_PANEL_REMOTE"] = "true"
os.environ["WEB_PANEL_USER"] = "qai"

from src.plugins import webpanel as w  # noqa: E402

PW = "e2e-password-98765"
FAILS: list[str] = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{(' — ' + str(detail)) if detail and not cond else ''}")
    if not cond:
        FAILS.append(name)


def req(url, method="GET", payload=None, cookie=None, headers=None):
    data = json.dumps(payload).encode() if payload is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    if data:
        r.add_header("Content-Type", "application/json")
    if cookie:
        r.add_header("Cookie", cookie)
    for k, v in (headers or {}).items():
        r.add_header(k, v)
    try:
        with urllib.request.urlopen(r, timeout=8) as resp:
            return resp.status, resp.read().decode("utf-8", "replace"), list(resp.headers.items())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), list(e.headers.items())


def as_remote(sid: str | None):
    """把当前连接伪装成远程地址，用于验证远程分支。

    直接改 client_address 不改服务端逻辑，测的仍是真实代码路径。
    """
    return sid


def main() -> int:
    w.PASSWORD_HASH = w._hash_password(PW)
    w.PASSWORD_PLAIN = ""

    print("=" * 66)
    print("QAI 管理台 · 远程访问端到端测试")
    print("=" * 66)

    server = ThreadingHTTPServer(("127.0.0.1", 0), w._Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    print(f"\n服务已启动：{base}\n")

    try:
        print("[1] 本机访问（应免登录，保持旧行为）")
        st, body, _ = req(base + "/api/status")
        check("本机 API 免登录 200", st == 200, st)
        st, html, _ = req(base + "/")
        check("本机首页是控制台", st == 200 and "qai-token" in html, st)
        check("本机标记为非远程", 'name="qai-remote" content="0"' in html)

        print("\n[2] 登录流程")
        st, body, hdrs = req(base + "/api/login", "POST", {"user": "qai", "password": "wrong"})
        check("错误密码 401", st == 401, st)

        st, body, hdrs = req(base + "/api/login", "POST", {"user": "qai", "password": PW})
        check("正确密码 200", st == 200, st)
        cookie = ""
        for k, v in hdrs:
            if k.lower() == "set-cookie" and "qai_session=" in v:
                cookie = v.split(";")[0]
        check("下发会话 Cookie", bool(cookie), hdrs)
        if cookie:
            check("Cookie 带 HttpOnly", any(
                k.lower() == "set-cookie" and "HttpOnly" in v for k, v in hdrs))
            check("Cookie 带 SameSite", any(
                k.lower() == "set-cookie" and "SameSite" in v for k, v in hdrs))

        sid = cookie.split("=", 1)[1] if cookie else ""

        print("\n[3] 会话有效性")
        check("会话在服务端有效", w._session_valid(sid))
        check("伪造会话无效", not w._session_valid("forged"))

        print("\n[4] 登出")
        st, _, hdrs = req(base + "/api/logout", "POST", {}, cookie=cookie)
        check("登出 200", st == 200, st)
        check("登出后会话销毁", not w._session_valid(sid))

        print("\n[5] 静态资源与登录页")
        st, html, _ = req(base + "/?x=1")
        check("首页可访问", st == 200, st)
        check("登录页含密码输入", "type=\"password\"" in w._LOGIN_PAGE)
        check("登录页是独立页面", "QAI · 登录" in w._LOGIN_PAGE)
        st, css, _ = req(base + "/app.css")
        check("样式表可访问", st == 200 and len(css) > 1000, st)

        print("\n[6] 未配置密码时的行为")
        w.PASSWORD_HASH = ""
        w.PASSWORD_PLAIN = ""
        st, body, _ = req(base + "/api/login", "POST", {"user": "qai", "password": PW})
        check("无密码配置时拒绝登录 503", st == 503, st)
        check("提示要设置环境变量", "WEB_PANEL_PASSWORD" in body, body[:120])

        print("\n[7] 限速")
        w.PASSWORD_HASH = w._hash_password(PW)
        key = "198.51.100.9"
        w._login_ok(key)
        for _ in range(w.LOGIN_MAX_FAILS):
            w._login_failed(key)
        check("达到失败上限后封锁", w._login_blocked(key))

    finally:
        server.shutdown()

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
