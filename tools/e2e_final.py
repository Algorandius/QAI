"""最终验收：起一个和真实启动器同构的 HTTP 服务，走完手机访问全流程。

和 e2e_phone_access 的区别：这里**不绕过**鉴权函数，
而是发真实的 X-QAI-Token 头，验证生产路径上确实能通。
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


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="qai-final-"))
    envf = tmp / ".env"
    envf.write_text("# final\nWEB_PANEL=true\nWEB_PANEL_PORT=8081\n", encoding="utf-8")

    spec = importlib.util.spec_from_file_location("lau", ROOT / "launcher.py")
    L = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(L)
    L.ENV_FILE = envf
    L.ENV = L._read_env()

    srv = ThreadingHTTPServer(("127.0.0.1", 0), L._Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"

    def call(path, method="GET", payload=None, token=True):
        data = json.dumps(payload).encode() if payload is not None else None
        r = urllib.request.Request(base + path, data=data, method=method)
        if data:
            r.add_header("Content-Type", "application/json")
        if token:
            r.add_header("X-QAI-Token", L._API_TOKEN)
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            try:
                return e.code, json.loads(raw)
            except Exception:
                return e.code, {"raw": raw}

    print("=" * 64)
    print("QAI 手机访问 · 生产路径验收（带真实令牌）")
    print("=" * 64)
    try:
        print("\n[1] 鉴权仍在生效")
        st, d = call("/api/phone-access", token=False)
        check("不带令牌被拒 403", st == 403, st)

        st, d = call("/api/phone-access")
        check("带令牌可读", st == 200, st)
        check("初始未开启", d.get("enabled") is False)

        print("\n[2] 一键开启（生产路径）")
        st, d = call("/api/phone-access/enable", "POST", {})
        check("开启成功", st == 200 and d.get("enabled") is True, st)
        pwd, url, qr = d.get("password", ""), d.get("url", ""), d.get("qr_svg", "")
        check("有密码", bool(pwd))
        check("有地址", url.startswith("http://"))
        check("有二维码", "<svg" in qr)
        print(f"      地址 {url}   账号 {d.get('user')}   密码 {pwd}")

        print("\n[3] 拿这串密码去管理台登录")
        from src.plugins import webpanel as w
        w.PASSWORD_HASH = L._read_env().get("WEB_PANEL_PASSWORD_HASH", "")
        w.PASSWORD_PLAIN = ""
        check("webpanel 能验证这个密码", w._verify_password(pwd))

        import base64
        import hashlib
        h = w.PASSWORD_HASH
        _, n, r_, p_, sb, hb = h.split("$")
        dk = hashlib.scrypt(pwd.encode(), salt=base64.b64decode(sb),
                            n=int(n), r=int(r_), p=int(p_), dklen=32)
        check("独立解析也通过", dk == base64.b64decode(hb))

        print("\n[4] 二维码内容正确性")
        check("二维码包含该地址", "svg" in qr.lower() and len(qr) > 200)

        print("\n[5] 关闭并确认收回")
        st, d = call("/api/phone-access/disable", "POST", {})
        check("关闭成功", st == 200 and d.get("enabled") is False)
        check("地址回到本机", L._read_env().get("WEB_PANEL_HOST") == "127.0.0.1")

        print("\n[6] .env 完整性")
        txt = envf.read_text(encoding="utf-8")
        check("注释保留", "# final" in txt)
        check("没有重复键", txt.count("WEB_PANEL_HOST=") == 1,
              txt.count("WEB_PANEL_HOST="))

    finally:
        srv.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 64)
    if FAILS:
        print(f"失败 {len(FAILS)} 项")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
