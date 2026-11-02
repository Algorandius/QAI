"""验证 index.html 的注入结果（令牌 + 远程标记）。"""
from __future__ import annotations

import os
import sys
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["WEB_PANEL"] = "false"
os.environ["WEB_PANEL_REMOTE"] = "true"

from src.plugins import webpanel as w  # noqa: E402


def fetch(base, path, cookie=None):
    r = urllib.request.Request(base + path)
    if cookie:
        r.add_header("Cookie", cookie)
    with urllib.request.urlopen(r, timeout=6) as resp:
        return resp.status, resp.read().decode("utf-8", "replace")


def main() -> int:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), w._Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    ok = True
    try:
        st, html = fetch(base, "/")
        print(f"[1] 本机首页 status={st} len={len(html)}")
        has_token = 'name="qai-token"' in html
        has_remote = 'name="qai-remote"' in html
        print(f"    令牌已注入: {has_token}")
        print(f"    远程标记已注入: {has_remote}")
        print(f"    标记值为本机(0): {'content=\"0\"' in html}")
        ok = ok and has_token and has_remote and 'content="0"' in html

        # 检查前端新增元素存在
        for need in ('id="remoteBanner"', 'id="logoutBtn"', 'id="remoteLogout"'):
            present = need in html
            print(f"    含 {need}: {present}")
            ok = ok and present

        st, html2 = fetch(base, "/app.css")
        print(f"[2] app.css status={st} len={len(html2)}")
        for rule in ("safe-area-inset-bottom", ".remote-banner", "orientation: landscape"):
            present = rule in html2
            print(f"    含 {rule}: {present}")
            ok = ok and present

        st, js = fetch(base, "/app.js")
        print(f"[3] app.js status={st} len={len(js)}")
        for need in ("function initRemote", "goLogin", 'meta[name="qai-remote"]'):
            present = need in js
            print(f"    含 {need}: {present}")
            ok = ok and present
    finally:
        srv.shutdown()

    print("\n" + ("全部通过" if ok else "有检查未通过"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
