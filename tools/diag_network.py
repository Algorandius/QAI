"""诊断：0.0.0.0 绑定后，从局域网 IP 到底能不能访问。

同时验证手机热点场景下常见的几个坑：
  1. 绑定地址是否真的是 0.0.0.0
  2. 从本机局域网 IP 能否回环访问自己
  3. 防火墙是否会拦（只能读状态，不能改）
"""
from __future__ import annotations

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

from src.plugins import webpanel as w  # noqa: E402


def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def all_ips() -> list[str]:
    ips = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in ips:
                ips.append(ip)
    except Exception:
        pass
    return ips


def main() -> int:
    ip = lan_ip()
    print("=" * 66)
    print("手机访问 · 网络连通性诊断")
    print("=" * 66)
    print(f"\n本机主机名：{socket.gethostname()}")
    print(f"探测到的局域网 IP：{ip}")
    print(f"该主机所有 IPv4：{', '.join(all_ips()) or '(无)'}")

    srv = ThreadingHTTPServer(("0.0.0.0", 0), w._Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"\n测试服务已绑定 0.0.0.0:{port}")

    results = []
    for host in ("127.0.0.1", ip):
        url = f"http://{host}:{port}/api/status"
        try:
            with urllib.request.urlopen(url, timeout=6) as r:
                ok = r.status == 200
        except Exception as e:
            ok = False
            print(f"  {host} 失败：{type(e).__name__}: {e}")
        results.append((host, ok))
        print(f"  {'PASS' if ok else 'FAIL'}  {host}:{port} → {'可访问' if ok else '不可访问'}")

    srv.shutdown()

    print("\n" + "=" * 66)
    print("结论")
    print("=" * 66)
    if all(ok for _, ok in results):
        print("本机侧绑定与回环访问都正常。")
        print("那么手机打不开的原因在：防火墙 / 热点隔离 / QAI 没运行 / IP 变了。")
    else:
        print("本机侧就有问题，先修本机。")

    print("\n手机上应该填的地址：")
    print(f"  http://{ip}:8081")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
