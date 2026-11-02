"""验证管理台「手机」页：8081 → 代理 → 8082 启动器。

这是跨端口代理，必须真起两个服务来测。
"""
from __future__ import annotations

import importlib.util
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
    tmp = Path(tempfile.mkdtemp(prefix="qai-proxy-"))
    envf = tmp / ".env"
    envf.write_text("WEB_PANEL=true\nWEB_PANEL_PORT=8081\n", encoding="utf-8")

    # --- 启动 8082 模拟器（真实 launcher） ---
    spec = importlib.util.spec_from_file_location("lau", ROOT / "launcher.py")
    L = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(L)
    L.ENV_FILE = envf
    L.ENV = L._read_env()
    L.DATA_DIR = tmp                      # 让令牌写进临时目录
    (tmp / "data").mkdir(exist_ok=True)
    (tmp / "data" / "api_token").write_text(L._API_TOKEN, encoding="utf-8")
    os.environ["QAI_DATA_DIR"] = str(tmp / "data")

    lport = 0
    lsrv = ThreadingHTTPServer(("127.0.0.1", 0), L._Handler)
    lport = lsrv.server_address[1]
    threading.Thread(target=lsrv.serve_forever, daemon=True).start()

    # --- 启动 8081（真实 webpanel） ---
    from src.plugins import webpanel as w
    w._launcher_base = lambda: f"http://127.0.0.1:{lport}"
    w.PASSWORD_HASH = ""
    w.PASSWORD_PLAIN = ""

    wsrv = ThreadingHTTPServer(("127.0.0.1", 0), w._Handler)
    wport = wsrv.server_address[1]
    threading.Thread(target=wsrv.serve_forever, daemon=True).start()

    lb = f"http://127.0.0.1:{lport}"
    wb = f"http://127.0.0.1:{wport}"

    print("=" * 64)
    print(f"管理台「手机」页 · 代理链路验证")
    print(f"  启动器 {lb}   管理台 {wb}")
    print("=" * 64)

    try:
        print("\n[1] 启动器侧直接访问")
        st, d = call(lb, "/api/phone-access", token=L._API_TOKEN)
        check("带令牌可读", st == 200, st)
        st, d = call(lb, "/api/phone-access", token=None)
        check("不带令牌被拒", st == 403, st)

        print("\n[2] 管理台代理读状态")
        st, d = call(wb, "/api/phone")
        check("代理返回 200", st == 200, st)
        check("能拿到 enabled 字段", "enabled" in d, list(d.keys()))

        print("\n[3] 管理台代理开启")
        st, d = call(wb, "/api/phone", "POST", {"action": "enable"}, token=w.PANEL_TOKEN)
        check("代理开启成功", st == 200 and d.get("ok") is not False, (st, d.get("error")))
        pwd = d.get("password", "")
        check("拿到密码", bool(pwd), pwd)
        check("拿到地址", (d.get("url") or "").startswith("http://"), d.get("url"))
        check("拿到二维码", "<svg" in (d.get("qr_svg") or ""))
        print(f"      地址 {d.get('url')}  密码 {pwd}")

        print("\n[4] 代理读回状态")
        st, d = call(wb, "/api/phone")
        check("已开启", d.get("enabled") is True)
        check("地址一致", (d.get("url") or "").startswith("http://"))

        print("\n[5] 代理重置密码")
        st, d = call(wb, "/api/phone", "POST", {"action": "reset"}, token=w.PANEL_TOKEN)
        newpwd = d.get("password", "")
        check("拿到新密码", bool(newpwd))
        check("新旧不同", newpwd != pwd)

        print("\n[6] 代理关闭")
        st, d = call(wb, "/api/phone", "POST", {"action": "disable"}, token=w.PANEL_TOKEN)
        check("关闭成功", d.get("enabled") is False)
        check(".env 回到本机", L._read_env().get("WEB_PANEL_HOST") == "127.0.0.1")

        print("\n[7] 未知操作")
        st, d = call(wb, "/api/phone", "POST", {"action": "hack"}, token=w.PANEL_TOKEN)
        check("被拒", d.get("ok") is False)

        print("\n[8] 启动器挂掉时")
        lsrv.shutdown()
        st, d = call(wb, "/api/phone")
        check("返回 launcher_down", d.get("launcher_down") is True, d)
        check("有错误说明", bool(d.get("error")))

    finally:
        try:
            lsrv.shutdown()
        except Exception:
            pass
        wsrv.shutdown()
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
