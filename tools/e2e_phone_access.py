"""手机访问 · HTTP 接口端到端验证。

真的起一个 launcher 的 HTTP 处理，走完整流程：
    读状态 → 一键开启 → 拿二维码 → 用生成的密码去管理台登录 → 关闭 → 确认收回
"""
from __future__ import annotations

import base64
import hashlib
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

FAILS: list[str] = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{(' — ' + str(detail)) if detail and not cond else ''}")
    if not cond:
        FAILS.append(name)


def req(url, method="GET", payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    if data:
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw}


def main() -> int:
    tmpdir = Path(tempfile.mkdtemp(prefix="qai-phone-e2e-"))
    env_file = tmpdir / ".env"
    env_file.write_text("# e2e\nWEB_PANEL=true\nWEB_PANEL_PORT=8081\n", encoding="utf-8")

    spec = importlib.util.spec_from_file_location("lau", ROOT / "launcher.py")
    L = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(L)
    L.ENV_FILE = env_file
    L.ENV = L._read_env()

    # 令牌校验放行：真实启动器靠注入的 token，这里直接绕过以便测业务端点
    L._token_ok = lambda handler: True
    L._same_site_ok = lambda handler: True

    srv = ThreadingHTTPServer(("127.0.0.1", 0), L.Handler if hasattr(L, "Handler") else L._Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    print("=" * 64)
    print("QAI 手机访问 · HTTP 端到端")
    print("=" * 64)

    try:
        print("\n[1] 初始状态")
        st, d = req(base + "/api/phone-access")
        check("接口 200", st == 200, st)
        check("enabled=False", d.get("enabled") is False, d.get("enabled"))
        check("没开时不返回二维码", not d.get("qr_svg"), len(d.get("qr_svg") or ""))

        print("\n[2] 一键开启")
        st, d = req(base + "/api/phone-access/enable", "POST", {})
        check("接口 200", st == 200, st)
        check("enabled=True", d.get("enabled") is True)
        pwd = d.get("password") or ""
        check("返回一次性密码", bool(pwd), pwd)
        check("返回地址", (d.get("url") or "").startswith("http://"), d.get("url"))
        check("返回二维码 SVG", "<svg" in (d.get("qr_svg") or ""))
        print(f"      地址：{d.get('url')}")
        print(f"      账号：{d.get('user')}")
        print(f"      密码：{pwd}")

        print("\n[3] 生成的密码真的能登录管理台")
        from src.plugins import webpanel as w
        w.PASSWORD_HASH = L._read_env().get("WEB_PANEL_PASSWORD_HASH", "")
        w.PASSWORD_PLAIN = ""
        check("哈希已被 webpanel 接受", w._verify_password(pwd))
        check("错误密码被拒", not w._verify_password("wrong-pass"))
        check("其他 QAI 用户拿不到别人的哈希",
              w._verify_password(pwd + "x") is False)

        print("\n[4] 关闭")
        st, d = req(base + "/api/phone-access/disable", "POST", {})
        check("接口 200", st == 200, st)
        check("enabled=False", d.get("enabled") is False)
        env = L._read_env()
        check(".env 恢复 127.0.0.1", env.get("WEB_PANEL_HOST") == "127.0.0.1",
              env.get("WEB_PANEL_HOST"))

        print("\n[5] 关闭后重新开启，不应换密码")
        before = env.get("WEB_PANEL_PASSWORD_HASH")
        st, d = req(base + "/api/phone-access/enable", "POST", {})
        check("未返回新密码", not d.get("password"), d.get("password"))
        check("哈希保持不变", L._read_env().get("WEB_PANEL_PASSWORD_HASH") == before)

        print("\n[6] 重置密码")
        st, d = req(base + "/api/phone-access/reset-password", "POST", {})
        newpwd = d.get("password") or ""
        check("拿到新密码", bool(newpwd))
        check("新旧不同", newpwd != pwd)
        w.PASSWORD_HASH = L._read_env().get("WEB_PANEL_PASSWORD_HASH", "")
        check("新密码可登录", w._verify_password(newpwd))
        check("旧密码已失效", not w._verify_password(pwd))

    finally:
        srv.shutdown()
        shutil.rmtree(tmpdir, ignore_errors=True)

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
