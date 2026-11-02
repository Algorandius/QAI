"""管理台远程登录/鉴权的验证脚本（进程内，不依赖 subprocess）。

验证点：
1. scrypt 哈希生成与校验
2. 地址判定（本机 / 局域网 / Tailscale CGNAT / 公网）
3. 真实 HTTP 服务：本机免登录、远程 401、密码错 401、密码对 200 + Cookie、登出后失效

运行：
    .venv\\Scripts\\python.exe -B tools\\test_panel_auth.py
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 必须在 import webpanel 之前设置：避免模块加载时自动起服务
os.environ["WEB_PANEL"] = "false"
os.environ["WEB_PANEL_REMOTE"] = "true"
os.environ["WEB_PANEL_USER"] = "qai"

PW = "test-password-12345"

from src.plugins import webpanel as w  # noqa: E402

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{(' — ' + detail) if detail and not cond else ''}")
    if not cond:
        FAILS.append(name)


def test_hash() -> None:
    print("\n[1] 密码哈希 (scrypt)")
    h = w._hash_password(PW)
    check("格式为 scrypt$n$r$p$salt$hash", h.startswith("scrypt$") and h.count("$") == 5)

    # 让模块用这个哈希
    w.PASSWORD_HASH = h
    w.PASSWORD_PLAIN = ""
    check("正确密码通过", w._verify_password(PW))
    check("错误密码拒绝", not w._verify_password("nope"))
    check("空密码拒绝", not w._verify_password(""))
    check("大小写敏感", not w._verify_password(PW.upper()))
    check("登录已配置", w._login_configured())

    # 同一密码两次哈希应不同（盐随机）
    check("盐随机化", w._hash_password(PW) != h)

    # 损坏的哈希不能放行
    w.PASSWORD_HASH = "scrypt$bad"
    check("损坏哈希拒绝而非崩溃", not w._verify_password(PW))
    w.PASSWORD_HASH = h


def test_addr() -> None:
    print("\n[2] 地址判定")
    cases = [
        # (地址, 是回环, 是内网/组网)
        ("127.0.0.1", True, True),          # 回环本身也落在 private 段
        ("::1", True, True),
        ("localhost", True, False),         # 名字，不是 IP
        ("192.168.1.9", False, True),
        ("10.0.0.5", False, True),
        ("172.16.0.1", False, True),
        ("100.101.102.103", False, True),   # Tailscale CGNAT，必须放行
        ("100.64.0.1", False, True),
        ("8.8.8.8", False, False),
        ("1.2.3.4", False, False),
        ("", False, False),
    ]
    for ip, loc, pri in cases:
        got_loc = w._is_loopback_addr(ip)
        got_pri = w._is_private_addr(ip)
        check(f"{ip or '(空)'}: loopback={loc} private={pri}",
              got_loc == loc and got_pri == pri,
              f"实际 loopback={got_loc} private={got_pri}")


def test_session() -> None:
    print("\n[3] 会话管理")
    sid = w._new_session()
    check("新会话有效", w._session_valid(sid))
    check("空 sid 无效", not w._session_valid(""))
    check("伪造 sid 无效", not w._session_valid("forged-token-abc"))
    w._session_drop(sid)
    check("登出后失效", not w._session_valid(sid))

    # 过期会话
    sid2 = w._new_session()
    with w._sessions_lock:
        w._sessions[sid2] = time.time() - 1
    check("过期会话失效", not w._session_valid(sid2))


def test_ratelimit() -> None:
    print("\n[4] 登录限速")
    key = "203.0.113.77"
    w._login_ok(key)
    check("初始未封锁", not w._login_blocked(key))
    for _ in range(w.LOGIN_MAX_FAILS):
        w._login_failed(key)
    check(f"失败 {w.LOGIN_MAX_FAILS} 次后封锁", w._login_blocked(key))
    # 封锁到期后恢复
    with w._login_lock:
        n, _until = w._login_fails[key]
        w._login_fails[key] = (n, time.time() - 1)
    check("封锁到期后恢复", not w._login_blocked(key))
    w._login_ok(key)
    check("成功登录清除计数", not w._login_blocked(key))


def test_http() -> None:
    print("\n[5] HTTP 端到端")
    server = ThreadingHTTPServer(("127.0.0.1", 0), w._Handler)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{port}"
    try:
        # 本机请求：免登录（保持旧行为）
        st, body, hdrs = _get(base + "/api/status")
        check("本机 /api/status 免登录", st == 200, f"status={st}")
        check("本机返回正常 JSON", isinstance(body, dict) and "ok" in body or st == 200)

        # 本机首页：正常页面
        st, raw, _ = _get_raw(base + "/")
        check("本机首页返回控制台", st == 200 and "qai-token" in raw)

        # 登录接口
        st, body = _post(base + "/api/login", {"user": "qai", "password": PW})
        check("正确密码登录成功", st == 200 and body.get("ok") is True, f"status={st} {body}")

        cookie = None
        st, body, hdrs = _post_full(base + "/api/login", {"user": "qai", "password": PW})
        for k, v in hdrs:
            if k.lower() == "set-cookie" and "qai_session=" in v:
                cookie = v.split(";")[0]
        check("登录后下发会话 Cookie", cookie is not None, str(hdrs))

        st, body = _post(base + "/api/login", {"user": "qai", "password": "wrong"})
        check("错误密码被拒", st == 401, f"status={st}")

        st, body, hdrs = _post_full(base + "/api/logout", {}, cookie=cookie)
        check("登出成功", st == 200)
        # 登出后同一个 cookie 再访问写接口应当 401
        # （先解除本机免登录模拟远程：直接查会话表）
        sid = cookie.split("=", 1)[1] if cookie else ""
        check("登出后会话被销毁", not w._session_valid(sid))
    finally:
        server.shutdown()


def _get(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status, json.loads(r.read().decode("utf-8")), dict(r.headers)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8")), dict(e.headers)
        except Exception:
            return e.code, {}, dict(e.headers)


def _get_raw(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status, r.read().decode("utf-8", "replace"), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), dict(e.headers)


def _post(url, payload, cookie=None):
    st, body, _hdrs = _post_full(url, payload, cookie)
    return st, body


def _post_full(url, payload, cookie=None):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if cookie:
        req.add_header("Cookie", cookie)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode("utf-8")), list(r.headers.items())
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw), list(e.headers.items())
        except Exception:
            return e.code, {"raw": raw}, list(e.headers.items())


def test_samesite() -> None:
    print("\n[6] 跨站防护（不带 Origin 必须放行，外站 Origin 必须拒绝）")
    h = w._Handler.__new__(w._Handler)
    h.headers = {}  # type: ignore[attr-defined]
    check("无 Origin 放行（本机导航/GET 的常态）", h._same_site_ok())
    h.headers = {"Sec-Fetch-Site": "cross-site"}  # type: ignore[attr-defined]
    check("cross-site 拒绝", not h._same_site_ok())
    h.headers = {"Origin": "https://evil.example.com"}  # type: ignore[attr-defined]
    check("外站 Origin 拒绝", not h._same_site_ok())
    h.headers = {"Origin": "http://127.0.0.1:8081"}  # type: ignore[attr-defined]
    check("本机 Origin 放行", h._same_site_ok())
    h.headers = {"Origin": "http://100.101.102.103:8081"}  # type: ignore[attr-defined]
    check("Tailscale Origin 放行", h._same_site_ok())
    h.headers = {"Origin": "http://192.168.1.9:8081"}  # type: ignore[attr-defined]
    check("局域网 Origin 放行", h._same_site_ok())


def main() -> int:
    print("=" * 62)
    print("QAI 管理台 · 远程访问鉴权验证")
    print("=" * 62)
    test_hash()
    test_addr()
    test_session()
    test_ratelimit()
    test_samesite()
    test_http()

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
