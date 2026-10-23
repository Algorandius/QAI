"""
QAI 桌面控制中心（本地启动器）

一个窗口里做三件事：
1. 启动 / 停止 / 重启机器人（不需要再双击 run.bat）
2. 查看机器人、管理面板、NapCat、反向 WebSocket 的连接状态
3. 内嵌 8081 的完整管理控制台；需要时一键以管理员权限拉起 NapCat

启动方式：
- 双击 `启动QAI.vbs`（无黑窗口）
- 或 `python launcher.py`（有控制台，方便排错）

它只监听 127.0.0.1，默认端口 8082（可用 LAUNCHER_PORT 覆盖）。
关闭启动器窗口不会自动停机器人；下次打开会检测到 8080/8081 并“接管”。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
UI_DIR = ROOT / "launcher_ui"
LOG_DIR = ROOT / "logs"
ENV_FILE = ROOT / ".env"
BOT_LOG = LOG_DIR / "launcher-bot.log"
LAUNCHER_LOG = LOG_DIR / "launcher.log"
LOG_DIR.mkdir(parents=True, exist_ok=True)


def _read_env() -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, v = s.split("=", 1)
            out[k.strip()] = v.strip()
    except Exception:
        pass
    return out


ENV = _read_env()


def reload_env() -> dict[str, str]:
    global ENV
    ENV = _read_env()
    return ENV


def _update_env(updates: dict[str, str]) -> None:
    """更新 .env 指定键，保留注释和其余行。"""
    if not ENV_FILE.exists():
        ENV_FILE.write_text("", encoding="utf-8")
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    seen: set[str] = set()
    for i, line in enumerate(lines):
        m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if not m:
            continue
        key = m.group(1)
        if key in updates:
            lines[i] = f"{key}={updates[key]}"
            seen.add(key)
    for key, val in updates.items():
        if key not in seen:
            lines.append(f"{key}={val}")
    tmp = ENV_FILE.parent / (ENV_FILE.name + ".tmp")
    tmp.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    tmp.replace(ENV_FILE)


def _normalize_ids(raw, label: str) -> list[str]:
    if isinstance(raw, list):
        parts = raw
    else:
        parts = re.split(r"[,，\s]+", str(raw or ""))
    out: list[str] = []
    for p in parts:
        p = str(p).strip()
        if not p:
            continue
        if not re.fullmatch(r"\d{5,12}", p):
            raise ValueError(f"{label}不合法：{p}（应为 5~12 位数字）")
        if p not in out:
            out.append(p)
    return out


def setup_status() -> dict:
    env = _read_env()
    key = env.get("LLM_API_KEY", "").strip()
    napdir = env.get("NAPCAT_DIR", "").strip()
    nap_ok = bool(napdir) and (Path(napdir) / "launcher-win10.bat").is_file()
    provider = env.get("LLM_PROVIDER", "").strip() or _infer_provider_from_base(env.get("LLM_BASE_URL", ""))
    if provider in ("tencent", "tencent_intl"):
        provider = "hunyuan"
    provider_keys = {
        pid: bool(env.get(env_key, "").strip())
        for pid, env_key in _PROVIDER_KEY_ENV.items()
    }
    if provider_keys.get("hunyuan") is False:
        provider_keys["hunyuan"] = bool(
            env.get("LLM_KEY_TENCENT", "").strip()
            or env.get("LLM_KEY_TENCENT_INTL", "").strip()
        )
    if provider and key:
        provider_keys[provider] = True
    return {
        "ok": True,
        "needs_setup": not bool(key) or not nap_ok,
        "llm_key_set": bool(key),
        "llm_provider": provider,
        "provider_keys": provider_keys,
        "llm_base_url": env.get("LLM_BASE_URL", "https://api.deepseek.com/v1"),
        "llm_model": env.get("LLM_MODEL", "deepseek-flash"),
        "admin_qq": env.get("BOT_ADMIN_QQ", ""),
        "allowed_groups": env.get("ALLOWED_GROUPS", ""),
        "napcat_dir": napdir,
        "napcat_default_dir": str(ROOT / "napcat"),
        "napcat_ok": nap_ok,
        "napcat_running": port_open(NAPCAT_PORT),
        "napcat_url": napcat_webui_url(),
        "env_path": str(ENV_FILE),
        "port": PANEL_PORT,
    }


def setup_detect() -> dict:
    """自动探测 NapCat / QQ 的常见安装位置。"""
    env = _read_env()
    candidates = [
        env.get("NAPCAT_DIR", ""),
        os.getenv("NAPCAT_DIR", ""),
        str(ROOT / "napcat"),
        r"E:\ChatBot\napcat",
        r"D:\ChatBot\napcat",
        r"C:\ChatBot\napcat",
        str(Path.home() / "napcat"),
        r"D:\napcat",
        r"C:\napcat",
    ]
    nap: list[dict] = []
    seen: set[str] = set()
    for raw in candidates:
        if not raw:
            continue
        p = Path(str(raw)).expanduser()
        try:
            key = str(p.resolve()).lower()
        except Exception:
            key = str(p).lower()
        if key in seen or not p.is_dir():
            continue
        seen.add(key)
        nap.append({
            "path": str(p),
            "launcher": (p / "launcher-win10.bat").is_file(),
            "webui": (p / "config" / "webui.json").is_file(),
        })
    qq_candidates = [
        r"D:\QQ\QQ.exe",
        r"C:\Program Files\Tencent\QQNT\QQ.exe",
        r"C:\Program Files (x86)\Tencent\QQNT\QQ.exe",
        str(Path(os.getenv("LOCALAPPDATA", "")) / "Programs" / "QQNT" / "QQ.exe"),
    ]
    qq = [p for p in qq_candidates if p and Path(p).is_file()]
    return {"ok": True, "napcat": nap, "qq": qq, "napcat_running": port_open(NAPCAT_PORT)}


def setup_save(data: dict) -> dict:
    """首次配置：写 .env，可选重启/启动机器人。"""
    global NAPCAT_DIR, _NAPCAT_TOKEN, NAPCAT_PORT
    env = _read_env()
    provider = str(data.get("llm_provider") or env.get("LLM_PROVIDER") or "").strip()
    current_provider = env.get("LLM_PROVIDER", "").strip() or _infer_provider_from_base(env.get("LLM_BASE_URL", ""))
    key_env = _PROVIDER_KEY_ENV.get(provider, "")
    key = str(data.get("llm_api_key") or "").strip()
    if not key:
        key = (env.get(key_env, "").strip() if key_env else "") or (
            env.get("LLM_API_KEY", "").strip()
            if provider and provider == current_provider
            else ""
        )
    if not key:
        label = provider or "所选模型"
        return {"ok": False, "error": f"{label} 还没有保存过 API Key，请填写"}
    base = str(data.get("llm_base_url") or env.get("LLM_BASE_URL") or "").strip()
    if not re.match(r"^https?://", base):
        return {"ok": False, "error": "LLM_BASE_URL 必须以 http:// 或 https:// 开头"}
    model = str(data.get("llm_model") or env.get("LLM_MODEL") or "").strip()
    if not model:
        return {"ok": False, "error": "请选择模型"}
    admin = _normalize_ids(data.get("admin_qq"), "管理员 QQ")
    groups = _normalize_ids(data.get("allowed_groups"), "群号")
    napdir = str(data.get("napcat_dir") or env.get("NAPCAT_DIR") or "").strip()
    if not napdir:
        return {"ok": False, "error": "请填写 NapCat 目录（包含 launcher-win10.bat）"}
    if not (Path(napdir) / "launcher-win10.bat").is_file():
        return {"ok": False, "error": f"NapCat 目录里没有 launcher-win10.bat：{napdir}"}
    updates = {
        "LLM_PROVIDER": provider,
        "LLM_API_KEY": key,
        "LLM_BASE_URL": base,
        "LLM_MODEL": model,
        "BOT_ADMIN_QQ": ",".join(admin),
        "ALLOWED_GROUPS": ",".join(groups),
        "NAPCAT_DIR": napdir,
    }
    if key_env and str(data.get("llm_api_key") or "").strip():
        updates[key_env] = key
    old_key = env.get("LLM_API_KEY", "").strip()
    old_provider = env.get("LLM_PROVIDER", "").strip() or _infer_provider_from_base(env.get("LLM_BASE_URL", ""))
    old_key_env = _PROVIDER_KEY_ENV.get(old_provider, "")
    if old_key and old_key_env and old_provider != provider and not env.get(old_key_env, "").strip():
        updates[old_key_env] = old_key
    try:
        _update_env(updates)
    except Exception as e:
        return {"ok": False, "error": f"写入 .env 失败：{e}"}
    reload_env()
    NAPCAT_DIR = Path(napdir)
    _NAPCAT_TOKEN = None
    try:
        NAPCAT_PORT = int(_read_env().get("NAPCAT_PORT") or NAPCAT_PORT)
    except ValueError:
        pass
    started = False
    if data.get("start"):
        if port_open(PANEL_PORT) or port_open(BOT_PORT):
            stop_bot()
        r = start_bot()
        started = bool(r.get("ok"))
        if not r.get("ok"):
            return {"ok": False, "error": r.get("error") or "配置已保存，但机器人启动失败", "saved": True}
    log("[launcher] 首次配置已保存")
    return {"ok": True, "message": "配置已保存", "started": started}


def _env_int(key: str, default: int) -> int:
    raw = os.getenv(key) or ENV.get(key) or str(default)
    try:
        return int(str(raw).strip())
    except ValueError:
        return default


def _env_bool(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None:
        raw = ENV.get(key)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


BOT_PORT = _env_int("PORT", 8080)
PANEL_PORT = _env_int("WEB_PANEL_PORT", 8081)
NAPCAT_PORT = _env_int("NAPCAT_PORT", 6099)
LAUNCHER_PORT = _env_int("LAUNCHER_PORT", 8082)
AUTO_START = _env_bool("LAUNCHER_AUTO_START", True)
NAPCAT_AUTO_START = _env_bool("NAPCAT_AUTO_START", False)
NAPCAT_DIR = Path(os.getenv("NAPCAT_DIR") or ENV.get("NAPCAT_DIR") or (ROOT / "napcat"))
PANEL_URL = f"http://127.0.0.1:{PANEL_PORT}/"
NAPCAT_WEBUI = f"http://127.0.0.1:{NAPCAT_PORT}/webui/"
APP_NAME = "qai-launcher"

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_LOCK = threading.RLock()
_BOT_PROC: subprocess.Popen | None = None
_NAPCAT_TOKEN: str | None = None
_NAPCAT_INSTALL_LOCK = threading.Lock()
_NAPCAT_INSTALL = {"status": "idle", "message": "", "progress": 0, "path": ""}

_PROVIDER_KEY_ENV = {
    "zhipu": "LLM_KEY_ZHIPU",
    "deepseek": "LLM_KEY_DEEPSEEK",
    "qwen": "LLM_KEY_QWEN",
    "kimi": "LLM_KEY_KIMI",
    "hunyuan": "LLM_KEY_HUNYUAN",
    "tencent": "LLM_KEY_TENCENT",
    "tencent_intl": "LLM_KEY_TENCENT_INTL",
}


def _infer_provider_from_base(base: str) -> str:
    b = (base or "").lower()
    if "bigmodel" in b:
        return "zhipu"
    if "deepseek" in b:
        return "deepseek"
    if "dashscope" in b:
        return "qwen"
    if "moonshot" in b:
        return "kimi"
    if "tokenhub" in b:
        return "hunyuan"
    return ""


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    try:
        with (LAUNCHER_LOG).open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    try:
        print(line, flush=True)
    except Exception:
        pass


# ---------------------------------------------------------------- 探测
def port_open(port: int, timeout: float = 0.25) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def panel_up() -> bool:
    try:
        with _OPENER.open(f"{PANEL_URL}api/status", timeout=0.8) as r:
            return r.status == 200
    except Exception:
        return False


def napcat_token() -> str:
    """从 NapCat 的 webui.json 读 token；读不到就返回空串。"""
    global _NAPCAT_TOKEN
    if _NAPCAT_TOKEN is not None:
        return _NAPCAT_TOKEN
    try:
        data = json.loads((NAPCAT_DIR / "config" / "webui.json").read_text(encoding="utf-8"))
        _NAPCAT_TOKEN = str(data.get("token") or "")
    except Exception:
        _NAPCAT_TOKEN = ""
    return _NAPCAT_TOKEN


def napcat_webui_url() -> str:
    """带 token 的 NapCat WebUI 地址，方便直接嵌入应用。"""
    base = f"http://127.0.0.1:{NAPCAT_PORT}/webui"
    token = napcat_token()
    return base + ("?token=" + urllib.parse.quote(token) if token else "")


def napcat_token_info() -> dict:
    """给界面用的 token 信息（自动读取，不用翻日志）。"""
    path = NAPCAT_DIR / "config" / "webui.json"
    token = napcat_token()
    masked = ""
    if token:
        masked = token if len(token) <= 6 else token[:4] + "…" + token[-3:]
    return {
        "ok": True,
        "has_token": bool(token),
        "token": token,
        "masked": masked,
        "path": str(path),
        "exists": path.is_file(),
        "webui_url": napcat_webui_url(),
    }


def netstat_lines() -> list[str]:
    try:
        r = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True, text=True, timeout=3,
            creationflags=_CREATE_NO_WINDOW,
        )
        return r.stdout.splitlines()
    except Exception:
        return []


def pid_on_port(port: int, lines: list[str] | None = None) -> int | None:
    for line in lines if lines is not None else netstat_lines():
        if f":{port}" not in line or "LISTENING" not in line:
            continue
        parts = line.split()
        if parts and parts[-1].isdigit():
            return int(parts[-1])
    return None


def ws_connected(lines: list[str] | None = None) -> bool:
    for line in lines if lines is not None else netstat_lines():
        if f":{BOT_PORT}" in line and "ESTABLISHED" in line:
            return True
    return False


def log_tail(n: int = 60) -> str:
    if not BOT_LOG.exists():
        return "（还没有启动日志）"
    try:
        lines = BOT_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-max(1, min(n, 300)):])
    except Exception as e:
        return f"读取日志失败：{e}"


def status() -> dict:
    lines = netstat_lines()
    bot = port_open(BOT_PORT)
    panel = port_open(PANEL_PORT) and panel_up()
    nap = port_open(NAPCAT_PORT)
    pid = pid_on_port(BOT_PORT, lines) or pid_on_port(PANEL_PORT, lines)
    return {
        "app": APP_NAME,
        "ok": True,
        "bot_running": bool(bot or panel),
        "panel_up": panel,
        "napcat_up": nap,
        "ws_connected": ws_connected(lines),
        "pid": pid,
        "bot_port": BOT_PORT,
        "panel_port": PANEL_PORT,
        "napcat_port": NAPCAT_PORT,
        "panel_url": PANEL_URL,
        "napcat_url": napcat_webui_url(),
        "napcat_dir": str(NAPCAT_DIR),
        "napcat_token_ready": bool(napcat_token()),
        "napcat_config": str(NAPCAT_DIR / "config" / "webui.json"),
        "napcat_auto_start": NAPCAT_AUTO_START,
        "auto_start": AUTO_START,
        "log": log_tail(50),
        "time": time.strftime("%H:%M:%S"),
    }


# ---------------------------------------------------------------- 进程控制
def _wait_port(port: int, want: bool, timeout: float = 8.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if port_open(port) == want:
            return True
        time.sleep(0.2)
    return port_open(port) == want


def start_bot() -> dict:
    global _BOT_PROC
    with _LOCK:
        if port_open(BOT_PORT) or port_open(PANEL_PORT):
            return {"ok": True, "started": False, "message": "机器人已经在运行（已接管）"}
        if _BOT_PROC is not None and _BOT_PROC.poll() is None:
            return {"ok": True, "started": False, "message": "机器人正在启动中"}
        try:
            f = BOT_LOG.open("ab", buffering=0)
        except Exception as e:
            return {"ok": False, "error": f"打不开日志文件：{e}"}
        try:
            _BOT_PROC = subprocess.Popen(
                [sys.executable, "bot.py"],
                cwd=str(ROOT),
                stdout=f,
                stderr=subprocess.STDOUT,
                creationflags=_CREATE_NO_WINDOW,
            )
        except Exception as e:
            f.close()
            return {"ok": False, "error": f"启动失败：{e}"}
        finally:
            try:
                f.close()
            except Exception:
                pass
        log(f"[launcher] 启动机器人 pid={_BOT_PROC.pid}")
        ok = _wait_port(PANEL_PORT, True, 10) or _wait_port(BOT_PORT, True, 1)
        if not ok:
            return {"ok": False, "error": "机器人启动超时，请查看日志", "log": log_tail(40)}
        return {"ok": True, "started": True, "message": "机器人已启动"}


def _taskkill(pid: int) -> None:
    try:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True, timeout=5,
            creationflags=_CREATE_NO_WINDOW,
        )
    except Exception as e:
        log(f"[launcher] taskkill {pid} 失败: {e}")


def stop_bot() -> dict:
    global _BOT_PROC
    with _LOCK:
        p = _BOT_PROC
        pid = pid_on_port(BOT_PORT) or pid_on_port(PANEL_PORT)
        if p is not None:
            try:
                if p.poll() is None:
                    p.terminate()
                    try:
                        p.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        _taskkill(p.pid)
            except Exception as e:
                log(f"[launcher] terminate 失败: {e}")
                _taskkill(p.pid)
        if pid and (p is None or p.pid != pid):
            _taskkill(pid)
        _BOT_PROC = None
        closed = _wait_port(PANEL_PORT, False, 5) and _wait_port(BOT_PORT, False, 2)
        if not closed:
            return {"ok": False, "error": "停止超时，端口仍被占用", "log": log_tail(30)}
        log("[launcher] 机器人已停止")
        return {"ok": True, "message": "机器人已停止"}


def restart_bot() -> dict:
    stop_bot()
    time.sleep(0.6)
    return start_bot()


def start_napcat() -> dict:
    if port_open(NAPCAT_PORT):
        return {"ok": True, "message": "NapCat 已经在运行"}
    bat = NAPCAT_DIR / "launcher-win10.bat"
    if not bat.is_file():
        return {"ok": False, "error": f"找不到 NapCat 启动脚本：{bat}"}
    try:
        ps = (
            f"Start-Process -FilePath '{bat}' "
            f"-WorkingDirectory '{NAPCAT_DIR}' -Verb RunAs"
        )
        subprocess.Popen(
            ["powershell", "-NoProfile", "-Command", ps],
            creationflags=_CREATE_NO_WINDOW,
        )
        log(f"[launcher] 请求以管理员启动 NapCat：{bat}")
        return {
            "ok": True,
            "message": "已请求管理员启动 NapCat，请在 UAC 弹窗确认；登录后连接会自动建立",
        }
    except Exception as e:
        return {"ok": False, "error": f"拉起 NapCat 失败：{e}"}


def stop_napcat() -> dict:
    """停止 NapCat。优先只结束占用 6099 的 QQ 进程；找不到时才按 QQ.exe 结束。"""
    if not port_open(NAPCAT_PORT):
        return {"ok": True, "message": "NapCat 已经停止"}
    pid = pid_on_port(NAPCAT_PORT)
    if pid:
        args = ["/F", "/PID", str(pid)]
        target = f"PID {pid}"
    else:
        args = ["/F", "/IM", "QQ.exe"]
        target = "QQ.exe"
    try:
        # QQ/NapCat 通常是管理员启动的，普通 taskkill 会被拒绝；这里也请求一次 UAC
        arg_list = ",".join(f"'{a}'" for a in args)
        ps = (
            "Start-Process -FilePath 'taskkill.exe' "
            f"-ArgumentList {arg_list} -Verb RunAs -Wait"
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            timeout=25, creationflags=_CREATE_NO_WINDOW,
        )
    except Exception as e:
        log(f"[launcher] 停止 NapCat 失败: {e}")
        return {"ok": False, "error": f"停止 NapCat 失败（UAC 被拒绝？）：{e}"}
    if _wait_port(NAPCAT_PORT, False, 8):
        log(f"[launcher] NapCat 已停止（{target}）")
        return {"ok": True, "message": f"NapCat 已停止（结束了 {target}）"}
    return {"ok": False, "error": "停止 NapCat 超时，QQ.exe 可能仍在运行"}


def restart_napcat() -> dict:
    stop_napcat()
    time.sleep(1.0)
    return start_napcat()


def _napcat_install_set(status: str, message: str, progress: int, path: str = "") -> None:
    with _NAPCAT_INSTALL_LOCK:
        _NAPCAT_INSTALL.update({
            "status": status, "message": message,
            "progress": max(0, min(100, int(progress))), "path": path,
        })


def napcat_install_status() -> dict:
    with _NAPCAT_INSTALL_LOCK:
        return dict(_NAPCAT_INSTALL)


def _download(url: str, dest: Path) -> None:
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "QAI/1.0"})
            with urllib.request.urlopen(req, timeout=45) as r, open(dest, "wb") as f:
                total = int(r.headers.get("Content-Length") or 0)
                got = 0
                while True:
                    chunk = r.read(1024 * 256)
                    if not chunk:
                        break
                    f.write(chunk)
                    got += len(chunk)
                    if total:
                        _napcat_install_set("downloading", f"正在下载 NapCat… {got // 1048576} / {total // 1048576} MB", 10 + int(got * 70 / total))
            return
        except Exception as e:
            last_error = e
            try:
                dest.unlink(missing_ok=True)
            except Exception:
                pass
            if attempt >= 3:
                break
            _napcat_install_set("downloading", f"下载中断，正在重试（{attempt + 1}/3）…", 8)
            time.sleep(2)
    raise last_error or RuntimeError("下载失败")


def _safe_extract(zip_path: Path, dest: Path) -> None:
    dest_resolved = dest.resolve()
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            target = (dest / info.filename).resolve()
            if not str(target).startswith(str(dest_resolved)):
                continue
            z.extract(info, dest)


def _find_launcher(root: Path) -> Path | None:
    for name in ("launcher-win10.bat", "launcher.bat", "launcher-win10-user.bat"):
        p = root / name
        if p.is_file():
            return p
    for p in root.rglob("launcher-win10.bat"):
        if p.is_file():
            return p
    return None


def _napcat_install_worker() -> None:
    target = ROOT / "napcat"
    try:
        if (target / "launcher-win10.bat").is_file():
            _install_set_path(target)
            _napcat_install_set("done", f"NapCat 已安装在 {target}", 100, str(target))
            return
        _napcat_install_set("downloading", "正在查询 NapCat 最新版本…", 3)
        direct = os.getenv("NAPCAT_INSTALL_URL", "").strip()
        if direct:
            url = direct
        else:
            api = "https://api.github.com/repos/NapNeko/NapCatQQ/releases/latest"
            req = urllib.request.Request(api, headers={"User-Agent": "QAI/1.0", "Accept": "application/vnd.github+json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                release = json.loads(r.read().decode("utf-8"))
            assets = release.get("assets") or []
            candidates = [a for a in assets if str(a.get("name", "")).lower().endswith(".zip")]
            preferred = [a for a in candidates if "shell.windows.node" in str(a.get("name", "")).lower()]
            if not preferred:
                preferred = [a for a in candidates if "napcat" in str(a.get("name", "")).lower()]
            use = (preferred or candidates or [None])[0]
            if not use:
                raise RuntimeError("GitHub 最新 Release 里没有找到可用的 .zip 资产")
            url = use["browser_download_url"]
        tmp_zip = ROOT / ".napcat-download.zip"
        tmp_dir = ROOT / ".napcat-extract"
        for p in (tmp_zip, tmp_dir):
            if p.is_dir():
                shutil.rmtree(p)
            elif p.exists():
                p.unlink()
        _napcat_install_set("downloading", "正在下载 NapCat（约 120MB）…", 8)
        _download(url, tmp_zip)
        _napcat_install_set("extracting", "正在解压 NapCat…", 82)
        tmp_dir.mkdir(parents=True, exist_ok=True)
        _safe_extract(tmp_zip, tmp_dir)
        launcher = _find_launcher(tmp_dir)
        if not launcher:
            raise RuntimeError("解压后没有找到 launcher-win10.bat")
        src = launcher.parent
        target.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, target, dirs_exist_ok=True)
        if not (target / "launcher-win10.bat").is_file():
            raise RuntimeError("安装后没有找到 launcher-win10.bat")
        _install_set_path(target)
        _napcat_install_set("done", f"NapCat 已安装到 {target}", 100, str(target))
        log(f"[launcher] NapCat 安装完成：{target}")
    except Exception as e:
        log(f"[launcher] NapCat 安装失败：{e}")
        raw = str(e)
        if any(k in raw for k in ("10054", "10060", "timed out", "Connection reset", "getaddrinfo", "SSL", "urlopen")):
            msg = "GitHub 下载连接被中断。请挂代理后重试，或手动下载 NapCat 并选择解压目录。"
        else:
            msg = f"安装失败：{raw}"
        _napcat_install_set("error", msg, 0)
    finally:
        for p in (ROOT / ".napcat-download.zip", ROOT / ".napcat-extract"):
            try:
                if p.is_dir():
                    shutil.rmtree(p)
                elif p.exists():
                    p.unlink()
            except Exception:
                pass


def _install_set_path(target: Path) -> None:
    global NAPCAT_DIR, _NAPCAT_TOKEN
    _update_env({"NAPCAT_DIR": str(target)})
    reload_env()
    NAPCAT_DIR = target
    _NAPCAT_TOKEN = None


def start_napcat_install() -> dict:
    state = napcat_install_status()
    if state.get("status") in ("downloading", "extracting"):
        return {"ok": True, "message": state.get("message") or "正在安装", "state": state}
    _napcat_install_set("downloading", "准备安装…", 1)
    threading.Thread(target=_napcat_install_worker, daemon=True).start()
    return {"ok": True, "message": "开始安装 NapCat；下载约 120MB，请稍候", "state": napcat_install_status()}


# ---------------------------------------------------------------- HTTP
def _send_json(h, code: int, obj) -> None:
    body = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
    h.send_response(code)
    h.send_header("Content-Type", "application/json; charset=utf-8")
    h.send_header("Content-Length", str(len(body)))
    h.send_header("Cache-Control", "no-store")
    h.end_headers()
    h.wfile.write(body)


def _send_file(h, path: Path, ctype: str) -> None:
    try:
        data = path.read_bytes()
    except Exception:
        _send_json(h, 404, {"ok": False, "error": "not found"})
        return
    h.send_response(200)
    h.send_header("Content-Type", ctype)
    h.send_header("Content-Length", str(len(data)))
    h.send_header("Cache-Control", "no-cache")
    h.end_headers()
    h.wfile.write(data)


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _body_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            length = 0
        if length <= 0:
            return {}
        raw = self.rfile.read(min(length, 2 * 1024 * 1024))
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            if not (UI_DIR / "index.html").is_file():
                _send_json(self, 500, {"ok": False, "error": "缺少 launcher_ui/index.html"})
                return
            _send_file(self, UI_DIR / "index.html", "text/html; charset=utf-8")
            return
        if u.path == "/app.css":
            _send_file(self, UI_DIR / "app.css", "text/css; charset=utf-8")
            return
        if u.path == "/app.js":
            _send_file(self, UI_DIR / "app.js", "text/javascript; charset=utf-8")
            return
        if u.path == "/theme.css":
            _send_file(self, UI_DIR / "theme.css", "text/css; charset=utf-8")
            return
        if u.path == "/theme.js":
            _send_file(self, UI_DIR / "theme.js", "text/javascript; charset=utf-8")
            return
        if u.path == "/models.js":
            _send_file(self, UI_DIR / "models.js", "text/javascript; charset=utf-8")
            return
        if u.path == "/favicon.svg":
            _send_file(self, UI_DIR / "favicon.svg", "image/svg+xml")
            return
        if u.path in ("/qai-icon.png", "/qai-icon-64.png", "/qai-cover.png"):
            _send_file(self, UI_DIR / u.path.lstrip("/"), "image/png")
            return
        if u.path == "/api/status":
            _send_json(self, 200, status())
            return
        if u.path == "/api/log":
            try:
                n = int(parse_qs(u.query).get("n", ["80"])[0])
            except ValueError:
                n = 80
            _send_json(self, 200, {"ok": True, "log": log_tail(n)})
            return
        if u.path == "/api/napcat/token":
            _send_json(self, 200, napcat_token_info())
            return
        if u.path == "/api/napcat/install/status":
            _send_json(self, 200, {"ok": True, **napcat_install_status()})
            return
        if u.path == "/api/setup/status":
            _send_json(self, 200, setup_status())
            return
        if u.path == "/api/setup/detect":
            _send_json(self, 200, setup_detect())
            return
        if u.path == "/api/tutorial":
            guide = ROOT / "新手教程.md"
            try:
                text = guide.read_text(encoding="utf-8")
            except Exception:
                text = "# 新手教程\n\n教程文件缺失，请查看项目根目录的「新手教程.md」。"
            _send_json(self, 200, {"ok": True, "text": text})
            return
        _send_json(self, 404, {"ok": False, "error": "not found"})

    def do_POST(self):
        u = urlparse(self.path)
        try:
            if u.path == "/api/start":
                _send_json(self, 200, start_bot())
                return
            if u.path == "/api/stop":
                _send_json(self, 200, stop_bot())
                return
            if u.path == "/api/restart":
                _send_json(self, 200, restart_bot())
                return
            if u.path == "/api/napcat/start":
                _send_json(self, 200, start_napcat())
                return
            if u.path == "/api/napcat/install":
                _send_json(self, 200, start_napcat_install())
                return
            if u.path == "/api/napcat/stop":
                _send_json(self, 200, stop_napcat())
                return
            if u.path == "/api/napcat/restart":
                _send_json(self, 200, restart_napcat())
                return
            if u.path == "/api/setup/save":
                _send_json(self, 200, setup_save(self._body_json()))
                return
        except Exception as e:
            _send_json(self, 500, {"ok": False, "error": str(e)})
            return
        _send_json(self, 404, {"ok": False, "error": "not found"})


def open_window() -> None:
    url = f"http://127.0.0.1:{LAUNCHER_PORT}/"
    if _read_env().get("QAI_USE_CHROME", "").strip().lower() not in ("1", "true", "yes"):
        if _open_native_window(url):
            return
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    for exe in candidates:
        if Path(exe).is_file():
            try:
                subprocess.Popen([exe, f"--app={url}", "--window-size=1360,900", "--no-first-run"])
                return
            except Exception:
                continue
    webbrowser.open(url)


def _qai_window_pids() -> set[int]:
    """返回当前所有可见的标题为 QAI 的窗口进程 PID。"""
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
                pids.add(int(pid.value))
            return True

        user32.EnumWindows(CB(cb), 0)
        return pids
    except Exception:
        return set()


def _find_qai_window(ignore: set[int] | None = None) -> int:
    ignore = ignore or set()
    for pid in _qai_window_pids():
        if pid not in ignore:
            return pid
    return 0


def _open_native_window(url: str) -> bool:
    """启动独立的 Edge WebView2 窗口；以“窗口真的出现”为准，失败重试。"""
    import importlib.util

    if importlib.util.find_spec("webview") is None:
        log("[launcher] 未安装 pywebview，回退 Chrome 窗口")
        return False
    script = ROOT / "qai_window.py"
    if not script.is_file():
        log("[launcher] 缺少 qai_window.py，回退 Chrome 窗口")
        return False
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    launcher = pythonw if pythonw.is_file() else exe
    existing_windows = _qai_window_pids()
    for attempt in (1, 2):
        try:
            proc = subprocess.Popen(
                [str(launcher), str(script), url],
                cwd=str(ROOT),
                close_fds=False,
            )
        except Exception as e:
            log(f"[launcher] 原生窗口启动失败：{e}")
            return False
        window_pid = 0
        for _ in range(90):
            window_pid = _find_qai_window(existing_windows)
            if window_pid:
                break
            if proc.poll() is not None:
                break
            time.sleep(0.1)
        if window_pid:
            try:
                (ROOT / "data").mkdir(exist_ok=True)
                (ROOT / "data" / "qai_window.pid").write_text(str(window_pid), encoding="utf-8")
            except Exception:
                pass
            log(f"[launcher] 原生 WebView2 窗口已出现 pid={window_pid}")
            return True
        log(f"[launcher] 原生窗口第 {attempt} 次未出现（进程 code={proc.poll()}）")
        try:
            if proc.poll() is None:
                proc.terminate()
        except Exception:
            pass
        time.sleep(1.0)
    log("[launcher] 原生窗口重试失败，回退 Chrome")
    return False


def auto_start_bot() -> None:
    """首次配置未完成时不自动启动，先让用户填 Key/路径。"""
    try:
        if setup_status().get("needs_setup"):
            log("[launcher] 首次配置未完成，跳过自动启动机器人")
            return
    except Exception:
        pass
    start_bot()


def is_launcher_running() -> bool:
    if not port_open(LAUNCHER_PORT):
        return False
    try:
        with _OPENER.open(f"http://127.0.0.1:{LAUNCHER_PORT}/api/status", timeout=1.0) as r:
            data = json.loads(r.read().decode("utf-8"))
            return data.get("app") == APP_NAME
    except Exception:
        return False


def launcher_status_app() -> str:
    if not port_open(LAUNCHER_PORT):
        return ""
    try:
        with _OPENER.open(f"http://127.0.0.1:{LAUNCHER_PORT}/api/status", timeout=1.0) as r:
            data = json.loads(r.read().decode("utf-8"))
            return str(data.get("app") or "")
    except Exception:
        return ""


def replace_legacy_launcher() -> bool:
    """旧版 QQ机器人控制中心 占用 8082 时，结束它再让 QAI 接管。"""
    pid = pid_on_port(LAUNCHER_PORT)
    if not pid:
        return False
    log(f"[launcher] 检测到旧版控制中心（PID {pid}），正在替换为 QAI")
    try:
        subprocess.run(
            ["taskkill", "/F", "/PID", str(pid)],
            timeout=8, creationflags=_CREATE_NO_WINDOW,
        )
    except Exception as e:
        log(f"[launcher] 结束旧控制中心失败：{e}")
        return False
    if _wait_port(LAUNCHER_PORT, False, 8):
        log("[launcher] 旧控制中心已退出，QAI 开始接管")
        return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description="QAI 桌面控制中心")
    ap.add_argument("--no-open", action="store_true", help="不自动打开应用窗口")
    ap.add_argument("--no-auto-start", action="store_true", help="不自动启动机器人")
    args = ap.parse_args()

    if is_launcher_running():
        log("[launcher] 控制中心已在运行，直接打开窗口")
        if not args.no_open:
            open_window()
        return 0

    if port_open(LAUNCHER_PORT):
        old_app = launcher_status_app()
        if old_app == "qqbot-launcher":
            if not replace_legacy_launcher():
                log("[launcher] 旧版控制中心替换失败，请先双击 停止QAI.bat 再打开 QAI")
                return 1
        else:
            log(f"[launcher] 端口 {LAUNCHER_PORT} 被其他程序占用（app={old_app or 'unknown'}）")
            return 1

    try:
        server = ThreadingHTTPServer(("127.0.0.1", LAUNCHER_PORT), _Handler)
    except Exception as e:
        log(f"[launcher] 启动失败：{e}")
        return 1

    log(f"[launcher] 控制中心已启动：http://127.0.0.1:{LAUNCHER_PORT}/")
    if not args.no_open:
        threading.Timer(0.8, open_window).start()
    if AUTO_START and not args.no_auto_start:
        threading.Timer(1.2, auto_start_bot).start()
    if NAPCAT_AUTO_START and not port_open(NAPCAT_PORT):
        threading.Timer(2.0, lambda: start_napcat()).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
