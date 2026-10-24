"""QAI 原生桌面窗口（Edge WebView2）。

由 launcher.py 的 open_window() 启动；不经过 Chrome 外壳。
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

import webview

ROOT = Path(__file__).resolve().parent
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_ASK_LOCK = threading.Lock()
_ASKING = False


def _base_url() -> str:
    """启动器地址由 launcher.py 通过命令行传入。

    端口不一定是 8082：8082 被别的程序占用时启动器会自动换端口，
    所以只能从传入的 URL 里取，不能写死。
    """
    url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8082/"
    parts = urllib.parse.urlsplit(url)
    if parts.scheme and parts.netloc:
        return f"{parts.scheme}://{parts.netloc}"
    return "http://127.0.0.1:8082"


def _api_token() -> str:
    """写接口令牌，由 launcher.py 作为第 2 个参数传进来。"""
    return sys.argv[2].strip() if len(sys.argv) > 2 else ""


def _api_headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    token = _api_token()
    if token:
        headers["X-QAI-Token"] = token
    return headers


def _run_stop_script() -> None:
    """API 不可用时，静默运行停止脚本兜底（/quiet 跳过暂停）。"""
    bat = ROOT / "停止QAI.bat"
    if not bat.is_file():
        return
    try:
        subprocess.Popen(
            ["cmd.exe", "/c", str(bat), "/quiet"],
            cwd=str(ROOT),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=_CREATE_NO_WINDOW,
        )
    except Exception:
        pass


def _close_behavior() -> str:
    """.env 里的 QAI_CLOSE_BEHAVIOR：

    ask（默认）= 关窗口时问一次「后台还是退出」
    exit       = 直接停机器人并退出启动器
    background = 只关界面，机器人在后台继续跑
    """
    try:
        for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith("QAI_CLOSE_BEHAVIOR"):
                value = s.split("=", 1)[1].split("#", 1)[0].strip().lower()
                return value or "ask"
    except Exception:
        pass
    return "ask"


def _ask_in_app(win) -> bool:
    """让页面弹出「后台 / 退出」选择框；成功返回 True。

    关键：**不能在关闭事件的 UI 线程上直接调 run_js/evaluate_js**。
    pywebview 的 edgechromium 实现里，evaluate_js 会 Invoke 到 UI 线程并
    semaphore.acquire() 等结果；如果调用者本身就是 UI 线程（FormClosing），
    就变成「UI 线程等自己」，窗口直接未响应。
    所以这里起一个后台线程，等 FormClosing 返回、消息循环恢复后再去调用。
    """
    global _ASKING
    with _ASK_LOCK:
        if _ASKING:
            return True           # 已经在问，别重复弹
        _ASKING = True

    def worker() -> None:
        global _ASKING
        try:
            time.sleep(0.08)      # 让 FormClosing 先返回，UI 线程回到消息循环
            # 先确认页面支持这个弹窗（老版本页面没有），否则会变成「点了 X 关不掉」
            supported = False
            try:
                supported = bool(win.evaluate_js("typeof window.__qaiAskClose === 'function'"))
            except Exception:
                supported = False
            if supported:
                run = getattr(win, "run_js", None)
                if callable(run):
                    run("window.__qaiAskClose()")
                else:
                    win.evaluate_js("window.__qaiAskClose()")
            else:
                _ask_fallback()
        except Exception:
            _ask_fallback()
        finally:
            _ASKING = False

    threading.Thread(target=worker, name="qai-close-ask", daemon=True).start()
    return True


def _ask_fallback() -> bool:
    """页面脚本不可用时的兜底：系统消息框，是=后台，否=退出。"""
    try:
        import ctypes

        MB_YESNO = 0x04
        MB_ICONQUESTION = 0x20
        MB_TOPMOST = 0x40000
        IDYES = 6
        r = ctypes.windll.user32.MessageBoxW(
            None,
            "要只关闭界面、让机器人在后台继续运行吗？\n\n"
            "「是」= 后台继续运行（再双击 QAI 可打开界面）\n"
            "「否」= 停止机器人并退出 QAI",
            "关闭 QAI",
            MB_YESNO | MB_ICONQUESTION | MB_TOPMOST,
        )
        if r == IDYES:
            return True       # 后台：允许关窗，不做 shutdown
    except Exception:
        pass
    _notify_shutdown()
    return True


def _on_closing(*_args) -> bool:
    """pywebview 的关闭事件：返回 False 会取消这次关闭。"""
    mode = _close_behavior()
    if mode == "background":
        return True
    if mode == "exit":
        _notify_shutdown()
        return True
    # ask：先拦下关闭，让页面弹选择框，用户选完由 /api/close/decision 收尾
    win = webview.windows[0] if getattr(webview, "windows", None) else None
    if win is not None and _ask_in_app(win):
        return False
    return _ask_fallback()


def _notify_shutdown() -> None:
    """关窗时的行为：默认停掉一切；设了 background 就只关界面。"""
    if _close_behavior() == "background":
        # 后台模式：不停止进程，下次双击 QAI 直接重开窗口。
        return
    ok = False
    base = _base_url()
    try:
        req = urllib.request.Request(
            f"{base}/api/shutdown",
            method="POST",
            data=b"{}",
            headers=_api_headers(),
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
                f"{base}/api/stop",
                method="POST",
                data=b"{}",
                headers=_api_headers(),
            )
            urllib.request.urlopen(stop_req, timeout=5).read()
        except Exception:
            pass
        # 旧启动器、接口异常或启动器卡死时，静默执行停止脚本。
        _run_stop_script()


def _env_flag(key: str) -> bool:
    """读 .env 里的开关（1/true/yes/on 为真）。"""
    try:
        for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith(key):
                value = s.split("=", 1)[1].split("#", 1)[0].strip().lower()
                return value in ("1", "true", "yes", "on")
    except Exception:
        pass
    return False


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
    window.events.closing += _on_closing
    webview.start(
        # .env 里设 QAI_DEVTOOLS=true 可以打开开发者工具（F12），排查界面问题用
        debug=_env_flag("QAI_DEVTOOLS"),
        private_mode=False,
        storage_path=str(ROOT / "data" / "webview"),
        icon=str(ROOT / "launcher_ui" / "qai.ico"),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
