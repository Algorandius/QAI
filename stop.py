"""QAI 安全停止脚本：只结束本目录启动的 QAI 进程。

为什么不再「按端口杀进程」
------------------------------------------------------------------
旧版 ``停止QAI.bat`` 的写法是：

    netstat -ano | findstr ":8080" ... taskkill /F /PID

8080 / 8081 是最常见的开发端口（Vue、Spring Boot、Tomcat、Jenkins 都默认用它们）。
用户一边开着别的项目、一边点「停止 QAI」，那些进程会被直接强杀。

现在的做法：四路互证，都不用 WMI（部分机器 / 受限账户下 Get-CimInstance 会直接被拒绝）
------------------------------------------------------------------
1. **pid 文件**：``data/qai_pids.json`` 由启动器在启动时写入，同时要求该 PID
   确实在监听 QAI 的端口（防 PID 被回收后误杀别的进程）
2. **HTTP 身份**：QAI 启动器的 ``/api/status`` 会返回 ``app=qai-launcher`` 和
   它管理的机器人 PID；管理台 ``/api/models`` 也是 QAI 独有的返回结构
3. **窗口标题**：``EnumWindows`` 找标题为 ``QAI`` 的可见窗口（不需要任何特权）
4. **命令行/可执行路径**：能用 WMI 或 ``OpenProcess`` 时作为额外证据

任何一路确认过就停；确认不了的一律跳过并说明原因，绝不盲杀。
这样「QAI 卡死需要强制停止」和「端口被别的程序占着」两种情况都不会误伤。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
ENV_FILE = ROOT / ".env"
PID_FILE = DATA / "qai_pids.json"
WINDOW_PID_FILE = DATA / "qai_window.pid"

DEFAULT_PORTS = {"PORT": 8080, "WEB_PANEL_PORT": 8081, "LAUNCHER_PORT": 8082}
# 结束顺序：机器人 → 窗口 → 启动器，避免启动器先没了留下孤儿进程
ROLE_ORDER = {"bot": 0, "window": 1, "launcher": 2, "": 3}

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
# 不传 CREATE_NO_WINDOW：stdout 重定向到文件时该标志会让子进程
# 以 STATUS_DLL_INIT_FAILED(0xC0000142) 失败。


# ---------------------------------------------------------------- 小工具
def _say(msg: str) -> None:
    try:
        print(f"[QAI] {msg}", flush=True)
    except Exception:
        pass


def _safe_int(raw) -> int:
    try:
        v = int(str(raw).strip())
    except (TypeError, ValueError):
        return 0
    return v if v > 0 else 0


def _read_env_ports() -> tuple[dict[str, int], set[int]]:
    """从 .env 读端口配置，返回 ({名字: 端口}, 全部端口)，读不到就用默认值。"""
    values = dict(DEFAULT_PORTS)
    try:
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, raw = line.partition("=")
            key = key.strip()
            if key in DEFAULT_PORTS:
                port = _safe_int(raw.strip().strip('"').strip("'"))
                if 0 < port < 65536:
                    values[key] = port
    except Exception:
        pass
    return values, set(values.values())


def _read_pid_json() -> dict[str, int]:
    """读 data/qai_pids.json；坏文件当作没有，不抛异常。"""
    out: dict[str, int] = {}
    try:
        data = json.loads(PID_FILE.read_text(encoding="utf-8"))
    except Exception:
        return out
    if not isinstance(data, dict):
        return out
    for key in ("launcher", "bot", "window"):
        pid = _safe_int(data.get(key))
        if pid and pid != os.getpid():
            out[key] = pid
    return out


def _read_window_pid() -> int:
    try:
        return _safe_int(WINDOW_PID_FILE.read_text(encoding="utf-8"))
    except Exception:
        return 0


def _read_recorded_launcher_port() -> int:
    """启动器端口不一定等于 .env 里的值：8082 被占用时它会自动换一个。"""
    try:
        data = json.loads(PID_FILE.read_text(encoding="utf-8"))
    except Exception:
        return 0
    if not isinstance(data, dict):
        return 0
    port = _safe_int(data.get("launcher_port"))
    return port if 0 < port < 65536 else 0


def _hidden_startupinfo():
    """隐藏子进程的控制台窗口。

    stop.py 正常是从 .bat 的控制台里跑，子进程会继承那个控制台、不会弹新窗口；
    但 qai_window.py 兜底调用时整条链都没有控制台，此时 netstat/taskkill
    会各自弹一个黑框出来。加上 SW_HIDE 两种情况都干净。
    """
    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = subprocess.SW_HIDE
        return si
    except Exception:
        return None


def _run_capture(args: list[str], timeout: float) -> bytes:
    """执行命令并捕获输出，不弹黑窗口。

    用临时文件而不是 subprocess.PIPE：受限账户或安全软件下创建匿名管道会报
    WinError 5；也能避免输出很长时管道写满导致卡死。
    """
    fd, path = tempfile.mkstemp(prefix="qai-stop-", suffix=".out")
    try:
        with os.fdopen(fd, "wb") as fh:
            try:
                subprocess.run(
                    args, stdout=fh, stderr=subprocess.DEVNULL,
                    timeout=timeout, startupinfo=_hidden_startupinfo(),
                )
            except Exception:
                return b""
        try:
            return Path(path).read_bytes()
        except Exception:
            return b""
    finally:
        try:
            Path(path).unlink(missing_ok=True)
        except Exception:
            pass


def _listeners(ports: set[int]) -> dict[int, set[int]]:
    """返回 {pid: {它监听的端口}}，只取 LISTENING 状态的 TCP 监听者。"""
    found: dict[int, set[int]] = {}
    raw = _run_capture(["netstat", "-ano", "-p", "TCP"], 15)
    if not raw:
        return found
    for line in raw.decode("utf-8", "replace").splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() != "TCP":
            continue
        local, foreign, state = parts[1], parts[2], parts[3].upper()
        # 状态名不做本地化假设：LISTENING 或对端端口为 0（未建立连接）
        if not (state.startswith("LISTEN") or foreign.endswith(":0")):
            continue
        if ":" not in local:
            continue
        try:
            port = int(local.rsplit(":", 1)[1])
        except ValueError:
            continue
        if port not in ports:
            continue
        pid = _safe_int(parts[4])
        if pid and pid != os.getpid():
            found.setdefault(pid, set()).add(port)
    return found


# ---------------------------------------------------------------- 身份校验
def _http_json(port: int, path: str, timeout: float = 1.5) -> dict | None:
    try:
        with _OPENER.open(f"http://127.0.0.1:{port}{path}", timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
            return data if isinstance(data, dict) else None
    except Exception:
        return None


def _probe_launcher(port: int) -> dict | None:
    """确认这个端口上是 QAI 启动器，并顺带拿到它管理的机器人 PID。"""
    data = _http_json(port, "/api/status")
    if data and str(data.get("app") or "") == "qai-launcher":
        return data
    return None


def _probe_panel(port: int) -> bool:
    """确认这个端口上是 QAI 管理台（返回结构里有固定模型目录）。"""
    data = _http_json(port, "/api/models")
    if not data or not data.get("ok"):
        return False
    ids = {str(p.get("id")) for p in (data.get("providers") or []) if isinstance(p, dict)}
    return bool(ids & {"zhipu", "deepseek", "qwen", "kimi", "hunyuan"})


def _qai_window_pids() -> set[int]:
    """标题为 QAI 的可见窗口所属 PID（只用 user32，不需要特权）。"""
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        pids: set[int] = set()
        CB = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def cb(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            n = user32.GetWindowTextLengthW(hwnd)
            if n <= 0:
                return True
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            if buf.value.strip() == "QAI":
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value:
                    pids.add(int(pid.value))
            return True

        user32.EnumWindows(CB(cb), 0)
        return pids
    except Exception:
        return set()


def _process_infos(pids: list[int]) -> dict[int, dict[str, str]]:
    """可选证据：一次取到 (可执行路径, 命令行)。取不到就返回空。"""
    pids = sorted({p for p in pids if p > 0})
    if not pids:
        return {}
    filt = " or ".join(f"ProcessId={p}" for p in pids)
    script = (
        "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
        f"Get-CimInstance Win32_Process -Filter '{filt}' | "
        "ForEach-Object { "
        "$c = $_.CommandLine; if ($null -eq $c) { $c = '' }; "
        "'{0}|{1}|{2}' -f $_.ProcessId, $_.ExecutablePath, $c }"
    )
    infos: dict[int, dict[str, str]] = {}
    raw = _run_capture(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script], 30
    )
    if not raw:
        return infos
    for line in raw.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line or "|" not in line:
            continue
        pid_s, exe, cmd = line.split("|", 2)
        pid = _safe_int(pid_s)
        if pid:
            infos[pid] = {"exe": exe.strip(), "cmd": cmd.strip()}
    return infos


def _exe_path_only(pid: int) -> str:
    """没有 WMI 时的兜底：只拿可执行文件路径。"""
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        handle = kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return ""
        try:
            size = wintypes.DWORD(1024)
            buf = ctypes.create_unicode_buffer(size.value)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return buf.value
            return ""
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return ""


def _path_under(info: dict[str, str] | None, root: Path) -> bool:
    if not info:
        return False
    root_s = str(root).lower().rstrip("\\/")
    if not root_s:
        return False
    return root_s in (info.get("exe") or "").lower() or root_s in (info.get("cmd") or "").lower()


# ---------------------------------------------------------------- 执行
def _kill(pid: int) -> bool:
    try:
        proc = subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=10,
        )
        return proc.returncode == 0
    except Exception:
        return False


def _cleanup_pid_files() -> None:
    for path in (PID_FILE, WINDOW_PID_FILE):
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass


def main() -> int:
    if os.name != "nt":
        _say("这个脚本只适用于 Windows。")
        return 0

    dry_run = any(a in ("-n", "--dry-run", "/n") for a in sys.argv[1:])
    env_ports, ports = _read_env_ports()
    launcher_port = env_ports["LAUNCHER_PORT"]
    # 8082 被别的程序占用时启动器会换端口，pid 文件里记着真实端口，要一起扫
    recorded_launcher_port = _read_recorded_launcher_port()
    if recorded_launcher_port:
        ports.add(recorded_launcher_port)
    listeners = _listeners(ports)          # pid -> 监听中的端口
    window_pids = _qai_window_pids()
    recorded = _read_pid_json()

    trusted: dict[int, tuple[str, str]] = {}   # pid -> (角色, 依据)
    skipped: list[tuple[int, str]] = []

    def trust(pid: int, role: str, reason: str) -> None:
        if not pid or pid == os.getpid():
            return
        cur = trusted.get(pid)
        if cur is None:
            trusted[pid] = (role, reason)
        elif not cur[0] and role:
            trusted[pid] = (role, f"{cur[1]} + {reason}")

    # ---- 依据 1：本目录启动器写下的 pid 文件（要求端口仍在监听，避免 PID 被回收）
    for role, pid in recorded.items():
        if role == "window":
            if pid in window_pids:
                trust(pid, "window", "窗口 pid 文件")
            else:
                skipped.append((pid, "窗口 pid 文件 → 已不是 QAI 窗口（可能已关闭）"))
            continue
        if pid in listeners:
            trust(pid, role, "pid 文件 + 端口在监听")
        else:
            skipped.append((pid, f"pid 文件（{role}）→ 未在监听 QAI 端口（可能已退出）"))

    legacy_window = _read_window_pid()
    if legacy_window:
        if legacy_window in window_pids:
            trust(legacy_window, "window", "窗口 pid 文件（旧版）")
        elif not any(legacy_window == p for p in recorded.values()):
            skipped.append((legacy_window, "窗口 pid 文件（旧版）→ 已不是 QAI 窗口"))

    # ---- 依据 2：启动器 / 管理台的 HTTP 身份
    launcher_info = None
    probe_ports = ([recorded_launcher_port] if recorded_launcher_port else []) + [launcher_port]
    for probe in dict.fromkeys(probe_ports):
        launcher_info = _probe_launcher(probe)
        if launcher_info:
            launcher_port = probe
            break
    if launcher_info:
        for pid, pset in listeners.items():
            if launcher_port in pset:
                trust(pid, "launcher", "启动器 HTTP 身份校验")
        bot_pid = _safe_int(launcher_info.get("pid"))
        if bot_pid and bot_pid in listeners:
            trust(bot_pid, "bot", "启动器报告的机器人进程")
    panel_port = env_ports["WEB_PANEL_PORT"]
    if panel_port in {p for pset in listeners.values() for p in pset} and _probe_panel(panel_port):
        for pid, pset in listeners.items():
            if panel_port in pset and pid not in trusted:
                trust(pid, "bot", "管理台 HTTP 身份校验")

    # ---- 依据 3：标题为 QAI 的窗口
    for pid in window_pids:
        trust(pid, "window", "标题为 QAI 的窗口")

    # ---- 还没有归属的端口占用者：用命令行/可执行路径再核一次
    unknown = [pid for pid in listeners if pid not in trusted]
    if unknown:
        infos = _process_infos(unknown)
        for pid in unknown:
            info = infos.get(pid)
            if info is None:
                exe = _exe_path_only(pid)
                info = {"exe": exe, "cmd": ""} if exe else None
            if _path_under(info, ROOT):
                trust(pid, "", "命令行指向本目录")
                continue
            where = ",".join(str(p) for p in sorted(listeners[pid]))
            exe = (info or {}).get("exe") or "未知程序"
            skipped.append((pid, f"端口 {where} → {exe}"))

    if not trusted:
        _say("没有找到属于本目录的 QAI 进程。")
        if skipped:
            _say("以下进程占着 QAI 的端口，但确认不了是 QAI，已跳过：")
            for pid, desc in skipped:
                _say(f"  PID {pid}  {desc}")
            _say("如果确实要停它们，请用任务管理器手动结束。")
        else:
            _say("QAI 没有在运行。")
        _cleanup_pid_files()
        return 0

    order = sorted(trusted.items(), key=lambda item: ROLE_ORDER.get(item[1][0], 3))
    label = {"bot": "机器人", "window": "窗口", "launcher": "启动器", "": "进程"}
    if dry_run:
        _say("只检查，不结束（--dry-run）")
        for pid, (role, reason) in order:
            _say(f"会结束{label.get(role, '进程')} PID {pid}（{reason}）")
        if skipped:
            _say("已跳过（确认不了是 QAI，或已经退出）：")
            for pid, desc in skipped:
                _say(f"  PID {pid}  {desc}")
        return 0

    failed: list[int] = []
    for pid, (role, reason) in order:
        if _kill(pid):
            _say(f"已结束{label.get(role, '进程')} PID {pid}（{reason}）")
        else:
            failed.append(pid)

    _cleanup_pid_files()

    if skipped:
        _say("已跳过（确认不了是 QAI，或已经退出）：")
        for pid, desc in skipped:
            _say(f"  PID {pid}  {desc}")
    if failed:
        _say("以下进程结束失败，请用任务管理器手动结束：")
        for pid in failed:
            _say(f"  PID {pid}")
        return 1
    _say("QAI 已停止。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
