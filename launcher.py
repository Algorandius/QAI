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
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
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
DATA_DIR = ROOT / "data"
ENV_FILE = ROOT / ".env"
BOT_LOG = LOG_DIR / "launcher-bot.log"
LAUNCHER_LOG = LOG_DIR / "launcher.log"
# stop.py 靠这个文件认出「哪些进程是本目录的 QAI」，不再按端口盲杀
PIDS_FILE = DATA_DIR / "qai_pids.json"
WINDOW_PID_FILE = DATA_DIR / "qai_window.pid"
LOG_DIR.mkdir(parents=True, exist_ok=True)


def _read_env() -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
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
        # 只有「没配模型 Key」才算没配好；NapCat 单独提示，不拦着用户先试模型
        "needs_setup": not bool(key),
        "napcat_ok": nap_ok,
        "llm_key_set": bool(key),
        "llm_provider": provider,
        "provider_keys": provider_keys,
        "llm_base_url": env.get("LLM_BASE_URL", "https://api.deepseek.com/v1"),
        "llm_model": env.get("LLM_MODEL", "deepseek-flash"),
        "admin_qq": env.get("BOT_ADMIN_QQ", ""),
        "allowed_groups": env.get("ALLOWED_GROUPS", ""),
        "napcat_dir": napdir,
        "close_behavior": env.get("QAI_CLOSE_BEHAVIOR", "exit").strip() or "exit",
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
    # NapCat 目录不再是硬性要求：很多人想先填模型 Key、之后再装 NapCat。
    # 只做提醒，不拦保存；没有 NapCat 时不自动启动机器人（反正也连不上 QQ）。
    napcat_ok = bool(napdir) and (Path(napdir) / "launcher-win10.bat").is_file()
    napcat_warning = ""
    if not napdir:
        napcat_warning = "还没有填 NapCat 目录，机器人暂时不会启动；装好 NapCat 后再回来保存一次即可。"
    elif not napcat_ok:
        napcat_warning = f"NapCat 目录里没有 launcher-win10.bat（{napdir}），机器人暂时不会启动。"
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
    if napcat_ok:
        NAPCAT_DIR = Path(napdir)
    _NAPCAT_TOKEN = None
    try:
        NAPCAT_PORT = int(_read_env().get("NAPCAT_PORT") or NAPCAT_PORT)
    except ValueError:
        pass
    started = False
    will_start = bool(data.get("start")) and napcat_ok
    if will_start:
        if port_open(PANEL_PORT) or port_open(BOT_PORT):
            stop_bot()
        r = start_bot()
        started = bool(r.get("ok"))
        if not r.get("ok"):
            return {
                "ok": False,
                "error": r.get("error") or "配置已保存，但机器人启动失败",
                "saved": True,
                "warning": napcat_warning,
            }
    log("[launcher] 首次配置已保存")
    return {
        "ok": True,
        "message": "配置已保存" + ("，机器人已启动" if started else ""),
        "started": started,
        "warning": napcat_warning,
    }


# ---------------------------------------------------------------- 各厂商 Key
# 人设可以单独选厂商和模型，但每个厂商的 Key 存在 LLM_KEY_<厂商> 里。
# 这组接口只动 LLM_KEY_<厂商>，不会改全局默认模型（LLM_PROVIDER / LLM_MODEL），
# 所以用户给人设配第二个厂商时，不必来回切换默认模型。

def _clean_secret(raw: object) -> str:
    """Key 只允许单行文本，防止换行把内容注入 .env 的其它变量。"""
    text = str(raw or "").strip()
    if any(ch in text for ch in "\r\n\x00"):
        raise ValueError("API Key 里不能有换行或空字符")
    if len(text) > 400:
        raise ValueError("API Key 太长，请确认没有多粘内容")
    return text


def _mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "•" * len(value)
    return f"{value[:4]}…{value[-4:]}"


def provider_keys_payload() -> dict:
    """每个厂商的 Key 状态；混元额外兼容 LLM_KEY_TENCENT / _TENCENT_INTL。"""
    env = _read_env()
    current = env.get("LLM_PROVIDER", "").strip() or _infer_provider_from_base(env.get("LLM_BASE_URL", ""))
    if current in ("tencent", "tencent_intl"):
        current = "hunyuan"
    llm_key_set = bool(env.get("LLM_API_KEY", "").strip())

    raw = {
        "zhipu": env.get("LLM_KEY_ZHIPU", ""),
        "deepseek": env.get("LLM_KEY_DEEPSEEK", ""),
        "qwen": env.get("LLM_KEY_QWEN", ""),
        "kimi": env.get("LLM_KEY_KIMI", ""),
        "hunyuan": (
            env.get("LLM_KEY_HUNYUAN", "")
            or env.get("LLM_KEY_TENCENT", "")
            or env.get("LLM_KEY_TENCENT_INTL", "")
        ),
    }
    items = []
    for pid in ("zhipu", "deepseek", "qwen", "kimi", "hunyuan"):
        value = str(raw.get(pid) or "").strip()
        # 全局默认模型用的 Key 存在 LLM_API_KEY 里，对当前厂商等同于已配置
        if not value and pid == current and llm_key_set:
            value = env.get("LLM_API_KEY", "").strip()
        items.append({
            "id": pid,
            "env": _PROVIDER_KEY_ENV[pid],
            "set": bool(value),
            "masked": _mask_secret(value),
            "is_current": pid == current,
        })
    return {"ok": True, "items": items, "current_provider": current, "llm_key_set": llm_key_set}


def save_provider_key(data: dict) -> dict:
    """保存 / 清除某个厂商的 Key，返回最新状态。"""
    provider = str(data.get("provider") or "").strip().lower()
    env_key = _PROVIDER_KEY_ENV.get(provider, "")
    if not env_key:
        return {"ok": False, "error": f"未知厂商：{provider or '（空）'}"}
    if provider.startswith("tencent"):
        env_key = "LLM_KEY_HUNYUAN"   # 统一写到混元的键上，避免两个别名分叉
    clear = bool(data.get("clear"))
    try:
        key = _clean_secret(data.get("key"))
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    if not key and not clear:
        return {"ok": False, "error": "请填写 API Key"}
    if key and len(key) < 8:
        return {"ok": False, "error": "API Key 太短，请确认复制完整"}

    try:
        _update_env({env_key: "" if clear else key})
    except Exception as e:
        return {"ok": False, "error": f"写入 .env 失败：{e}"}
    reload_env()
    log(f"[launcher] 厂商 Key 已{'清除' if clear else '保存'}：{provider} → {env_key}")

    # 机器人只在启动时读 .env，改完 Key 必须重启才生效
    was_running = port_open(PANEL_PORT) or port_open(BOT_PORT)
    restarted = False
    if was_running:
        restarted = bool(restart_bot().get("ok"))
    if not was_running:
        message = f"{env_key} 已{'清除' if clear else '保存'}，下次启动生效"
    elif restarted:
        message = f"{env_key} 已{'清除' if clear else '保存'}，机器人已重启"
    else:
        message = f"{env_key} 已{'清除' if clear else '保存'}，但机器人重启失败，请手动重启"
    return {
        "ok": True,
        "message": message,
        "provider": provider,
        "env": env_key,
        "restarted": restarted,
        **provider_keys_payload(),
    }


# ---------------------------------------------------------------- 数据备份
# 用户换电脑是真实高频需求：聊天记录、长期记忆、好感度、人设绑定都在 data/ 里。
# 导出不含 WebView 缓存（几十 MB 且不该迁移），导入前要求机器人已停止。
DATA_BACKUP_SKIP = {
    "webview",
    "qai_pids.json",
    "qai_window.pid",
    # 在线更新留下的下载包/暂存目录：属于程序文件，不该混进用户数据备份
    "_update",
    "_update_backup",
}
DATA_IMPORT_MAX_BYTES = 300 * 1024 * 1024


def export_data_zip() -> bytes:
    buf = io.BytesIO()
    count = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        if DATA_DIR.is_dir():
            for path in DATA_DIR.rglob("*"):
                if not path.is_file():
                    continue
                rel = path.relative_to(DATA_DIR)
                if any(part in DATA_BACKUP_SKIP for part in rel.parts):
                    continue
                if path.name in DATA_BACKUP_SKIP:
                    continue
                z.write(path, Path("data") / rel)
                count += 1
    log(f"[launcher] 导出数据 {count} 个文件")
    return buf.getvalue()


def import_data_zip(raw: bytes) -> dict:
    """用上传的 zip 覆盖 data/；覆盖前先把现有数据备份一份。"""
    if port_open(BOT_PORT) or port_open(PANEL_PORT):
        return {
            "ok": False,
            "error": "机器人正在运行，请先点「停止」再导入，否则内存里的记忆会覆盖刚导入的文件",
        }
    if not raw:
        return {"ok": False, "error": "没有收到文件内容"}
    if len(raw) > DATA_IMPORT_MAX_BYTES:
        return {"ok": False, "error": "备份文件超过 300MB，请确认选对了文件"}
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except Exception as e:
        return {"ok": False, "error": f"不是有效的 zip 文件：{e}"}

    plan: list[tuple[zipfile.ZipInfo, Path]] = []
    for info in zf.infolist():
        if info.is_dir():
            continue
        norm = Path(info.filename.replace("\\", "/"))
        if norm.is_absolute() or ".." in norm.parts:
            return {"ok": False, "error": f"压缩包里有非法路径：{info.filename}"}
        if not norm.parts or norm.parts[0].lower() != "data":
            continue                      # 只认 data/ 下的内容
        rel = Path(*norm.parts[1:])
        if not rel.parts or any(part in DATA_BACKUP_SKIP for part in rel.parts):
            continue
        plan.append((info, DATA_DIR / rel))
    if not plan:
        return {"ok": False, "error": "压缩包里没有 data/ 目录，可能选错文件了"}

    backup = ROOT / f"data.bak-{time.strftime('%Y%m%d-%H%M%S')}"
    try:
        if DATA_DIR.is_dir():
            shutil.copytree(
                DATA_DIR, backup,
                ignore=shutil.ignore_patterns("webview"), dirs_exist_ok=True,
            )
    except Exception as e:
        return {"ok": False, "error": f"备份现有数据失败，已中止导入：{e}"}

    restored = 0
    try:
        for info, target in plan:
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as fsrc, target.open("wb") as fdst:
                shutil.copyfileobj(fsrc, fdst)
            restored += 1
    except Exception as e:
        return {
            "ok": False,
            "error": f"导入中断：{e}",
            "backup": str(backup),
            "restored": restored,
        }
    log(f"[launcher] 导入数据 {restored} 个文件，旧数据备份在 {backup.name}")
    return {
        "ok": True,
        "message": f"已导入 {restored} 个文件；旧数据备份为 {backup.name}",
        "restored": restored,
        "backup": str(backup),
        "restart_required": True,
    }


# ---------------------------------------------------------------- 在线更新
# 思路：程序文件和用户数据分开。更新包只含程序文件，updater.py 只覆盖
# 白名单里的路径，data/、.env、logs/、assets/、runtime/、napcat/ 一律不碰，
# 所以用户不用重新解压、不会丢聊天记录。

def _ver_tuple(raw: str) -> tuple[int, ...]:
    # 去掉 v 前缀和 -beta / +build 之类后缀，只按数字段比较
    core = str(raw or "").strip().lstrip("vV").split("-")[0].split("+")[0]
    parts: list[int] = []
    for piece in core.split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts) or (0,)


def _ver_newer(candidate: str, base: str) -> bool:
    a, b = _ver_tuple(candidate), _ver_tuple(base)
    width = max(len(a), len(b))
    a = a + (0,) * (width - len(a))
    b = b + (0,) * (width - len(b))
    return a > b


# 国内用户经常连不上 GitHub，所以更新通路做成四层，任何一层能用就行：
#   1. QAI_UPDATE_MANIFEST —— 自建 JSON 清单，完全绕开 GitHub API（网盘直链/CDN/自己服务器）
#   2. 直连 GitHub API
#   3. 内置加速源（gh-proxy / chenc）拼在 GitHub 地址前面
#   4. 「本地文件更新」——用户自己从网盘/群里拿到 zip 选进来，全程不联网
# 下载资产时同样按「直连 → 加速源」逐个试，且分别用「系统代理」「不走代理」各试一次。
DEFAULT_UPDATE_MIRRORS = (
    "https://gh-proxy.com/",
    "https://github.chenc.dev/",
)


def _cfg_text(key: str, default: str = "") -> str:
    """读配置：先看环境变量，再看 .env。"""
    v = (os.getenv(key) or "").strip()
    if v:
        return v
    try:
        return str(ENV.get(key) or "").strip() or default
    except Exception:
        return default


def update_mirrors() -> list[str]:
    """更新源前缀，第一个固定是空串（直连）。"""
    custom = [x.strip() for x in _cfg_text("QAI_UPDATE_MIRRORS").split(",") if x.strip()]
    out = [""]
    for item in custom + list(DEFAULT_UPDATE_MIRRORS):
        prefix = item if item.endswith("/") else item + "/"
        if prefix not in out:
            out.append(prefix)
    return out


def _update_openers() -> list:
    """先尊重系统代理（很多人正是靠代理上 GitHub），再走直连。"""
    openers = []
    try:
        openers.append(urllib.request.build_opener())
    except Exception:
        pass
    openers.append(_OPENER)
    return openers


def _update_json() -> tuple[dict | None, str, str]:
    """取版本信息，返回 (数据, 命中的地址, 最后一次错误)。"""
    manifest = _cfg_text("QAI_UPDATE_MANIFEST")
    urls: list[str] = []
    if manifest:
        urls.append(manifest)
    urls += [prefix + GITHUB_RELEASES_LATEST for prefix in update_mirrors()]

    last_err = ""
    deadline = time.time() + 25
    for url in urls:
        if time.time() > deadline:
            last_err = last_err or "所有更新源都超时"
            break
        for opener in _update_openers():
            try:
                req = urllib.request.Request(
                    url,
                    headers={
                        "Accept": "application/vnd.github+json",
                        "User-Agent": f"QAI/{APP_VERSION}",
                    },
                )
                with opener.open(req, timeout=6) as r:
                    data = json.loads(r.read().decode("utf-8", "replace"))
                if isinstance(data, dict):
                    return data, url, ""
            except Exception as e:
                last_err = str(e)
    return None, "", last_err


def _normalize_release(data: dict) -> dict:
    """GitHub Release 结构和自建清单结构都收敛成同一个返回。"""
    if "tag_name" in data or "assets" in data:
        latest = str(data.get("tag_name") or "").lstrip("vV")
        notes = str(data.get("body") or "")
        page = str(data.get("html_url") or "")
        by_name = {str(a.get("name") or ""): a for a in (data.get("assets") or [])}
        update_asset = next((a for n, a in by_name.items() if n.endswith("-update.zip")), None)
        full_asset = next((a for n, a in by_name.items() if n.endswith("-Windows-x64.zip")), None)
        asset = update_asset or full_asset
        name = str(asset.get("name") or "") if asset else ""
        url = str(asset.get("browser_download_url") or "") if asset else ""
        sha_url = ""
        if name and by_name.get(f"{name}.sha256"):
            sha_url = str(by_name[f"{name}.sha256"].get("browser_download_url") or "")
        size = int(asset.get("size") or 0) if asset else 0
        return {
            "latest": latest,
            "notes": notes,
            "page": page,
            "asset": name,
            "asset_url": url,
            "asset_size": size,
            "asset_is_update_pack": bool(update_asset),
            "sha256_url": sha_url,
        }
    # 自建清单：{"version","notes","page","update":{name,url,size,sha256_url}}
    node = data.get("update") if isinstance(data.get("update"), dict) else {}
    if not node and isinstance(data.get("full"), dict):
        node = data["full"]
    return {
        "latest": str(data.get("version") or "").lstrip("vV"),
        "notes": str(data.get("notes") or ""),
        "page": str(data.get("page") or ""),
        "asset": str(node.get("name") or ""),
        "asset_url": str(node.get("url") or ""),
        "asset_size": int(node.get("size") or 0),
        "asset_is_update_pack": True,
        "sha256_url": str(node.get("sha256_url") or ""),
    }


def update_check() -> dict:
    """查有没有新版本；所有源都失败时给出可操作的提示，而不是一句「网络错误」。"""
    current = APP_VERSION
    data, source, err = _update_json()
    if data is None:
        return {
            "ok": False,
            "current": current,
            "error": (
                "连不上任何更新源（已尝试 GitHub 直连和内置加速源）。\n"
                "可以：①在 .env 里设 QAI_UPDATE_MIRRORS 换加速源；"
                "②设 QAI_UPDATE_MANIFEST 指向自建清单；"
                "③直接用「从本地文件更新」，把手动下载的 zip 选进来。\n"
                f"底层错误：{err}"
            ),
            "offline_hint": True,
        }
    info = _normalize_release(data)
    info.update({
        "ok": True,
        "current": current,
        "has_update": bool(info.get("latest")) and _ver_newer(info["latest"], current),
        "source": source,
        "mirrors_tried": len(update_mirrors()),
    })
    return info


def _download_to(url: str, dest: Path, opener=None, timeout: float = 180) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    opener = opener or _OPENER
    with opener.open(url, timeout=timeout) as r, tmp.open("wb") as f:
        shutil.copyfileobj(r, f, length=1024 * 64)
    tmp.replace(dest)


def _download_update(url: str, dest: Path) -> str:
    """按「代理/直连 × 直连地址/加速源」逐个试，返回命中的地址。"""
    errors: list[str] = []
    for opener in _update_openers():
        for prefix in update_mirrors():
            target = prefix + url
            try:
                _download_to(target, dest, opener=opener)
                log(f"[launcher] 更新包下载成功：{target[:80]}")
                return target
            except Exception as e:
                errors.append(f"{target[:56]} → {e}")
    raise RuntimeError("；".join(errors[-3:]) or "下载失败")


def _fetch_text_any(url: str) -> str:
    for opener in _update_openers():
        for prefix in update_mirrors():
            try:
                with opener.open(prefix + url, timeout=15) as r:
                    return r.read().decode("utf-8", "replace")
            except Exception:
                continue
    return ""


def _fetch_text_direct(url: str, timeout: float = 15) -> str:
    """只走直连/系统代理，**绝不经过第三方加速源**。

    校验和是关键：如果 sha256 和 zip 都从同一个镜像取，镜像被投毒时
    两个一起被换掉，校验永远通过，等于没有校验。所以校验文件必须来自
    可信通道（GitHub 直连，或用户自己在清单里指定的地址）。
    """
    for opener in _update_openers():
        try:
            with opener.open(url, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except Exception:
            continue
    return ""


def _find_program_root(base: Path) -> Path | None:
    """更新包解压后可能多一层版本目录，找到真正含 launcher.py 的那层。"""
    if (base / "launcher.py").is_file():
        return base
    for child in sorted(base.iterdir()):
        if child.is_dir() and (child / "launcher.py").is_file():
            return child
    for child in base.rglob("launcher.py"):
        return child.parent
    return None


def _extract_update(zip_path: Path) -> tuple[Path | None, str]:
    """解压到暂存目录并校验，返回 (程序根目录, 错误)。"""
    work = DATA_DIR / UPDATE_DIR_NAME
    staging = work / "staging"
    try:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True, exist_ok=True)
        _safe_extract(zip_path, staging)
    except Exception as e:
        return None, f"解压更新包失败：{e}"
    program_root = _find_program_root(staging)
    if not program_root:
        return None, "更新包里没有 launcher.py，可能选错文件了"
    try:
        import updater

        problem = updater.validate_staging(program_root)
    except Exception as e:
        problem = f"updater 不可用：{e}"
    return (None, problem) if problem else (program_root, "")


def _launch_updater(program_root: Path, to_version: str) -> tuple[bool, str]:
    """启动更新进程并安排自身退出。"""
    # 先停机器人：更新后反正要重启，也避免它继续写 data/
    if port_open(BOT_PORT) or port_open(PANEL_PORT):
        stop_bot()

    updater_py = ROOT / "updater.py"
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    runner = pythonw if pythonw.is_file() else exe
    log_path = DATA_DIR / UPDATE_DIR_NAME / "last-result.json"
    try:
        subprocess.Popen(
            [
                str(runner), str(updater_py),
                "--staging", str(program_root),
                "--root", str(ROOT),
                "--port", str(LAUNCHER_PORT),
                "--from-version", APP_VERSION,
                "--to-version", to_version,
                "--log", str(log_path),
            ],
            cwd=str(ROOT),
            close_fds=True,
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
    except Exception as e:
        return False, f"启动更新程序失败：{e}"

    log(f"[launcher] 更新程序已启动，准备退出以便替换文件（{APP_VERSION} → {to_version}）")

    def _finish() -> None:
        try:
            for pid in _qai_window_pids():
                _taskkill(pid)
        except Exception:
            pass
        shutdown_launcher()

    threading.Timer(1.2, _finish).start()
    return True, ""


def update_apply(data: dict | None = None) -> dict:
    """在线更新：下载 + 校验 + 解压 + 交给 updater。"""
    payload = data or {}
    info = update_check()
    if not info.get("ok"):
        return info

    # 注意：info 里带 ok=True，所以失败返回必须把 info 放前面再覆盖，
    # 反过来写会被 info 的 ok 盖掉，界面就会把失败当成功。
    def fail(message: str, extra: dict | None = None) -> dict:
        out = {**info, **(extra or {})}
        out.update({"ok": False, "error": message})
        return out

    if not info.get("has_update") and not payload.get("force"):
        return fail(f"已经是最新版本（{APP_VERSION}）")
    url = info.get("asset_url") or ""
    if not url:
        return fail("这个 Release 里没有可用的 zip 资产，请用「从本地文件更新」")
    # 校验和必须来自可信通道，且必须有——没有校验的自动更新等于给了投毒后门。
    # 下载前就检查，避免白下几十 MB。
    if not info.get("sha256_url"):
        return fail(
            "这个版本没有发布 SHA256 校验文件，为安全起见不在线更新。\n"
            "请用「从本地文件更新」，或等发布方补上校验文件。"
        )

    work = DATA_DIR / UPDATE_DIR_NAME
    zip_path = work / (info.get("asset") or "update.zip")
    try:
        work.mkdir(parents=True, exist_ok=True)
        log(f"[launcher] 开始下载更新：{info.get('asset')}")
        _download_update(url, zip_path)
    except Exception as e:
        return fail(
            f"下载更新失败（直连和加速源都试过了）：{e}\n"
            "可以改用「从本地文件更新」：手动下载 zip 后直接选文件。",
            {"offline_hint": True},
        )

    expect_parts = _fetch_text_direct(info["sha256_url"]).split()
    if not expect_parts:
        return fail(
            "拿不到官方校验文件（GitHub 直连被拦？），为安全起见已中止在线更新。\n"
            "可以改用「从本地文件更新」：手动下载 zip 后直接选文件。",
            {"offline_hint": True},
        )
    expect = expect_parts[0].strip().lower()
    actual = hashlib.sha256(zip_path.read_bytes()).hexdigest().lower()
    if expect != actual:
        return fail("更新包校验失败（SHA256 不一致），已中止——可能被中间人篡改")
    log("[launcher] 更新包 SHA256 校验通过")

    program_root, problem = _extract_update(zip_path)
    if program_root is None:
        return fail(problem)

    ok, err = _launch_updater(program_root, str(info.get("latest") or ""))
    if not ok:
        return fail(err)
    return {
        "ok": True,
        "message": f"正在更新到 {info.get('latest')}：下载完成，QAI 会关闭并自动重开",
        "from": APP_VERSION,
        "to": info.get("latest"),
        "size": info.get("asset_size"),
        "restarting": True,
    }


def update_from_local(raw: bytes) -> dict:
    """离线更新：用户自己选的 zip，全程不联网。"""
    if not raw:
        return {"ok": False, "error": "没有收到文件内容"}
    if len(raw) > 200 * 1024 * 1024:
        return {"ok": False, "error": "文件超过 200MB，确认选的是更新包而不是别的文件"}
    work = DATA_DIR / UPDATE_DIR_NAME
    zip_path = work / "manual-update.zip"
    try:
        work.mkdir(parents=True, exist_ok=True)
        zip_path.write_bytes(raw)
    except Exception as e:
        return {"ok": False, "error": f"保存上传文件失败：{e}"}
    program_root, problem = _extract_update(zip_path)
    if program_root is None:
        return {"ok": False, "error": problem}
    try:
        import updater

        target = updater.read_version(program_root) or "本地文件"
    except Exception:
        target = "本地文件"
    ok, err = _launch_updater(program_root, target)
    if not ok:
        return {"ok": False, "error": err}
    return {
        "ok": True,
        "message": f"正在用本地文件更新到 {target}：QAI 会关闭并自动重开",
        "from": APP_VERSION,
        "to": target,
        "restarting": True,
    }


# ---------------------------------------------------------------- 权限收紧
def _tighten_permissions() -> None:
    """把 .env 和 data/ 的访问权限收紧到「当前用户 + SYSTEM + 管理员」。

    里面是明文 API Key 和聊天记录；默认继承权限下，同机其它账户通常能读到。
    只做一次（写 marker 记住），失败不影响启动。
    """
    if os.name != "nt":
        return
    if _cfg_text("QAI_SKIP_ACL").strip().lower() in ("1", "true", "yes"):
        return
    marker = DATA_DIR / ".acl_tightened"
    try:
        if marker.exists():
            return
    except Exception:
        return
    user = os.environ.get("USERNAME", "").strip()
    if not user:
        return
    grants = [f"{user}:F", "*S-1-5-18:F", "*S-1-5-32-544:F"]  # 用户 / SYSTEM / Administrators
    targets: list[tuple[str, str]] = []
    if ENV_FILE.is_file():
        targets.append((str(ENV_FILE), "F"))
    if DATA_DIR.is_dir():
        targets.append((str(DATA_DIR), "(OI)(CI)F"))
    ok = True
    for path, perm in targets:
        args = ["icacls", path, "/inheritance:r"]
        for grant in grants:
            user_part, _, _ = grant.partition(":")
            args += ["/grant:r", f"{user_part}:{perm}"]
        try:
            r = subprocess.run(
                args, capture_output=True, timeout=20,
                creationflags=_CREATE_NO_WINDOW,
            )
            if r.returncode != 0:
                ok = False
                log(f"[launcher] 收紧权限失败（{path}）：{r.stdout.decode('utf-8', 'replace')[:120]}")
        except Exception as e:
            ok = False
            log(f"[launcher] 收紧权限异常（{path}）：{e}")
    if ok:
        try:
            marker.write_text(time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")
            log("[launcher] 已把 .env 与 data/ 的访问权限收紧到当前用户")
        except Exception:
            pass


# ---------------------------------------------------------------- 托盘图标
# 后台运行时（关了窗口但机器人在跑）需要在右下角有个图标，
# 否则用户既看不见进程、也不知道怎么把它叫回来。
_WM_TRAY = 0x8000 + 1
_WM_LBUTTONDBLCLK = 0x0203
_WM_RBUTTONUP = 0x0205
_WM_COMMAND = 0x0111
_ID_OPEN = 40001
_ID_QUIT = 40002
_ID_SEP = 0


def start_tray() -> None:
    if os.name != "nt":
        return
    threading.Thread(target=_tray_loop, name="qai-tray", daemon=True).start()


def _tray_loop() -> None:
    """右下角托盘图标：双击打开界面，右键菜单「打开界面 / 退出 QAI」。

    全部用 ctypes 调 Win32，不额外装依赖。任何一步失败都静默退出，
    绝不能因为图标问题把启动器搞崩。
    """
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        shell32 = ctypes.windll.shell32
        kernel32 = ctypes.windll.kernel32

        LRESULT = ctypes.c_ssize_t
        WNDPROC = ctypes.WINFUNCTYPE(
            LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
        )

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [
                ("style", wintypes.UINT),
                ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR),
            ]

        class NOTIFYICONDATAW(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("hWnd", wintypes.HWND),
                ("uID", wintypes.UINT),
                ("uFlags", wintypes.UINT),
                ("uCallbackMessage", wintypes.UINT),
                ("hIcon", wintypes.HICON),
                ("szTip", ctypes.c_wchar * 128),
                ("dwState", wintypes.DWORD),
                ("dwStateMask", wintypes.DWORD),
                ("szInfo", ctypes.c_wchar * 256),
                ("uVersion", wintypes.UINT),
                ("szInfoTitle", ctypes.c_wchar * 64),
                ("dwInfoFlags", wintypes.DWORD),
                ("guidItem", ctypes.c_byte * 16),
                ("hBalloonIcon", wintypes.HICON),
            ]

        # 64 位下必须声明 argtypes/restype：
        # 不声明的话句柄按 c_int 返回会被截断（HWND/HICON 都是 8 字节），
        # WPARAM/LPARAM 按 int 传入又会 OverflowError。
        user32.CreateWindowExW.argtypes = [
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
        ]
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.RegisterClassW.argtypes = [ctypes.c_void_p]
        user32.RegisterClassW.restype = ctypes.c_ushort
        user32.DefWindowProcW.argtypes = [
            wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
        ]
        user32.DefWindowProcW.restype = LRESULT
        user32.LoadImageW.argtypes = [
            wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
            ctypes.c_int, ctypes.c_int, wintypes.UINT,
        ]
        user32.LoadImageW.restype = wintypes.HANDLE
        user32.CreatePopupMenu.restype = wintypes.HMENU
        user32.AppendMenuW.argtypes = [
            wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR
        ]
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        user32.TrackPopupMenu.argtypes = [
            wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, wintypes.HWND, ctypes.c_void_p,
        ]
        user32.TrackPopupMenu.restype = ctypes.c_int
        user32.PostMessageW.argtypes = [
            wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
        ]
        user32.DestroyMenu.argtypes = [wintypes.HMENU]
        user32.DestroyWindow.argtypes = [wintypes.HWND]
        user32.GetMessageW.argtypes = [
            ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT
        ]
        user32.GetMessageW.restype = ctypes.c_int
        user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.DispatchMessageW.restype = LRESULT
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        shell32.Shell_NotifyIconW.argtypes = [
            wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)
        ]
        shell32.Shell_NotifyIconW.restype = wintypes.BOOL

        class_name = "QaiTrayWindow"
        hinst = kernel32.GetModuleHandleW(None)
        icon_path = str(UI_DIR / "app.ico")
        hicon = user32.LoadImageW(
            None, icon_path, 1, 0, 0, 0x10  # IMAGE_ICON, LR_LOADFROMFILE
        )

        state = {"hwnd": None, "nid": None}

        def show_menu(hwnd) -> None:
            hmenu = user32.CreatePopupMenu()
            user32.AppendMenuW(hmenu, 0x800, _ID_OPEN, "打开 QAI 界面")
            user32.AppendMenuW(hmenu, 0x800, _ID_QUIT, "退出 QAI（停止机器人）")
            pt = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            user32.SetForegroundWindow(hwnd)
            cmd = user32.TrackPopupMenu(
                hmenu, 0x0100 | 0x0002, pt.x, pt.y, 0, hwnd, None  # TPM_RETURNCMD|TPM_RIGHTBUTTON
            )
            user32.PostMessageW(hwnd, 0x0000, 0, 0)  # WM_NULL
            user32.DestroyMenu(hmenu)
            if cmd == _ID_OPEN:
                log("[launcher] 托盘：打开界面")
                try:
                    open_window()
                except Exception as e:
                    log(f"[launcher] 托盘打开窗口失败：{e}")
            elif cmd == _ID_QUIT:
                log("[launcher] 托盘：退出 QAI")
                try:
                    shell32.Shell_NotifyIconW(2, ctypes.byref(state["nid"]))  # NIM_DELETE
                except Exception:
                    pass
                shutdown_launcher()

        def wnd_proc(hwnd, msg, wparam, lparam):
            if msg == _WM_TRAY:
                event = int(lparam) & 0xFFFF
                if event == _WM_LBUTTONDBLCLK:
                    log("[launcher] 托盘：双击打开界面")
                    try:
                        open_window()
                    except Exception as e:
                        log(f"[launcher] 托盘打开窗口失败：{e}")
                elif event == _WM_RBUTTONUP:
                    show_menu(hwnd)
                return 0
            if msg == 0x0010:  # WM_CLOSE
                user32.DestroyWindow(hwnd)
                return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        wc = WNDCLASSW()
        wc.lpfnWndProc = WNDPROC(wnd_proc)
        wc.hInstance = hinst
        wc.hIcon = hicon
        wc.lpszClassName = class_name
        if not user32.RegisterClassW(ctypes.byref(wc)):
            log("[launcher] 托盘：注册窗口类失败，跳过托盘图标")
            return

        hwnd = user32.CreateWindowExW(0, class_name, "QAI", 0, 0, 0, 0, 0, None, None, hinst, None)
        if not hwnd:
            log("[launcher] 托盘：创建消息窗口失败，跳过托盘图标")
            return
        state["hwnd"] = hwnd

        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = hwnd
        nid.uID = 1
        nid.uFlags = 0x1 | 0x2 | 0x4  # NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = _WM_TRAY
        nid.hIcon = hicon
        nid.szTip = "QAI 正在运行 · 双击打开界面"
        state["nid"] = nid
        # 开机自启时资源管理器可能还没就绪，重试几次
        added = False
        for _ in range(3):
            if shell32.Shell_NotifyIconW(0, ctypes.byref(nid)):  # NIM_ADD
                added = True
                break
            time.sleep(1.0)
        if not added:
            log("[launcher] 托盘：添加图标失败（任务栏未就绪或权限不足），跳过")
            return
        log("[launcher] 托盘图标已就绪")

        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
    except Exception as e:
        log(f"[launcher] 托盘图标不可用：{e}")


# ---------------------------------------------------------------- 关窗行为
def open_external_url(data: dict) -> dict:
    """用系统默认浏览器打开链接。

    教程里的网址都走这里：WebView2 里直接点 <a> 不一定能跳出应用，
    交给 webbrowser 打开才能在用户的浏览器里正常显示。
    """
    url = str((data or {}).get("url") or "").strip()
    if not re.match(r"^https?://", url, re.I):
        return {"ok": False, "error": "只允许 http/https 链接"}
    opened = False
    try:
        opened = bool(webbrowser.open(url))
    except Exception:
        opened = False
    if not opened:
        try:
            os.startfile(url)  # type: ignore[attr-defined]
            opened = True
        except Exception:
            opened = False
    if not opened:
        return {"ok": False, "url": url, "error": "系统没有可用的浏览器，请手动复制链接"}
    return {"ok": True, "url": url}


def save_close_behavior(data: dict) -> dict:
    """ask = 每次询问（默认）；exit = 全停；background = 只关界面。"""
    mode = str(data.get("mode") or "ask").strip().lower()
    if mode not in ("ask", "exit", "background"):
        return {"ok": False, "error": "mode 只能是 ask / exit / background"}
    try:
        _update_env({"QAI_CLOSE_BEHAVIOR": mode})
    except Exception as e:
        return {"ok": False, "error": f"写入 .env 失败：{e}"}
    reload_env()
    message = {
        "ask": "已保存：每次关窗口都会问一次",
        "background": "已保存：关窗口只关界面，机器人在后台继续运行（再双击 QAI 可重新打开窗口）",
        "exit": "已保存：关窗口会停止机器人并退出启动器",
    }[mode]
    log(f"[launcher] 关窗行为改为 {mode}")
    return {"ok": True, "mode": mode, "message": message}


def _close_window_only() -> None:
    """只结束窗口进程，启动器和机器人留在后台。"""
    try:
        for pid in _qai_window_pids():
            _taskkill(pid)
    except Exception as e:
        log(f"[launcher] 关闭窗口失败：{e}")


def close_decision(data: dict) -> dict:
    """用户在「关窗口」弹窗里选完之后调这里。"""
    payload = data or {}
    mode = str(payload.get("mode") or "").strip().lower()
    if mode not in ("exit", "background"):
        return {"ok": False, "error": "mode 只能是 exit 或 background"}
    if bool(payload.get("remember")):
        try:
            _update_env({"QAI_CLOSE_BEHAVIOR": mode})
            reload_env()
            log(f"[launcher] 用户选择记住关窗方式：{mode}")
        except Exception as e:
            log(f"[launcher] 记住关窗方式失败：{e}")

    if mode == "background":
        # 只关窗口：机器人和启动器继续跑，下次双击 QAI 会直接重开窗口
        threading.Timer(0.2, _close_window_only).start()
        return {"ok": True, "mode": mode, "message": "已转入后台运行"}

    def _finish() -> None:
        _close_window_only()
        shutdown_launcher()

    threading.Timer(0.2, _finish).start()
    return {"ok": True, "mode": mode, "message": "正在停止机器人并退出"}


# ---------------------------------------------------------------- Key 自检
_FALLBACK_MODELS = {
    "zhipu": ("https://open.bigmodel.cn/api/paas/v4/", "glm-4.7-flash"),
    "deepseek": ("https://api.deepseek.com/v1", "deepseek-flash"),
    "qwen": ("https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen3.8-flash"),
    "kimi": ("https://api.moonshot.cn/v1", "kimi-k3"),
    "hunyuan": ("https://tokenhub.tencentmaas.com/v1", "hy3-preview"),
}


def _provider_endpoint(provider: str) -> tuple[str, str]:
    """厂商对应的 (base_url, 默认模型)。优先用 model_catalog，取不到用内置表。"""
    try:
        from src.model_catalog import PROVIDERS

        for p in PROVIDERS:
            if p.get("id") == provider:
                models = p.get("models") or []
                return str(p.get("base") or ""), str(models[0]["id"] if models else "")
    except Exception:
        pass
    return _FALLBACK_MODELS.get(provider, ("", ""))


def test_provider_key(data: dict) -> dict:
    """真发一次最小请求，验证 Key / 模型 / 网络是否真的能用。

    以前 Key 填错只会在日志里报错，界面上什么提示都没有，用户只看到「机器人不说话」。
    """
    provider = str((data or {}).get("provider") or "").strip().lower()
    if not provider:
        return {"ok": False, "error": "缺少 provider"}
    env = _read_env()
    names = []
    try:
        from src.model_catalog import key_envs_for

        names = list(key_envs_for(provider))
    except Exception:
        names = [f"LLM_KEY_{provider.upper()}"]
    key = next((env.get(n, "").strip() for n in names if env.get(n, "").strip()), "")
    if not key:
        current = env.get("LLM_PROVIDER", "").strip() or _infer_provider_from_base(env.get("LLM_BASE_URL", ""))
        if provider == current:
            key = env.get("LLM_API_KEY", "").strip()
    if not key:
        return {"ok": False, "error": "这个厂商还没有保存 Key，先保存再测试"}

    base, model = _provider_endpoint(provider)
    current = env.get("LLM_PROVIDER", "").strip() or _infer_provider_from_base(env.get("LLM_BASE_URL", ""))
    if provider == current:
        base = env.get("LLM_BASE_URL", "").strip() or base
        model = env.get("LLM_MODEL", "").strip() or model
    if not base or not model:
        return {"ok": False, "error": f"不知道 {provider} 的接口地址/模型，请检查 .env"}

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "只回复一个字：好"}],
        "max_tokens": 8,
        "temperature": 0,
        "stream": False,
    }
    started = time.time()
    try:
        req = urllib.request.Request(
            base.rstrip("/") + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "User-Agent": f"QAI/{APP_VERSION}",
            },
            method="POST",
        )
        with _OPENER.open(req, timeout=25) as r:
            body = json.loads(r.read().decode("utf-8", "replace"))
        ms = int((time.time() - started) * 1000)
        reply = ""
        try:
            reply = str(body["choices"][0]["message"].get("content") or "").strip()
        except Exception:
            reply = ""
        log(f"[launcher] Key 自检通过：{provider} / {model}（{ms}ms）")
        return {
            "ok": True,
            "provider": provider,
            "model": model,
            "ms": ms,
            "reply": reply[:40],
            "message": f"{model} 可用（{ms}ms）" + (f"，模型回了「{reply[:20]}」" if reply else ""),
        }
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:200]
        except Exception:
            pass
        hint = "Key 无效或没权限" if e.code in (401, 403) else f"HTTP {e.code}"
        return {"ok": False, "error": f"{hint}：{detail or e.reason}"}
    except Exception as e:
        return {"ok": False, "error": f"连不上 {base}：{e}"}


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
# 版本号统一放在 version.py，发版只改那一行
try:
    from version import API_LEVEL, VERSION as APP_VERSION
except Exception:  # 极端情况下（文件被删）也要能起来
    APP_VERSION = "1.0.0"
    API_LEVEL = 2

GITHUB_RELEASES_LATEST = "https://api.github.com/repos/Algorandius/QAI/releases/latest"
UPDATE_DIR_NAME = "_update"

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_LOCK = threading.RLock()
_BOT_PROC: subprocess.Popen | None = None
# --no-dialog 时置 True：自动测试里不要弹出会阻塞的消息框
_DIALOG_DISABLED = False
# 写接口令牌：每次启动随机生成，只注入给同源页面。
# 没有它，用户浏览器里任意一个恶意网页都能 POST /api/update/from-file 装任意代码。
_API_TOKEN = secrets.token_urlsafe(24)
# 机器人是不是"用户希望它在跑"（看门狗据此决定要不要自动拉起）
_BOT_WANTED = False
# 看门狗的重启配额：时间窗内最多重启几次，避免「起来又死」无限闪烁
_RESTART_WINDOW_SEC = 300
_MAX_RESTARTS = 3
_ALIVE_RESET_SEC = 120
# 放弃自动重启时记下最后一次失败信息，供界面显示
_BOT_LAST_FAILURE: dict = {}
_WATCHDOG_STOP = threading.Event()
_NAPCAT_TOKEN: str | None = None
_NAPCAT_INSTALL_LOCK = threading.Lock()
_NAPCAT_INSTALL = {"status": "idle", "message": "", "progress": 0, "path": ""}

# 厂商 → .env 键名。启动器是独立进程，原则上不该 import 机器人代码，
# 所以这里留一份副本；src/ 在时以 model_catalog 为准，避免两边写歪。
_PROVIDER_KEY_ENV = {
    "zhipu": "LLM_KEY_ZHIPU",
    "deepseek": "LLM_KEY_DEEPSEEK",
    "qwen": "LLM_KEY_QWEN",
    "kimi": "LLM_KEY_KIMI",
    "hunyuan": "LLM_KEY_HUNYUAN",
    "tencent": "LLM_KEY_TENCENT",
    "tencent_intl": "LLM_KEY_TENCENT_INTL",
}
try:
    from src.model_catalog import PROVIDER_KEY_ENV as _SHARED_KEY_ENV

    _PROVIDER_KEY_ENV = dict(_SHARED_KEY_ENV)
except Exception:  # 只带启动器的残缺目录也能跑
    pass


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


def _write_pids() -> None:
    """记录本目录 QAI 的进程号，供 停止QAI.bat / stop.py 精确停止。

    只写 pid 文件、不按端口杀进程：端口上很可能是用户自己的其他服务。
    """
    payload: dict[str, int] = {"launcher": os.getpid(), "launcher_port": LAUNCHER_PORT}
    bot = _BOT_PROC.pid if _BOT_PROC is not None and _BOT_PROC.poll() is None else 0
    if not bot:
        # 启动器重启后接管已在运行的机器人时，_BOT_PROC 是空的，用端口反查一次
        bot = pid_on_port(BOT_PORT) or pid_on_port(PANEL_PORT) or 0
    if bot:
        payload["bot"] = bot
    try:
        win = int(WINDOW_PID_FILE.read_text(encoding="utf-8").strip())
        if win > 0:
            payload["window"] = win
    except Exception:
        pass
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        PIDS_FILE.write_text(json.dumps(payload), encoding="utf-8")
    except Exception as e:
        log(f"[launcher] 写进程记录失败: {e}")


# ---------------------------------------------------------------- 探测
def port_open(port: int, timeout: float = 0.25) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def _pick_free_port(preferred: int, tries: int = 10) -> int:
    """启动器端口被别的程序占用时，往后找一个空端口。

    8082 被别的软件（实测遇到过 QQ）抢走后，旧逻辑只打一行日志就退出，
    用户看到的就是「双击 QAI 毫无反应」。这里改成换端口继续启动。
    """
    reserved = {BOT_PORT, PANEL_PORT}
    candidates = [p for p in range(preferred + 1, preferred + 1 + tries)]
    candidates += list(range(18082, 18082 + tries))
    for port in candidates:
        if port in reserved or port == preferred:
            continue
        if not port_open(port):
            return port
    return 0


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


def _hidden_startupinfo():
    """让控制台子进程以隐藏窗口启动（父进程没有控制台时尤其需要）。"""
    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = subprocess.SW_HIDE
        return si
    except Exception:
        return None


def run_capture_hidden(args: list[str], timeout: float = 5.0) -> str:
    """跑一个控制台命令取回输出，**绝不弹出黑窗口**。

    启动器在 pythonw 下运行（没有控制台），一旦子进程不带隐藏标志，
    Windows 就会给每次调用新开一个控制台窗口——netstat 每 2 秒被状态轮询
    调一次，用户看到的就是满屏一闪而过的黑框。所以这里两条路都保证隐藏：

    1. 管道 + CREATE_NO_WINDOW：最快，正常情况下用它
    2. 个别安全软件禁止创建匿名管道（WinError 5），退回「输出重定向到临时文件
       + STARTUPINFO(SW_HIDE)」，依然不弹窗
    """
    try:
        r = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout,
            errors="replace", creationflags=_CREATE_NO_WINDOW or 0,
        )
        return r.stdout or ""
    except Exception:
        pass

    fd = -1
    path = ""
    try:
        fd, path = tempfile.mkstemp(prefix="qai-out-", suffix=".txt")
        with os.fdopen(fd, "wb") as fh:
            fd = -1
            subprocess.run(
                args, stdout=fh, stderr=subprocess.DEVNULL,
                timeout=timeout, startupinfo=_hidden_startupinfo(),
            )
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except Exception:
                pass
        if path:
            try:
                Path(path).unlink(missing_ok=True)
            except Exception:
                pass


def netstat_lines() -> list[str]:
    """netstat 输出（成功与否都不弹窗）。"""
    return run_capture_hidden(["netstat", "-ano", "-p", "TCP"], 4).splitlines()


def _pid_image(pid: int) -> str:
    """PID 对应的可执行文件路径；拿不到就返回空串。"""
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


def _port_owner_hint(port: int) -> str:
    """把「端口被占用」说得具体点：哪个 PID、哪个程序。

    以前只打 app=unknown，用户根本不知道是谁抢的（实测遇到过 QQ 占 8082）。
    """
    pid = pid_on_port(port)
    if not pid:
        return ""
    image = _pid_image(pid)
    return f"（PID {pid}{('  ' + image) if image else ''}）"


def _show_error_box(title: str, message: str) -> None:
    """无控制台启动（双击 vbs）时，用系统消息框把失败原因显示出来。

    以前启动失败只是写日志然后静默退出，用户看到的就是「双击毫无反应」。
    """
    try:
        import ctypes

        MB_ICONERROR = 0x10
        MB_TOPMOST = 0x40000
        ctypes.windll.user32.MessageBoxW(None, message, title, MB_ICONERROR | MB_TOPMOST)
    except Exception:
        pass


def _fatal(message: str, detail: str = "") -> int:
    """启动失败的统一出口：写日志 + 弹框，返回退出码 1。"""
    line = message + (f"：{detail}" if detail else "")
    log(f"[launcher] 启动失败 - {line}")
    if not _DIALOG_DISABLED:
        _show_error_box("QAI 启动失败", f"{line}\n\n日志文件：{LAUNCHER_LOG}")
    return 1


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
        # 看门狗放弃自动重启时把原因和日志尾巴带上，界面直接提示
        "bot_last_failure": dict(_BOT_LAST_FAILURE) or None,
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
    global _BOT_PROC, _BOT_WANTED
    with _LOCK:
        if port_open(BOT_PORT) or port_open(PANEL_PORT):
            _BOT_WANTED = True
            return {"ok": True, "started": False, "message": "机器人已经在运行（已接管）"}
        if _BOT_PROC is not None and _BOT_PROC.poll() is None:
            _BOT_WANTED = True
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
        _write_pids()
        ok = _wait_port(PANEL_PORT, True, 10) or _wait_port(BOT_PORT, True, 1)
        if not ok:
            # 把进程退出码和机器人日志的最后几行一起带回去，界面上直接能看到原因，
            # 不用让用户自己去翻 logs\launcher-bot.log。
            code = _BOT_PROC.poll()
            tail = _bot_log_tail(6)
            detail = f"，退出码 {code}" if code is not None else "，进程仍在但不监听端口"
            hint = ("\n最后几行日志：\n" + tail) if tail else ""
            return {
                "ok": False,
                "error": f"机器人启动失败{detail}{hint}",
                "log": log_tail(40),
            }
        _BOT_WANTED = True
        _BOT_LAST_FAILURE.clear()
        return {"ok": True, "started": True, "message": "机器人已启动"}


def _bot_log_tail(lines: int = 6) -> str:
    """机器人日志的最后几行非空内容（用于把错误直接显示给用户）。"""
    try:
        if not BOT_LOG.is_file():
            return ""
        rows = BOT_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return ""
    picked = [r.strip() for r in rows if r.strip()][-max(1, lines):]
    return "\n".join(picked)


def watchdog_loop() -> None:
    """机器人意外退出时自动拉起，但不会无限循环。

    - 记录退出码，日志里能看出是崩溃还是被杀
    - 5 分钟内最多自动重启 3 次；超过就停手并在状态里标记，避免「起来又死」无限闪烁
    - 机器人稳定运行满 2 分钟后，重启配额清零
    """
    global _BOT_WANTED, _BOT_LAST_FAILURE
    restarts: list[float] = []
    alive_since: float | None = None
    while not _WATCHDOG_STOP.wait(6.0):
        try:
            now = time.time()
            alive = _BOT_PROC is not None and _BOT_PROC.poll() is None
            if alive:
                if alive_since is None:
                    alive_since = now
                elif now - alive_since >= _ALIVE_RESET_SEC and restarts:
                    restarts.clear()
                    log("[launcher] 机器人已稳定运行，重启配额已清零")
                continue
            alive_since = None
            if not _BOT_WANTED:
                continue
            if port_open(BOT_PORT) or port_open(PANEL_PORT):
                continue       # 端口还在（可能是接管的外部进程），不动它

            code = _BOT_PROC.returncode if _BOT_PROC is not None else None
            restarts = [t for t in restarts if now - t < _RESTART_WINDOW_SEC]
            if len(restarts) >= _MAX_RESTARTS:
                _BOT_WANTED = False
                _BOT_LAST_FAILURE = {
                    "at": time.strftime("%H:%M:%S"),
                    "code": code,
                    "log": _bot_log_tail(8),
                    "reason": f"最近 {_RESTART_WINDOW_SEC // 60} 分钟内已自动重启 {len(restarts)} 次",
                }
                log(
                    f"[launcher] {_BOT_LAST_FAILURE['reason']}，退出码 {code}，"
                    "停止自动重启（避免反复开关）。请查看 logs\\launcher-bot.log"
                )
                continue
            restarts.append(now)
            log(
                f"[launcher] 检测到机器人已退出（退出码 {code}），"
                f"自动重启（{len(restarts)}/{_MAX_RESTARTS}，时间窗 {_RESTART_WINDOW_SEC // 60} 分钟）"
            )
            r = start_bot()
            if not r.get("ok"):
                log(f"[launcher] 自动重启失败：{r.get('error')}")
        except Exception as e:
            log(f"[launcher] 看门狗异常：{e}")


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
    global _BOT_PROC, _BOT_WANTED
    # 用户主动停止：看门狗不要再把它拉起来
    _BOT_WANTED = False
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
        _write_pids()
        log("[launcher] 机器人已停止")
        return {"ok": True, "message": "机器人已停止"}


def shutdown_launcher() -> None:
    """关闭窗口时同步停止机器人，然后退出启动器。"""
    global _BOT_WANTED
    _WATCHDOG_STOP.set()
    _BOT_WANTED = False
    result = {}
    try:
        result = stop_bot()
    except Exception as e:
        log(f"[launcher] 关闭时停止机器人失败: {e}")
    if not result.get("ok"):
        # stop_bot 超时也强制清掉 8080/8081，避免机器人留在后台。
        for port in (BOT_PORT, PANEL_PORT):
            pid = pid_on_port(port)
            if pid:
                _taskkill(pid)
    try:
        WINDOW_PID_FILE.unlink(missing_ok=True)
        PIDS_FILE.unlink(missing_ok=True)
    except Exception:
        pass
    log("[launcher] 窗口关闭，机器人已停止，启动器退出")
    os._exit(0)


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
            try:
                # 用相对路径判断是否越界：不能拿字符串前缀比，
                # 否则 "staging_evil/" 会被误判成在 "staging/" 里面
                target.relative_to(dest_resolved)
            except ValueError:
                log(f"[launcher] 跳过越界的压缩包条目：{info.filename}")
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


def _apply_onebot_config(cfg_path: Path, url: str, token: str) -> None:
    raw = json.loads(cfg_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{cfg_path.name} 不是 JSON 对象")
    network = raw.setdefault("network", {})
    if not isinstance(network, dict):
        raise ValueError(f"{cfg_path.name} 的 network 字段格式不正确")
    clients = network.setdefault("websocketClients", [])
    if not isinstance(clients, list):
        raise ValueError(f"{cfg_path.name} 的 websocketClients 字段格式不正确")

    target = next(
        (
            c for c in clients
            if isinstance(c, dict)
            and (
                "onebot/v11/ws" in str(c.get("url") or "")
                or str(c.get("name") or "").lower() in ("qai", "nonebot2", "bot1")
            )
        ),
        None,
    )
    if target is None:
        target = {
            "name": "QAI",
            "enable": True,
            "url": url,
            "reportSelfMessage": False,
            "messagePostFormat": "array",
            "token": token,
            "debug": False,
            "heartInterval": 30000,
            "reconnectInterval": 5000,
            "verifyCertificate": True,
        }
        clients.append(target)
    else:
        target.update({
            "enable": True,
            "url": url,
            "token": token,
            "messagePostFormat": "array",
            "reportSelfMessage": False,
        })

    backup = cfg_path.with_name(cfg_path.name + ".qai.bak")
    shutil.copy2(cfg_path, backup)
    tmp = cfg_path.with_name(cfg_path.name + ".tmp")
    tmp.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(cfg_path)


def quick_connect_onebot() -> dict:
    """把当前 QQ 的 NapCat 配置改成 QAI 的反向 WebSocket。"""
    global ENV
    env = reload_env()
    token = str(env.get("ONEBOT_ACCESS_TOKEN") or "").strip()
    token_changed = False
    if not token:
        token = "qai-" + secrets.token_hex(8)
        _update_env({"ONEBOT_ACCESS_TOKEN": token})
        env = reload_env()
        token_changed = True

    bot_port = _env_int("PORT", 8080)
    # 配置写入前重启一次机器人，确保它读到的就是当前 ONEBOT_ACCESS_TOKEN
    result = restart_bot()
    if not result.get("ok"):
        return {"ok": False, "error": result.get("error") or "机器人重启失败，未写入 NapCat 配置"}

    was_running = port_open(NAPCAT_PORT)
    if was_running:
        stopped = stop_napcat()
        if not stopped.get("ok"):
            return {"ok": False, "error": "需要先停止 NapCat 才能写入配置：" + str(stopped.get("error") or "")}
        time.sleep(0.8)
        # 实测：只在后台结束进程还不够。NapCat 的命令行窗口如果还开着，
        # 它退出时会把自己的配置写回去，于是刚写好的 OneBot 配置被覆盖。
        if port_open(NAPCAT_PORT):
            return {
                "ok": False,
                "error": (
                    "NapCat 还在运行（6099 端口仍被占用）。\n"
                    "请手动关闭 NapCat 的命令行窗口，等 QQ 完全退出后，再点一次「一键连接」。\n"
                    "命令行窗口没关时，NapCat 退出会把刚写好的配置覆盖回去，所以这一步必须手动做。"
                ),
            }

    config_dir = NAPCAT_DIR / "config"
    candidates = sorted(
        list(config_dir.glob("onebot11_*.json")) + list(config_dir.glob("onebot11.json")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    ) if config_dir.is_dir() else []
    if not candidates:
        return {
            "ok": False,
            "error": "没有找到 NapCat 的 onebot11 配置。请先启动 NapCat 并完成 QQ 登录。",
        }

    url = f"ws://127.0.0.1:{bot_port}/onebot/v11/ws"
    written: list[str] = []
    for cfg_path in candidates:
        try:
            _apply_onebot_config(cfg_path, url, token)
            written.append(str(cfg_path))
        except Exception as e:
            return {"ok": False, "error": f"写入 {cfg_path.name} 失败：{e}"}

    if was_running or not port_open(NAPCAT_PORT):
        started = start_napcat()
        if not started.get("ok"):
            return {"ok": False, "error": f"配置已写入，但启动 NapCat 失败：{started.get('error')}"}

    log(f"[launcher] OneBot 快捷连接已写入 {len(written)} 个配置（port={bot_port}）")
    return {
        "ok": True,
        "message": f"已写入 {len(written)} 个 NapCat 账号配置",
        "config": written[0] if written else "",
        "configs": written,
        "token_changed": token_changed,
        "napcat_running": port_open(NAPCAT_PORT),
        "restart_needed": False,
    }


# ---------------------------------------------------------------- HTTP
def _send_json(h, code: int, obj) -> None:
    body = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
    h.send_response(code)
    h.send_header("Content-Type", "application/json; charset=utf-8")
    h.send_header("Content-Length", str(len(body)))
    h.send_header("Cache-Control", "no-store")
    h.end_headers()
    h.wfile.write(body)


def _send_bytes(h, data: bytes, ctype: str, filename: str = "") -> None:
    h.send_response(200)
    h.send_header("Content-Type", ctype)
    h.send_header("Content-Length", str(len(data)))
    h.send_header("Cache-Control", "no-store")
    if filename:
        h.send_header("Content-Disposition", f'attachment; filename="{filename}"')
    h.end_headers()
    h.wfile.write(data)


def _send_file(h, path: Path, ctype: str) -> None:
    try:
        data = path.read_bytes()
    except Exception:
        _send_json(h, 404, {"ok": False, "error": "not found"})
        return
    h.send_response(200)
    h.send_header("Content-Type", ctype)
    h.send_header("Content-Length", str(len(data)))
    # no-store 而不是 no-cache：no-cache 仍允许 WebView 用缓存副本，
    # 改了 app.css / app.js 却看不到效果就是这么来的。
    h.send_header("Cache-Control", "no-store, must-revalidate")
    h.send_header("Pragma", "no-cache")
    h.send_header("Expires", "0")
    h.end_headers()
    h.wfile.write(data)


def _same_site_ok(handler) -> bool:
    """拒绝来自其它网站（或 iframe）的请求。

    浏览器对「简单请求」不做 CORS 预检，所以 Cross-Site 的 POST 是能打进来的；
    光靠 CORS 挡不住，必须在服务端看这组头。
    """
    # 只拒 cross-site。same-site 必须放行：QAI 自己就是 8082 的页面里
    # 嵌 8081 的 iframe，同主机不同端口在浏览器里算 same-site。
    site = (handler.headers.get("Sec-Fetch-Site") or "").strip().lower()
    if site == "cross-site":
        return False
    origin = (handler.headers.get("Origin") or "").strip()
    if origin:
        try:
            host = (urlparse(origin).hostname or "").lower()
        except Exception:
            return False
        if host not in ("127.0.0.1", "localhost", "::1"):
            return False
    return True


def _token_ok(handler) -> bool:
    got = (handler.headers.get("X-QAI-Token") or "").strip()
    return bool(got) and hmac.compare_digest(got, _API_TOKEN)


def _serve_index(handler) -> None:
    """首页：把写接口令牌注入成 meta，只有同源脚本读得到。"""
    path = UI_DIR / "index.html"
    if not path.is_file():
        _send_json(handler, 500, {"ok": False, "error": "缺少 launcher_ui/index.html"})
        return
    try:
        html = path.read_text(encoding="utf-8")
    except Exception as e:
        _send_json(handler, 500, {"ok": False, "error": f"读 index.html 失败：{e}"})
        return
    meta = f'<meta name="qai-token" content="{_API_TOKEN}">'
    if "qai-token" in html:
        html = re.sub(r'<meta name="qai-token"[^>]*>', meta, html, count=1)
    else:
        html = html.replace("<head>", f"<head>\n  {meta}", 1) if "<head>" in html else meta + html
    body = html.encode("utf-8")
    handler.send_response(200)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_OPTIONS(self):
        # 不返回任何 CORS 头：跨站带自定义头的请求会在预检阶段失败
        self.send_response(403)
        self.send_header("Content-Length", "0")
        self.end_headers()

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

    def _body_raw(self, limit: int = DATA_IMPORT_MAX_BYTES) -> bytes:
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            length = 0
        if length <= 0:
            return b""
        return self.rfile.read(min(length, limit))

    def do_GET(self):
        u = urlparse(self.path)
        if not _same_site_ok(self):
            _send_json(self, 403, {"ok": False, "error": "拒绝来自其它网站的请求"})
            return
        if u.path in ("/", "/index.html"):
            _serve_index(self)
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
        if u.path == "/api/napcat/pan":
            url = "https://pan.quark.cn/s/3dc6f10f0ebd"
            opened = False
            try:
                opened = bool(webbrowser.open(url))
            except Exception:
                opened = False
            if not opened:
                try:
                    os.startfile(url)  # type: ignore[attr-defined]
                    opened = True
                except Exception:
                    opened = False
            if opened:
                _send_json(self, 200, {"ok": True, "url": url})
            else:
                _send_json(self, 500, {"ok": False, "error": "系统没有可用的浏览器，请手动复制链接", "url": url})
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
        if u.path == "/api/provider-keys":
            _send_json(self, 200, provider_keys_payload())
            return
        if u.path == "/api/version":
            _send_json(self, 200, {
                "ok": True,
                "app": APP_NAME,
                "version": APP_VERSION,
                "api": API_LEVEL,
            })
            return
        if u.path == "/api/update/check":
            _send_json(self, 200, update_check())
            return
        if u.path == "/api/data/export":
            try:
                blob = export_data_zip()
            except Exception as e:
                _send_json(self, 500, {"ok": False, "error": f"打包失败：{e}"})
                return
            name = f"QAI-backup-{time.strftime('%Y%m%d-%H%M')}.zip"
            _send_bytes(self, blob, "application/zip", name)
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
        # 所有写操作都必须：同源 + 带令牌。缺一个就直接拒绝。
        # 表单提交设不了自定义头，跨站 fetch 带自定义头会触发预检（我们回 403），
        # 所以这两条一起把「用户随便打开个网页就能操控 QAI」堵死了。
        if not _same_site_ok(self):
            _send_json(self, 403, {"ok": False, "error": "拒绝来自其它网站的请求"})
            return
        if not _token_ok(self):
            _send_json(self, 403, {"ok": False, "error": "缺少或错误的控制令牌，请刷新 QAI 窗口后重试"})
            return
        try:
            if u.path == "/api/shutdown":
                _send_json(self, 200, {"ok": True, "message": "启动器正在退出"})
                threading.Timer(0.3, shutdown_launcher).start()
                return
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
            if u.path == "/api/napcat/quick-connect":
                _send_json(self, 200, quick_connect_onebot())
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
            if u.path == "/api/provider-keys":
                _send_json(self, 200, save_provider_key(self._body_json()))
                return
            if u.path == "/api/data/import":
                _send_json(self, 200, import_data_zip(self._body_raw()))
                return
            if u.path == "/api/update/apply":
                _send_json(self, 200, update_apply(self._body_json()))
                return
            if u.path == "/api/update/from-file":
                _send_json(self, 200, update_from_local(self._body_raw()))
                return
            if u.path == "/api/settings/close-behavior":
                _send_json(self, 200, save_close_behavior(self._body_json()))
                return
            if u.path == "/api/close/decision":
                _send_json(self, 200, close_decision(self._body_json()))
                return
            if u.path == "/api/open-url":
                _send_json(self, 200, open_external_url(self._body_json()))
                return
            if u.path == "/api/provider-keys/test":
                _send_json(self, 200, test_provider_key(self._body_json()))
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
                [str(launcher), str(script), url, _API_TOKEN],
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
                DATA_DIR.mkdir(parents=True, exist_ok=True)
                WINDOW_PID_FILE.write_text(str(window_pid), encoding="utf-8")
            except Exception:
                pass
            _write_pids()
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
    global LAUNCHER_PORT, _DIALOG_DISABLED
    ap = argparse.ArgumentParser(description="QAI 桌面控制中心")
    ap.add_argument("--no-open", action="store_true", help="不自动打开应用窗口")
    ap.add_argument("--no-auto-start", action="store_true", help="不自动启动机器人")
    ap.add_argument("--no-dialog", action="store_true", help="启动失败时不弹消息框（自动测试用）")
    args = ap.parse_args()
    _DIALOG_DISABLED = args.no_dialog

    if is_launcher_running():
        log("[launcher] 控制中心已在运行，直接打开窗口")
        if not args.no_open:
            open_window()
        return 0

    if port_open(LAUNCHER_PORT):
        old_app = launcher_status_app()
        if old_app == "qqbot-launcher":
            if not replace_legacy_launcher():
                return _fatal(
                    "旧版控制中心还在运行，替换失败",
                    "请先双击「停止QAI.bat」，再从新目录打开 QAI",
                )
        else:
            hint = _port_owner_hint(LAUNCHER_PORT) or (
                f"（app={old_app}）" if old_app else ""
            )
            alt = _pick_free_port(LAUNCHER_PORT)
            if not alt:
                return _fatal(
                    f"端口 {LAUNCHER_PORT} 被其他程序占用{hint}，也没找到可用端口",
                    "请关闭占用该端口的程序后重试，或在 .env 里改 LAUNCHER_PORT",
                )
            log(
                f"[launcher] 端口 {LAUNCHER_PORT} 被其他程序占用{hint}，"
                f"改用端口 {alt} 启动"
            )
            LAUNCHER_PORT = alt

    try:
        server = ThreadingHTTPServer(("127.0.0.1", LAUNCHER_PORT), _Handler)
    except Exception as e:
        return _fatal(f"控制中心无法监听端口 {LAUNCHER_PORT}", str(e))

    log(f"[launcher] 控制中心已启动：http://127.0.0.1:{LAUNCHER_PORT}/")
    _write_pids()
    _tighten_permissions()
    start_tray()
    threading.Thread(target=watchdog_loop, name="qai-watchdog", daemon=True).start()
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
