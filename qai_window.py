"""QAI 原生桌面窗口（Edge WebView2）。

由 launcher.py 的 open_window() 启动；不经过 Chrome 外壳。
"""
from __future__ import annotations

import json
import subprocess
import sys
import urllib.request
from pathlib import Path

import webview

ROOT = Path(__file__).resolve().parent
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _run_stop_script() -> None:
    """API 不可用时，静默运行停止脚本兜底。"""
    bat = ROOT / "停止QAI.bat"
    if not bat.is_file():
        return
    try:
        subprocess.Popen(
            ["cmd.exe", "/c", str(bat)],
            cwd=str(ROOT),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=_CREATE_NO_WINDOW,
        )
    except Exception:
        pass


def _notify_shutdown() -> None:
    """关窗时通知启动器停止机器人并退出，避免残留 8082 进程。"""
    ok = False
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:8082/api/shutdown",
            method="POST",
            data=b"{}",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            body = json.loads(resp.read() or b"{}")
            ok = resp.status == 200 and bool(body.get("ok"))
    except Exception:
        ok = False
    if not ok:
        # 旧启动器没有 /api/shutdown 时，先用旧接口停机器人，再静默停启动器。
        try:
            stop_req = urllib.request.Request(
                "http://127.0.0.1:8082/api/stop",
                method="POST",
                data=b"{}",
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(stop_req, timeout=5).read()
        except Exception:
            pass
        # 旧启动器、接口异常或启动器卡死时，静默执行停止脚本。
        _run_stop_script()


def main() -> int:
    url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8082/"
    storage = ROOT / "data" / "webview"
    storage.mkdir(parents=True, exist_ok=True)
    window = webview.create_window(
        "QAI",
        url,
        width=1360,
        height=900,
        min_size=(1080, 700),
        background_color="#06131d",
        confirm_close=False,
    )
    window.events.closed += _notify_shutdown
    webview.start(
        debug=False,
        private_mode=False,
        storage_path=str(ROOT / "data" / "webview"),
        icon=str(ROOT / "launcher_ui" / "qai.ico"),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
