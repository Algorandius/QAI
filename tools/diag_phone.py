"""一键诊断：手机为什么连不上。

直接照着用户能做的检查跑一遍，输出人话结论。
"""
from __future__ import annotations

import json
import os
import socket
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def port_listening(port: int) -> tuple[bool, str]:
    """检查端口是否在监听，并返回它绑在哪个地址。"""
    import subprocess
    try:
        out = subprocess.run(
            ["netstat", "-ano"], capture_output=True, text=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
    except Exception:
        return False, "无法执行 netstat"
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[3] == "LISTENING" and parts[1].endswith(f":{port}"):
            return True, parts[1]
    return False, ""


def main() -> int:
    ip = lan_ip()
    print("=" * 66)
    print("QAI 手机访问 · 诊断")
    print("=" * 66)

    ok = True

    # 1. 配置
    env = {}
    envf = ROOT / ".env"
    try:
        for line in envf.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s and not s.startswith("#") and "=" in s:
                k, v = s.split("=", 1)
                env[k.strip()] = v.strip()
    except Exception as e:
        print(f"\n[1] 读 .env 失败：{e}")
        ok = False

    host = env.get("WEB_PANEL_HOST", "(未设置)")
    has_pwd = bool(env.get("WEB_PANEL_PASSWORD_HASH") or env.get("WEB_PANEL_PASSWORD"))
    print(f"\n[1] 配置")
    print(f"    WEB_PANEL_HOST = {host}")
    print(f"    已设密码       = {has_pwd}")
    if host in ("127.0.0.1", "(未设置)", "localhost"):
        print("    ✗ 只监听本机，手机连不上。需要在管理台「手机」页打开开关。")
        ok = False
    else:
        print("    ✓ 已配置为对外监听")

    # 2. 服务是否在跑
    print(f"\n[2] 服务状态")
    for port, name in ((8081, "管理台"), (8082, "启动器")):
        up, addr = port_listening(port)
        if not up:
            print(f"    ✗ {name} {port} 没有监听 —— QAI 没运行？")
            ok = False
        else:
            print(f"    ✓ {name} {port} 监听在 {addr}")
            if addr.startswith("127.0.0.1") and port == 8081:
                print(f"       ✗ 只绑定了本机回环，手机连不上！")
                print(f"         说明当前跑的是旧版 QAI，不认 WEB_PANEL_HOST。")
                ok = False

    # 3. 本机能否访问
    print(f"\n[3] 本机访问测试")
    try:
        with urllib.request.urlopen(f"http://{ip}:8081/api/status", timeout=5) as r:
            print(f"    ✓ http://{ip}:8081 可访问（{r.status}）")
    except Exception as e:
        print(f"    ✗ http://{ip}:8081 打不开：{type(e).__name__}")
        ok = False

    # 4. 代码版本
    print(f"\n[4] 代码版本")
    wp = ROOT / "src" / "plugins" / "webpanel.py"
    try:
        has = "WEB_PANEL_HOST" in wp.read_text(encoding="utf-8")
        print(f"    {'✓' if has else '✗'} 当前目录支持 WEB_PANEL_HOST：{has}")
        if not has:
            ok = False
    except Exception:
        print("    ✗ 找不到 webpanel.py")

    print("\n" + "=" * 66)
    print(f"手机应该打开：http://{ip}:8081")
    print("=" * 66)
    if ok:
        print("各项检查通过。如果手机还打不开，多半是：")
        print("  · 手机不在同一个 WiFi")
        print("  · 路由器开了「AP 隔离」，设备之间不能互访")
        print("  · Windows 防火墙拦了 8081")
    else:
        print("上面标 ✗ 的项需要先修。")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
