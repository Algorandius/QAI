"""QAI 原生桌面窗口（Edge WebView2）。

由 launcher.py 的 open_window() 启动；不经过 Chrome 外壳。
"""
from __future__ import annotations

import sys
from pathlib import Path

import webview

ROOT = Path(__file__).resolve().parent


def main() -> int:
    url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8082/"
    storage = ROOT / "data" / "webview"
    storage.mkdir(parents=True, exist_ok=True)
    webview.create_window(
        "QAI",
        url,
        width=1360,
        height=900,
        min_size=(1080, 700),
        background_color="#06131d",
        confirm_close=False,
    )
    webview.start(
        debug=False,
        private_mode=False,
        storage_path=str(ROOT / "data" / "webview"),
        icon=str(ROOT / "launcher_ui" / "qai.ico"),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
