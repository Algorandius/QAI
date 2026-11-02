"""
Web 管理控制台（默认 127.0.0.1:8081）

前端位于 `webpanel_ui/`，本文件提供：
- 静态文件（index.html / app.css / app.js / favicon.svg）
- JSON API（状态、场景、人设、好感度、聊天记录、表情库、配置）
- 本机 HTTP 服务（ThreadingHTTPServer）
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import subprocess
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from nonebot import logger

from src import commands_doc, diary, diary_auto, longterm, memory, model_catalog, personas, sticker_lib
from src.config import ROOT, env, env_bool, env_int
from src.stats import STATS, uptime_str

ENABLED = env_bool("WEB_PANEL", True)
PORT = max(1, env_int("WEB_PANEL_PORT", 8081))
UI_DIR = ROOT / "webpanel_ui"
ENV_FILE = ROOT / ".env"
MAX_BODY = 12 * 1024 * 1024
# 写接口令牌：启动时随机生成，注入管理台页面，用于拒绝跨站请求。
# 同机原生程序仍可读取页面本身，这一层靠文件权限（见 launcher 的权限收紧）。
PANEL_TOKEN = secrets.token_urlsafe(24)

# ---------------------------------------------------------------- 远程访问
#
# 默认只监听 127.0.0.1，和以前行为一致。要让手机连进来，把 WEB_PANEL_HOST
# 设成 0.0.0.0（或用 WEB_PANEL_REMOTE=true）。一旦对外监听，本机以外的请求
# 必须通过密码登录；令牌随页面下发，不能单独作为远程鉴权。
BIND_HOST = env("WEB_PANEL_HOST", "").strip()
_REMOTE_FLAG = env_bool("WEB_PANEL_REMOTE", False)
if not BIND_HOST:
    # 手机访问是默认能力：没有显式配置时一律对外监听（远程仍需密码登录），
    # 想只在本机用可以显式写 WEB_PANEL_HOST=127.0.0.1
    BIND_HOST = "0.0.0.0"
elif BIND_HOST in ("127.0.0.1", "localhost", "::1") and env_bool("WEB_PANEL_FORCE_REMOTE", False):
    BIND_HOST = "0.0.0.0"
IS_LOOPBACK_BIND = BIND_HOST in ("127.0.0.1", "localhost", "::1")
# 只要不是只绑回环，就等于对外开了口子，必须启用登录。
# 只绑回环以外一律视为对外，需启用登录。WEB_PANEL_REMOTE 仅作等效开关。
REMOTE_ENABLED = (not IS_LOOPBACK_BIND) or _REMOTE_FLAG

_LOOPBACK_NAMES = ("127.0.0.1", "localhost", "::1")
# Tailscale / 运营商级 NAT 常用段，ipaddress 不认为它是 private
_CGNAT = ipaddress.ip_network("100.64.0.0/10")


def _is_loopback_addr(addr: str) -> bool:
    a = (addr or "").strip()
    if not a:
        return False
    if a.lower() in _LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(a).is_loopback
    except ValueError:
        return False


def _is_private_addr(addr: str) -> bool:
    """局域网 / Tailscale（CGNAT 100.64/10）/ 链路本地。

    注意：Python 的 ipaddress 把 100.64/10 归为 is_global=False 但 is_private=False，
    而 Tailscale 正好用这一段，所以必须显式放行，否则组网后访问会被 403。
    """
    a = (addr or "").strip()
    if not a:
        return False
    try:
        ip = ipaddress.ip_address(a)
    except ValueError:
        return False
    if ip.is_private or ip.is_link_local:
        return True
    if isinstance(ip, ipaddress.IPv4Address) and ip in _CGNAT:
        return True
    return False


# 登录密码：支持明文 WEB_PANEL_PASSWORD，或哈希 WEB_PANEL_PASSWORD_HASH
# （格式 scrypt$<n>$<r>$<p>$<salt_b64>$<hash_b64>，可用 tools/panel_password.py 生成）。
# 只存哈希更好：.env 可能被备份、被打包、被误传。
PASSWORD_HASH = env("WEB_PANEL_PASSWORD_HASH", "").strip()
PASSWORD_PLAIN = env("WEB_PANEL_PASSWORD", "").strip()
REMOTE_USER = env("WEB_PANEL_USER", "qai") or "qai"
SESSION_HOURS = max(1, env_int("WEB_PANEL_SESSION_HOURS", 24 * 7))

_COOKIE_NAME = "qai_session"
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2 ** 14, 8, 1
_sessions: dict[str, float] = {}
_sessions_lock = threading.Lock()
_login_fails: dict[str, tuple[int, float]] = {}
_login_lock = threading.Lock()
LOGIN_MAX_FAILS = 5
LOGIN_LOCK_SECONDS = 300


def _hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.scrypt(
        password.encode("utf-8"), salt=salt,
        n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32,
    )
    return "scrypt${}${}${}${}${}".format(
        _SCRYPT_N, _SCRYPT_R, _SCRYPT_P,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(dk).decode("ascii"),
    )


def _verify_password(password: str) -> bool:
    if not password:
        return False
    if PASSWORD_HASH:
        try:
            scheme, n, r, p, salt_b64, hash_b64 = PASSWORD_HASH.split("$")
            if scheme != "scrypt":
                return False
            dk = hashlib.scrypt(
                password.encode("utf-8"),
                salt=base64.b64decode(salt_b64),
                n=int(n), r=int(r), p=int(p), dklen=32,
            )
            return hmac.compare_digest(dk, base64.b64decode(hash_b64))
        except Exception:
            return False
    if PASSWORD_PLAIN:
        return hmac.compare_digest(password, PASSWORD_PLAIN)
    return False


def _login_configured() -> bool:
    return bool(PASSWORD_HASH or PASSWORD_PLAIN)


def _new_session() -> str:
    sid = secrets.token_urlsafe(32)
    with _sessions_lock:
        now = time.time()
        # 清理过期会话
        for k in [k for k, exp in _sessions.items() if exp <= now]:
            _sessions.pop(k, None)
        _sessions[sid] = now + SESSION_HOURS * 3600
    return sid


def _session_valid(sid: str) -> bool:
    if not sid:
        return False
    with _sessions_lock:
        exp = _sessions.get(sid)
        if exp is None:
            return False
        if exp <= time.time():
            _sessions.pop(sid, None)
            return False
        return True


def _session_drop(sid: str) -> None:
    with _sessions_lock:
        _sessions.pop(sid, None)


def _login_blocked(key: str) -> bool:
    with _login_lock:
        n, until = _login_fails.get(key, (0, 0.0))
        if n >= LOGIN_MAX_FAILS and time.time() < until:
            return True
        if n >= LOGIN_MAX_FAILS and time.time() >= until:
            _login_fails.pop(key, None)
        return False


def _login_failed(key: str) -> None:
    with _login_lock:
        n, _ = _login_fails.get(key, (0, 0.0))
        _login_fails[key] = (n + 1, time.time() + LOGIN_LOCK_SECONDS)


def _login_ok(key: str) -> None:
    with _login_lock:
        _login_fails.pop(key, None)


_LOGIN_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#0b0d13">
<title>QAI · 登录</title>
<link rel="icon" href="/qai-icon-64.png" type="image/png">
<style>
:root { color-scheme: dark; }
* { box-sizing: border-box; }
body {
  margin: 0; min-height: 100vh; display: grid; place-items: center; padding: 24px;
  background: radial-gradient(1200px 600px at 50% -10%, #16233a 0%, #0a0c12 55%);
  color: #e8ecf5; font-family: system-ui, -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif;
}
.card {
  width: 100%; max-width: 380px; padding: 30px 26px; border-radius: 18px;
  background: rgba(20, 26, 38, .82); border: 1px solid rgba(120, 150, 210, .18);
  box-shadow: 0 24px 60px rgba(0, 0, 0, .5); backdrop-filter: blur(14px);
}
.logo { width: 52px; height: 52px; border-radius: 15px; display: grid; place-items: center;
  background: linear-gradient(140deg, #4c7dff, #8b5cf6); font-size: 24px; margin-bottom: 16px; }
h1 { font-size: 19px; margin: 0 0 6px; font-weight: 650; letter-spacing: .2px; }
p.sub { margin: 0 0 22px; font-size: 13px; color: #8b95ab; line-height: 1.55; }
label { display: block; font-size: 12.5px; color: #9aa5bd; margin-bottom: 7px; }
input {
  width: 100%; padding: 12px 13px; font-size: 16px; border-radius: 11px;
  border: 1px solid rgba(120, 150, 210, .22); background: rgba(8, 12, 20, .75);
  color: #e8ecf5; outline: none; transition: border-color .16s, box-shadow .16s;
}
input:focus { border-color: #4c7dff; box-shadow: 0 0 0 3px rgba(76, 125, 255, .18); }
button {
  width: 100%; margin-top: 18px; padding: 12px; font-size: 15px; font-weight: 600;
  border: 0; border-radius: 11px; cursor: pointer; color: #fff;
  background: linear-gradient(140deg, #4c7dff, #7c5cf6);
  transition: transform .12s, filter .16s;
}
button:hover { filter: brightness(1.08); }
button:active { transform: scale(.985); }
button:disabled { opacity: .6; cursor: default; }
.msg { min-height: 20px; margin-top: 13px; font-size: 13px; color: #ff8080; }
.remember { display: flex; align-items: center; gap: 8px; margin-top: 14px; font-size: 13px; color: #9aa5bd; }
.remember input { width: 16px; height: 16px; padding: 0; margin: 0; accent-color: #4c7dff; }
.forgot { margin-top: 12px; text-align: center; font-size: 13px; }
.forgot a { color: #7ba7ff; text-decoration: none; }
.forgot a:hover { text-decoration: underline; }
.reset-panel { margin-top: 14px; padding: 13px 14px; border-radius: 11px;
  background: rgba(8, 12, 20, .6); border: 1px solid rgba(120, 150, 210, .18); }
.reset-panel.hidden { display: none; }
.reset-panel code { background: rgba(0,0,0,.4); padding: 1px 5px; border-radius: 4px; font-size: 12px; }
.reset-panel .steps { margin: 0; padding-left: 18px; font-size: 12.5px; color: #b8c1d4; line-height: 1.9; }
</style>
</head>
<body>
<form class="card" id="f">
  <div class="logo">🔒</div>
  <h1>QAI 管理控制台</h1>
  <p class="sub">这台控制器只允许已登录的设备访问。</p>
  <label for="u">用户名</label>
  <input id="u" name="u" autocomplete="username" placeholder="qai">
  <label for="p" style="margin-top:14px">密码</label>
  <input id="p" name="p" type="password" autocomplete="current-password" required>
  <div class="remember">
    <input id="r" name="r" type="checkbox" checked>
    <label for="r" style="margin:0">记住这台设备</label>
  </div>
  <button id="b" type="submit">登录</button>
  <div class="msg" id="m"></div>
  <div class="forgot">
    <a href="#" id="forgotLink">忘记密码？</a>
  </div>
  <div class="reset-panel hidden" id="resetPanel">
    <p class="sub" style="margin:0 0 12px">
      密码只存哈希，找不回来，只能重置。<br>
      <b>重置必须在运行机器人的那台电脑上操作</b>（本机访问免登录）。
    </p>
    <ol class="steps">
      <li>回到电脑，打开 QAI</li>
      <li>管理台左侧点「手机」</li>
      <li>点「重置密码」，新密码会显示出来</li>
    </ol>
    <p class="sub" style="margin:12px 0 0">
      打不开管理台时，删掉 <code>.env</code> 里的这一行，重启 QAI 即可恢复成只允许本机：
      <br><code>WEB_PANEL_HOST=0.0.0.0</code>
    </p>
  </div>
</form>
<script>
(function () {
  var f = document.getElementById("f"), m = document.getElementById("m"), b = document.getElementById("b");
  document.getElementById("forgotLink").addEventListener("click", function (e) {
    e.preventDefault();
    document.getElementById("resetPanel").classList.toggle("hidden");
  });
  f.addEventListener("submit", async function (e) {
    e.preventDefault();
    m.textContent = ""; b.disabled = true;
    try {
      var r = await fetch("/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          user: document.getElementById("u").value,
          password: document.getElementById("p").value,
          remember: document.getElementById("r").checked
        })
      });
      var d = await r.json().catch(function () { return {}; });
      if (r.ok && d.ok) { location.replace("/"); return; }
      m.textContent = d.error || "登录失败";
    } catch (err) {
      m.textContent = "无法连接服务器";
    }
    b.disabled = false;
  });
})();
</script>
</body>
</html>
"""

_STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".json": "application/json; charset=utf-8",
    ".webmanifest": "application/manifest+json; charset=utf-8",
}
_STATIC_FILES = (
    "/", "/index.html", "/app.css", "/app.js", "/theme.css", "/theme.js",
    "/favicon.svg", "/manifest.webmanifest",
    "/qai-icon.png", "/qai-icon-64.png", "/qai-cover.png",
)
# 远程访问、尚未登录时仍然放行的静态资源（登录页与它自己的样式）
_LOGIN_PUBLIC = (
    "/app.css", "/theme.css", "/theme.js", "/favicon.svg",
    "/manifest.webmanifest", "/qai-icon.png", "/qai-icon-64.png",
)

# ---------------------------------------------------------------- 小工具
def _mood_label(v: float) -> str:
    if v >= 6:
        return "很开心"
    if v >= 2:
        return "心情不错"
    if v > -2:
        return "平静"
    if v > -6:
        return "有点低落"
    return "很难过"


def _is_secret(key: str) -> bool:
    k = key.strip().upper()
    return (
        k.endswith(("_KEY", "_TOKEN", "_SECRET", "_PASSWORD"))
        or "TOKEN" in k
        or "SECRET" in k
        or "PASSWORD" in k
    )


# 各固定模型的参数能力；设置页据此隐藏不适用参数。
MODEL_FEATURES = {
    "glm-4.7-flash": {"image": False, "video": False, "web": False, "tools": True},
    "glm-5.3-flash": {"image": True, "video": True, "web": False, "tools": True},
    "glm-5.3": {"image": False, "video": False, "web": False, "tools": True},
    "deepseek-flash": {"image": True, "video": False, "web": False, "tools": True},
    "deepseek-v4-pro": {"image": False, "video": False, "web": False, "tools": True},
    "qwen3.8-flash": {"image": True, "video": True, "web": True, "tools": True},
    "kimi-k3": {"image": True, "video": False, "web": True, "tools": True},
    "hy3-preview": {"image": False, "video": False, "web": False, "tools": True},
    "hy4-preview": {"image": True, "video": False, "web": False, "tools": True},
}


def _current_model_features() -> dict:
    model = _read_env_pairs().get("LLM_MODEL", "").strip()
    return MODEL_FEATURES.get(model, {"image": True, "video": True, "web": True, "tools": True})


# 常用 .env 参数的中文快捷编辑表。只放非密钥、可安全调整的项。
ENV_QUICK_GROUPS: list[dict] = [
    {
        "title": "回复节奏",
        "items": [
            {"key": "AI_MAX_CHARS", "label": "整轮字数", "type": "int", "unit": "字", "min": 50, "max": 2000, "default": 600, "desc": "一次回复的总字数上限"},
            {"key": "AI_MSG_MAX_CHARS", "label": "单条字数", "type": "int", "unit": "字", "min": 8, "max": 500, "default": 20, "desc": "单条消息最多几个字，超出会切开"},
            {"key": "AI_MAX_MSGS", "label": "单轮条数", "type": "int", "unit": "条", "min": 1, "max": 20, "default": 5, "desc": "一次回复最多连发几条"},
            {"key": "AI_TYPE_CPS", "label": "打字速度", "type": "float", "unit": "字/秒", "min": 1, "max": 100, "default": 16, "desc": "越小越慢，越像真人手打"},
            {"key": "LLM_TEMPERATURE", "label": "模型温度", "type": "float", "unit": "", "min": 0, "max": 2, "default": 0.8, "desc": "越高越随机，越低越稳定"},
            {"key": "AI_GROUP_AUTO_PROB", "label": "接话概率", "type": "percent", "unit": "%", "min": 0, "max": 100, "default": 0.8, "desc": "没被 @ 时主动接话的概率"},
            {"key": "AI_GROUP_AUTO_COOLDOWN", "label": "群接话冷却", "type": "int", "unit": "秒", "min": 0, "max": 3600, "default": 10, "desc": "同一个群两次主动接话的最小间隔"},
            {"key": "AI_COOLDOWN_SEC", "label": "用户冷却", "type": "int", "unit": "秒", "min": 0, "max": 3600, "default": 5, "desc": "同一用户两次对话的最小间隔"},
        ],
    },
    {
        "title": "能力开关",
        "items": [
            {"key": "AI_GROUP_AUTO", "label": "自主接话", "type": "bool", "unit": "", "min": 0, "max": 1, "default": True, "desc": "允许机器人不 @ 也主动接话"},
            {"key": "AI_WEB_TOOL", "label": "联网搜索", "type": "bool", "unit": "", "min": 0, "max": 1, "default": True, "desc": "允许模型调用网页搜索/抓取工具", "requires": "tools"},
            {"key": "AI_WEB_MAX_SEARCH", "label": "单轮搜索次数", "type": "int", "unit": "次", "min": 0, "max": 10, "default": 3, "desc": "一次回复最多搜索几次", "requires": "tools"},
            {"key": "AI_POKE", "label": "戳一戳", "type": "bool", "unit": "", "min": 0, "max": 1, "default": True, "desc": "允许机器人主动拍一拍"},
            {"key": "AI_LONGTERM", "label": "长期记忆", "type": "bool", "unit": "", "min": 0, "max": 1, "default": True, "desc": "允许模型写入/读取长期记忆"},
            {"key": "AI_STICKER", "label": "表情包", "type": "bool", "unit": "", "min": 0, "max": 1, "default": True, "desc": "允许机器人发表情包"},
        ],
    },
    {
        "title": "图像能力",
        "items": [
            {"key": "AI_VISION", "label": "识图", "type": "bool", "unit": "", "min": 0, "max": 1, "default": False, "desc": "允许模型理解图片（需要视觉模型）", "requires": "image"},
            {"key": "AI_VISION_SEARCH", "label": "识图联网核对", "type": "bool", "unit": "", "min": 0, "max": 1, "default": True, "desc": "识图后结合联网内容核对图片信息", "requires": "image"},
            {"key": "AI_VISION_MAX_IMAGES", "label": "单轮识图张数", "type": "int", "unit": "张", "min": 1, "max": 5, "default": 2, "desc": "一次最多把几张图送给模型", "requires": "image"},
            {"key": "AI_IMAGE_MAX_PER_TURN", "label": "单轮发图张数", "type": "int", "unit": "张", "min": 1, "max": 5, "default": 1, "desc": "一次回复最多发几张图片", "requires": "image"},
            {"key": "AI_IMAGE_TRIGGER_PROB", "label": "主动配图概率", "type": "percent", "unit": "%", "min": 0, "max": 100, "default": 0.4, "desc": "主动找图/配图的概率", "requires": "image"},
            {"key": "AI_IMAGE_COOLDOWN", "label": "发图冷却", "type": "int", "unit": "秒", "min": 0, "max": 3600, "default": 90, "desc": "两次发图的最小间隔", "requires": "image"},
        ],
    },
]
_ENV_QUICK_INDEX = {it["key"]: it for g in ENV_QUICK_GROUPS for it in g["items"]}


def _env_quick_payload() -> list[dict]:
    pairs = _read_env_pairs()
    caps = _current_model_features()
    out = []
    for group in ENV_QUICK_GROUPS:
        items = []
        for it in group["items"]:
            req = it.get("requires")
            if req and not caps.get(req):
                continue
            item = dict(it)
            item["value"] = pairs.get(it["key"], str(it["default"]))
            items.append(item)
        if not items:
            continue
        out.append({"title": group["title"], "items": items})
    return out


def _coerce_env_item(item: dict, raw) -> tuple[str, object]:
    key = item["key"]
    typ = item["type"]
    if typ == "bool":
        if isinstance(raw, bool):
            val = raw
        else:
            val = str(raw).strip().lower() in ("1", "true", "on", "yes", "开")
        return ("true" if val else "false"), val
    if typ == "percent":
        val = float(raw)
        if val > 1:
            val = val / 100.0
        if not (0.0 <= val <= 1.0):
            raise ValueError(f"{item['label']}应在 0~100%")
        return f"{val:.4f}".rstrip("0").rstrip("."), val
    if typ == "int":
        val = int(float(raw))
    else:
        val = float(raw)
    lo, hi = item["min"], item["max"]
    if not (lo <= val <= hi):
        raise ValueError(f"{item['label']}应在 {lo}~{hi}{item['unit']}")
    return str(val), val


def _apply_env_hot(values: dict[str, object]) -> None:
    """把常用 .env 参数同步到正在运行的 ai_chat / web_tools。"""
    try:
        from src.plugins import ai_chat as ai
        from src import web_tools
        attrs = {
            "AI_MAX_CHARS": "MAX_CHARS",
            "AI_MSG_MAX_CHARS": "AI_MSG_MAX_CHARS",
            "AI_MAX_MSGS": "AI_MAX_MSGS",
            "AI_COOLDOWN_SEC": "COOLDOWN",
            "AI_TYPE_CPS": "TYPE_CPS",
            "AI_GROUP_AUTO_PROB": "AI_GROUP_AUTO_PROB",
            "AI_GROUP_AUTO_COOLDOWN": "AI_GROUP_AUTO_COOLDOWN",
            "LLM_TEMPERATURE": "LLM_TEMPERATURE",
            "AI_VISION_MAX_IMAGES": "AI_VISION_MAX_IMAGES",
            "AI_VISION_SEARCH": "AI_VISION_SEARCH",
            "AI_IMAGE_MAX_PER_TURN": "AI_IMAGE_MAX_PER_TURN",
            "AI_IMAGE_TRIGGER_PROB": "AI_IMAGE_TRIGGER_PROB",
            "AI_IMAGE_COOLDOWN": "AI_IMAGE_COOLDOWN",
        }
        for key, val in values.items():
            if key in attrs:
                setattr(ai, attrs[key], val)
            if key in getattr(ai, "PARAM_DEFAULTS", {}):
                ai.PARAM_DEFAULTS[key] = val
        if "AI_GROUP_AUTO" in values:
            ai.AI_GROUP_AUTO = bool(values["AI_GROUP_AUTO"])
            ai.AUTO_ENABLED = bool(values["AI_GROUP_AUTO"])
        for key, attr in (("AI_VISION", "AI_VISION"), ("AI_VISION_SEARCH", "AI_VISION_SEARCH"),
                          ("AI_POKE", "AI_POKE"),
                          ("AI_LONGTERM", "AI_LONGTERM"), ("AI_STICKER", "AI_STICKER")):
            if key in values:
                setattr(ai, attr, bool(values[key]))
        if "AI_WEB_TOOL" in values:
            web_tools.WEB_TOOL = bool(values["AI_WEB_TOOL"])
        if "AI_WEB_MAX_SEARCH" in values:
            web_tools.WEB_MAX_SEARCH = int(values["AI_WEB_MAX_SEARCH"])
    except Exception as e:
        logger.warning(f"[webpanel] 常用参数热更新失败: {e}")


def _read_env_pairs() -> dict[str, str]:
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


def _env_list(key: str) -> list[str]:
    raw = _read_env_pairs().get(key, "")
    return [x for x in re.split(r"[,，\s]+", raw) if x]


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


def _scene_ref(raw) -> tuple[str, int]:
    m = re.fullmatch(r"(group|private):(\d{1,20})", str(raw or "").strip())
    if not m:
        raise ValueError("场景格式应为 group:群号 或 private:QQ号")
    return m.group(1), int(m.group(2))


def _scene_key(kind: str, sid: int) -> str:
    return f"{kind}:{sid}"


def _update_env(updates: dict[str, str]) -> None:
    """只改指定的 .env 键，保留注释和其余行；先写临时文件再 rename。"""
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


# ---------------------------------------------------------------- 数据组装
def _persona_payload(pid: str) -> dict:
    meta = personas.read_meta(pid)
    return {
        "id": pid,
        "name": meta.get("name") or pid,
        "desc": meta.get("desc") or "",
        "config": meta.get("config") or {},
        "text": personas.text_of(pid),
        "avatar": personas.avatar_url(pid),
    }


# ---------------------------------------------------------------- 手机访问
#
# 管理台在 8081，启动器在 8082。浏览器直接跨端口调 8082 会被同源挡住，
# 而且启动器接口还要令牌。所以统一由这里代理一层：
# 管理台自己带令牌访问本机 8082，前端只跟 8081 说话。

def _launcher_base() -> str:
    port = max(1, env_int("LAUNCHER_PORT", 8082))
    return f"http://127.0.0.1:{port}"


def _launcher_token() -> str:
    """读启动器落盘的令牌（启动器每次启动重写 data/api_token）。

    这个文件只对本机用户可读，权限收在数据目录里。
    """
    # 允许用环境变量覆盖，便于测试与自定义数据目录
    override = env("QAI_DATA_DIR", "").strip()
    base = Path(override) if override else (ROOT / "data")
    p = base / "api_token"
    try:
        if p.is_file():
            return p.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return ""


def _launcher_call(path: str, method: str = "GET", body: dict | None = None) -> tuple[int, dict]:
    import urllib.error
    import urllib.request

    url = _launcher_base().rstrip("/") + path
    data = json.dumps(body or {}).encode("utf-8") if method == "POST" else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    tok = _launcher_token()
    if tok:
        req.add_header("X-QAI-Token", tok)
    try:
        with urllib.request.urlopen(req, timeout=6) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"ok": False, "error": raw[:200]}
    except Exception as e:
        return 503, {"ok": False, "error": f"启动器未响应：{e}"}


# ---------------------------------------------------------------- 手机访问
#
# 实现在 webpanel 内，不依赖 8082 启动器：管理台可能随 bot.py 单独运行。

def _lan_ip() -> str:
    import socket as _sock
    s = _sock.socket(_sock.AF_INET, _sock.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        try:
            s.close()
        except Exception:
            pass


def _random_password() -> str:
    """不用抄错的密码：四段三字符，去掉 i l o 0 1 这类易混字符。"""
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    return "-".join(
        "".join(secrets.choice(alphabet) for _ in range(3)) for _ in range(4)
    )


def _qr_svg(text: str) -> str:
    if not text:
        return ""
    try:
        import io as _io
        import segno
        buf = _io.BytesIO()
        segno.make(text, error="m").save(
            buf, kind="svg", scale=4, border=2, dark="#0b0e15", light="#ffffff"
        )
        return buf.getvalue().decode("utf-8")
    except Exception:
        return ""


_PHONE_PWD_FILE = None  # 延迟解析：ROOT 在运行期可能被测试替换


def _phone_pwd_file() -> Path:
    override = env("QAI_DATA_DIR", "").strip()
    base = Path(override) if override else (ROOT / "data")
    return base / "phone_password.txt"


def _remember_password(pw: str) -> None:
    """把明文密码留一份在本机，方便用户随时查看。

    只在 data/ 下，不写进 .env —— .env 会被打包和备份，
    里面躺明文密码等于把钥匙一起发出去。
    本机访问才能读到，远程必须登录。
    """
    try:
        f = _phone_pwd_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(pw, encoding="utf-8")
        try:
            os.chmod(f, 0o600)
        except Exception:
            pass
    except Exception as e:
        logger.warning(f"[webpanel] 记录手机密码失败：{e}")


def _recall_password() -> str:
    try:
        f = _phone_pwd_file()
        if f.is_file():
            return f.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return ""


def _phone_access_status() -> dict:
    pairs = _read_env_pairs()
    host = (pairs.get("WEB_PANEL_HOST") or "").strip()
    enabled = host not in ("", "127.0.0.1", "localhost", "::1")
    ip = _lan_ip()
    port = str(PORT)
    user = (pairs.get("WEB_PANEL_USER") or "qai").strip() or "qai"
    has_pwd = bool(
        (pairs.get("WEB_PANEL_PASSWORD_HASH") or "").strip()
        or (pairs.get("WEB_PANEL_PASSWORD") or "").strip()
    )
    url = f"http://{ip}:{port}"
    return {
        "ok": True,
        "enabled": enabled,
        "host": host,
        "has_password": has_pwd,
        "user": user,
        "port": port,
        "lan_ip": ip,
        "url": url,
        "qr_svg": _qr_svg(url) if enabled else "",
        # 当前进程实际绑在哪儿，用来提醒「改了要重启」
        "active_bind": BIND_HOST,
        "restart_needed": enabled and IS_LOOPBACK_BIND,
        # 本机存的那份明文密码。调用方会判断是不是本机，再决定要不要下发。
        "_saved_password": _recall_password(),
    }


def _phone_access_enable() -> dict:
    pairs = _read_env_pairs()
    updates = {"WEB_PANEL_HOST": "0.0.0.0"}
    has_pwd = bool(
        (pairs.get("WEB_PANEL_PASSWORD_HASH") or "").strip()
        or (pairs.get("WEB_PANEL_PASSWORD") or "").strip()
    )
    password = ""
    # 有哈希但没有本地明文记录时，说明密码已不可取回，重设一个新的。
    recalled = _recall_password()
    if not has_pwd or not recalled:
        password = _random_password()
        updates["WEB_PANEL_PASSWORD_HASH"] = _hash_password(password)
        updates["WEB_PANEL_USER"] = (pairs.get("WEB_PANEL_USER") or "qai").strip() or "qai"
    _update_env(updates)
    if password:
        _remember_password(password)
    st = _phone_access_status()
    st["password"] = password
    st["message"] = "已开启，请记下密码" if password else "已开启"
    logger.info(f"[webpanel] 手机访问已开启：{st['url']}")
    return st


def _phone_access_disable() -> dict:
    _update_env({"WEB_PANEL_HOST": "127.0.0.1"})
    st = _phone_access_status()
    st["message"] = "已关闭，管理台只监听本机"
    logger.info("[webpanel] 手机访问已关闭")
    return st


def _phone_access_reset() -> dict:
    password = _random_password()
    pairs = _read_env_pairs()
    _update_env({
        "WEB_PANEL_PASSWORD_HASH": _hash_password(password),
        "WEB_PANEL_USER": (pairs.get("WEB_PANEL_USER") or "qai").strip() or "qai",
    })
    _remember_password(password)
    st = _phone_access_status()
    st["password"] = password
    st["message"] = "密码已重置"
    logger.info("[webpanel] 手机访问密码已重置")
    return st


def _models_payload() -> dict:
    current_provider = env("LLM_PROVIDER", "")
    current_model = env("LLM_MODEL", "")
    if not current_provider:
        current_base = env("LLM_BASE_URL", "").lower()
        for p in model_catalog.PROVIDERS:
            host = p["base"].split("//", 1)[-1].split("/", 1)[0].lower()
            if any(m["id"] == current_model for m in p["models"]) or (host and host in current_base):
                current_provider = p["id"]
                break
    providers = []
    for p in model_catalog.PROVIDERS:
        # 别名（混元 == tencent / tencent_intl）由 model_catalog 统一处理
        key_set = any(env(name, "") for name in model_catalog.key_envs_for(p["id"]))
        if not key_set and p["id"] == current_provider:
            key_set = bool(env("LLM_API_KEY", ""))
        providers.append({
            "id": p["id"],
            "name": p["name"],
            "base": p["base"],
            "key_set": key_set,
            "models": p["models"],
        })
    return {
        "ok": True,
        "current": {"provider": current_provider, "model": current_model},
        "providers": providers,
        # 前端「去配置」要跳到启动器的各厂商 Key 面板
        "launcher_port": max(1, env_int("LAUNCHER_PORT", 8082)),
    }


def _status_payload() -> dict:
    scenes = memory.list_scenes()
    smap = personas.load_scene_map()
    seen: set[str] = set()
    for s in scenes:
        key = _scene_key(s["scene"], s["id"])
        s["key"] = key
        s["persona"] = smap.get(key, "")
        s["mood_label"] = _mood_label(float(s.get("mood") or 0))
        s["longterm"] = longterm.count(key)
        s["last_active"] = float(memory.LAST_ACTIVE.get(s["id"], 0) or 0)
        seen.add(key)
    # 绑定过但还没聊过的场景也显示，方便解除/调整
    for key, pid in smap.items():
        if key in seen:
            continue
        kind, _, sid = key.partition(":")
        if not sid.isdigit():
            continue
        scenes.append({
            "scene": kind,
            "id": int(sid),
            "key": key,
            "size": 0,
            "mood": 0.0,
            "mood_label": _mood_label(0.0),
            "summary_len": 0,
            "persona": pid,
            "longterm": longterm.count(key),
            "last_active": 0.0,
        })
    scenes.sort(key=lambda x: (x["scene"] != "group", str(x["id"])))
    current = personas.current_id()
    return {
        "ok": True,
        "uptime": uptime_str(),
        "recv": STATS["recv"],
        "sent": STATS["sent"],
        "ai_calls": STATS["ai_calls"],
        "ai_fail": STATS["ai_fail"],
        "blocked": STATS["blocked"],
        "scenes": scenes,
        "longterm_total": sum(int(s.get("longterm") or 0) for s in scenes),
        "persona": {
            "id": current,
            "name": personas.display_name(current),
            "list": personas.list_personas(),
        },
    }


def _diary_payload(qs: dict) -> dict:
    """日记页数据：目录 + 选中那一本的全文。

    查询参数：
      key   指定「场景|人设」（不给则用第一个）
      q     按正文/人设名/场景筛选
    """
    wanted = str(qs.get("key", [""])[0] or "").strip()
    text_q = str(qs.get("q", [""])[0] or "").strip().lower()

    keys = diary.all_keys()
    if text_q:
        keys = [
            k for k in keys
            if text_q in str(k.get("latest_text") or "").lower()
            or text_q in str(k.get("persona_name") or "").lower()
            or text_q in str(k.get("persona") or "").lower()
            or text_q in str(k.get("scene_id") or "")
        ]

    scene_names = _scene_name_map()

    def label(k: dict) -> str:
        scene = k.get("scene") or ""
        sid = k.get("scene_id") or 0
        where = scene_names.get(f"{scene}:{sid}") or (f"群 {sid}" if scene == "group" else f"私聊 {sid}")
        who = k.get("persona_name") or k.get("persona") or "默认人设"
        return f"{where} · {who}"

    for k in keys:
        k["label"] = label(k)

    if not wanted and keys:
        wanted = keys[0]["key"]

    entries: list[dict] = []
    if wanted:
        scene, sid, pid = diary.split_key(wanted)
        if scene:
            entries = diary.list_entries(scene == "group", sid, pid)

    # 今天还没写的（让用户知道谁会写）
    try:
        pending = diary_auto.which_entries_missing_today()
    except Exception:
        pending = []
    for p in pending:
        where = scene_names.get(f"{p['scene']}:{p['scene_id']}") or f"{p['scene']} {p['scene_id']}"
        p["label"] = f"{where} · {p.get('persona_name') or p.get('persona')}"

    return {
        "ok": True,
        "keys": keys,
        "selected": wanted,
        "entries": entries,
        "pending": pending,
        "stats": diary.stats(),
        "auto": {
            "enabled": bool(diary_auto.DIARY_ENABLE and diary_auto.DIARY_AUTO),
            "hour": diary_auto.DIARY_HOUR,
            "minute": diary_auto.DIARY_MINUTE,
            "describe": diary_auto.describe(),
        },
    }


def _scene_name_map() -> dict[str, str]:
    """场景 id → 可读名字（群名拿不到，就用「群 12345」）。"""
    out: dict[str, str] = {}
    try:
        from src.plugins.ai_chat import GROUP_CTX, PRIVATE_CTX
    except Exception:
        return out
    for gid in list(GROUP_CTX.keys())[:200]:
        out[f"group:{gid}"] = f"群 {gid}"
    for uid in list(PRIVATE_CTX.keys())[:200]:
        out[f"private:{uid}"] = f"私聊 {uid}"
    return out


def _chatlog_payload(qs: dict) -> list[dict]:
    try:
        n = int(qs.get("n", ["200"])[0])
    except (TypeError, ValueError):
        n = 200
    n = max(1, min(n, 2000))
    scene = str(qs.get("scene", [""])[0] or "").strip()
    text = str(qs.get("q", [""])[0] or "").strip().lower()
    logs = memory.read_chatlog(2000)
    if scene:
        logs = [r for r in logs if _scene_key(str(r.get("scene", "")), int(r.get("scene_id", 0))) == scene]
    if text:
        logs = [
            r for r in logs
            if text in str(r.get("user", "")).lower()
            or text in str(r.get("bot", "")).lower()
            or text in str(r.get("user_id", ""))
        ]
    return logs[-n:]


def _masked_config() -> list[dict]:
    out: list[dict] = []
    try:
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, v = s.split("=", 1)
            out.append({"key": k.strip(), "value": v, "masked": _is_secret(k) and bool(v)})
    except Exception:
        pass
    return out


def _sticker_tags(raw) -> list[str]:
    if isinstance(raw, str):
        raw = re.split(r"[,，、;；\s]+", raw)
    out: list[str] = []
    for t in (raw or []):
        t = str(t).strip()
        if t and t not in out:
            out.append(t)
    return out[:12]


def _sticker_auto_tags(data_url: str) -> tuple[list[str], str]:
    """用当前模型给表情图/动图打几个中文标签；失败时返回原因让 UI 提示手动补充。"""
    try:
        from src.plugins import ai_chat as ai
    except Exception as e:
        return [], f"模型模块未就绪：{e}"
    if not getattr(ai, "AI_VISION", False):
        return [], "当前模型未开启识图，已跳过自动标签，请手动补充表情含义"
    if not getattr(ai, "LLM_API_KEY", ""):
        return [], "未配置模型 Key，已跳过自动标签，请手动补充表情含义"
    prompt = (
        "这是一张 QQ 表情包/动图。请用中文概括它的含义和情绪，"
        "只输出 3~6 个用逗号分隔的短标签，不要解释、不要编号。"
        "例如：开心, 撒娇, 无语, 猫猫"
    )
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": data_url}},
        ],
    }]
    try:
        text = asyncio.run(ai.call_llm(messages, temperature=0.1))
    except Exception as e:
        logger.warning(f"[webpanel] 表情识图失败: {e}")
        return [], "当前模型无法识图，已跳过自动标签，请手动补充表情含义"
    tags = [x.strip(" 。.!！?？、") for x in re.split(r"[,，、;；\n]+", text or "") if x.strip()]
    tags = [x[:12] for x in tags if x][:6]
    if not tags:
        return [], "模型没有返回可用标签，请手动补充表情含义"
    return tags, ""


def _sticker_import(name: str, data_url: str, tags=None, sublib: str = "", sublibs=None) -> dict:
    valid = set(sticker_lib.load_sublibs())
    wanted = []
    if isinstance(sublibs, list):
        wanted.extend(str(x).strip() for x in sublibs)
    if sublib:
        wanted.append(str(sublib).strip())
    wanted = [x for x in dict.fromkeys(wanted) if x]
    bad = [x for x in wanted if x not in valid]
    if bad:
        raise ValueError(f"没有这个表情子库：{bad[0]}")
    m = re.match(r"^data:image/(gif|png|jpe?g|webp);base64,(.+)$", str(data_url or ""), re.S | re.I)
    if not m:
        raise ValueError("只支持 GIF / PNG / JPG / WebP 的 base64 数据")
    ext = m.group(1).lower().replace("jpeg", "jpg")
    try:
        raw = base64.b64decode(m.group(2), validate=True)
    except Exception:
        raise ValueError("图片 base64 解码失败")
    if not raw:
        raise ValueError("图片为空")
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError("单张图片不能超过 8MB")
    fname = f"{hashlib.md5(raw).hexdigest()[:16]}.{ext}"
    path = sticker_lib.LIB_DIR / fname
    path.write_bytes(raw)
    items = sticker_lib.load_index()
    found = next((x for x in items if x.get("file") == fname), None)
    provided = _sticker_tags(tags)
    warning = ""
    if not provided:
        auto, warning = _sticker_auto_tags(data_url)
        provided = auto
    if found:
        merged = list(dict.fromkeys(list(found.get("tags") or []) + provided))
        found["tags"] = merged
    else:
        found = {"file": fname, "tags": provided, "url": ""}
        items.append(found)
    if wanted:
        found["sublibs"] = list(dict.fromkeys(wanted))
    found.pop("sublib", None)
    sticker_lib.save_index(items)
    return {**found, "warning": warning}


def _sticker_delete(filename: str) -> bool:
    fname = str(filename or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", fname) or "/" in fname or "\\" in fname:
        raise ValueError("非法表情文件名")
    items = sticker_lib.load_index()
    kept = [x for x in items if x.get("file") != fname]
    if len(kept) == len(items):
        return False
    sticker_lib.save_index(kept)
    try:
        p = (sticker_lib.LIB_DIR / fname).resolve()
        if p.parent == sticker_lib.LIB_DIR.resolve() and p.is_file():
            p.unlink()
    except Exception as e:
        logger.warning(f"[webpanel] 删除表情文件失败: {e}")
    return True


def _open_sticker_folder() -> None:
    sticker_lib.LIB_DIR.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        os.startfile(str(sticker_lib.LIB_DIR))  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", str(sticker_lib.LIB_DIR)])


# ---------------------------------------------------------------- HTTP Handler
class _Handler(BaseHTTPRequestHandler):
    # 每个请求都要发额外响应头（Set-Cookie）时的暂存区，_send_* 里统一吐出
    _extra_headers: list = []

    def log_message(self, *a):
        pass

    def handle_one_request(self):
        self._extra_headers = []
        try:
            super().handle_one_request()
        finally:
            self._extra_headers = []

    # ---- 安全：只接受同源请求，写操作再校验令牌 ----
    def _client_is_local(self) -> bool:
        return _is_loopback_addr(self.client_address[0])

    def _session_sid(self) -> str:
        raw = self.headers.get("Cookie") or ""
        if not raw:
            return ""
        try:
            jar = SimpleCookie()
            jar.load(raw)
        except Exception:
            return ""
        morsel = jar.get(_COOKIE_NAME)
        return morsel.value if morsel else ""

    def _authed(self) -> bool:
        """本机免登录；远程必须持有有效会话。

        远程不再单靠 X-QAI-Token：那个令牌跟着 index.html 一起下发，
        能打开页面的人都能拿到，拿到就等于完全控制。
        """
        if self._client_is_local():
            return True
        return _session_valid(self._session_sid())

    def _same_site_ok(self, allow_no_origin: bool = True) -> bool:
        """挡住其它网站发起的跨站请求。

        浏览器对「简单请求」不做 CORS 预检，跨站 POST/GET 是能打进来的；
        必须在服务端看 Sec-Fetch-Site / Origin。

        没有 Origin 是常态：浏览器导航、同源 GET 都不带它，非浏览器客户端
        （curl、手机原生客户端）也不带。旧实现就是放行的，这里保持，
        否则本机打开控制台会直接 403。真正的跨站请求会带
        Sec-Fetch-Site: cross-site 或一个外站 Origin，两种都能挡住。
        """
        site = (self.headers.get("Sec-Fetch-Site") or "").strip().lower()
        if site == "cross-site":
            return False
        origin = (self.headers.get("Origin") or "").strip()
        if not origin:
            return True if allow_no_origin else False
        try:
            host = (urlparse(origin).hostname or "").lower()
        except Exception:
            return False
        if _is_loopback_addr(host):
            return True
        # 已开启远程访问时，允许局域网 / Tailscale 网段内的同源调用
        if REMOTE_ENABLED and _is_private_addr(host):
            return True
        return False

    def _token_ok(self) -> bool:
        got = (self.headers.get("X-QAI-Token") or "").strip()
        return bool(got) and hmac.compare_digest(got, PANEL_TOKEN)

    def _set_session_cookie(self, sid: str) -> None:
        # 只允许 HTTP 传输时不要 Secure，否则手机直接用 http:// 访问会存不下
        parts = [
            f"{_COOKIE_NAME}={sid}",
            "Path=/",
            f"Max-Age={SESSION_HOURS * 3600}",
            "HttpOnly",
            "SameSite=Lax",
        ]
        self._extra_headers.append(("Set-Cookie", "; ".join(parts)))

    def _clear_session_cookie(self) -> None:
        self._extra_headers.append(
            ("Set-Cookie", f"{_COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax")
        )

    def do_OPTIONS(self):
        # 不回 CORS 头：跨站带自定义头的请求会在预检阶段失败
        self.send_response(403)
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ---- 响应 ----
    def _emit_extra_headers(self) -> None:
        for k, v in self._extra_headers:
            self.send_header(k, v)

    def _send_json(self, code: int, obj) -> None:
        body = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._emit_extra_headers()
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, code: int, body: bytes, ctype: str, cache: str = "no-cache") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self._emit_extra_headers()
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location: str) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _body_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            length = 0
        if length <= 0:
            return {}
        if length > MAX_BODY:
            raise ValueError("请求体过大（上限 2MB）")
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            raise ValueError("请求体不是合法 JSON")
        return data if isinstance(data, dict) else {}

    # ---- 静态文件 ----
    def _serve_static(self, path: str) -> bool:
        name = "index.html" if path in ("/", "/index.html") else path.lstrip("/")
        p = (UI_DIR / name).resolve()
        try:
            p.relative_to(UI_DIR.resolve())
        except ValueError:
            return False
        if not p.is_file():
            return False
        ctype = _STATIC_TYPES.get(p.suffix.lower(), "application/octet-stream")
        try:
            body = p.read_bytes()
        except Exception:
            return False
        if name == "index.html":
            # 把写接口令牌注入成 meta，只有同源脚本读得到
            try:
                html = body.decode("utf-8")
                meta = f'<meta name="qai-token" content="{PANEL_TOKEN}">'
                # 告诉前端是不是远程连接（远程才显示退出登录）
                _mode = "1" if (REMOTE_ENABLED and not self._client_is_local()) else "0"
                meta = meta + '<meta name="qai-remote" content="' + _mode + '">'
                if "qai-token" in html:
                    html = re.sub(r'<meta name="qai-token"[^>]*>', meta, html, count=1)
                elif "<head>" in html:
                    html = html.replace("<head>", f"<head>\n  {meta}", 1)
                else:
                    html = meta + html
                body = html.encode("utf-8")
            except Exception:
                pass
        self._send_bytes(200, body, ctype)
        return True

    def _send_login_page(self) -> None:
        self._send_bytes(
            200, _LOGIN_PAGE.encode("utf-8"), "text/html; charset=utf-8", cache="no-store"
        )

    def _serve_sticker(self, name: str) -> None:
        """返回表情库图片（严格限制文件名，防路径穿越）。"""
        if not name or not re.fullmatch(r"[A-Za-z0-9._-]+", name):
            self._send_json(404, {"ok": False, "error": "bad name"})
            return
        p = sticker_lib.LIB_DIR / name
        try:
            if p.resolve().parent != sticker_lib.LIB_DIR.resolve():
                raise ValueError("outside lib")
            data = p.read_bytes()
        except Exception:
            self._send_json(404, {"ok": False, "error": "not found"})
            return
        ctype = {
            ".gif": "image/gif", ".png": "image/png",
            ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
        }.get(p.suffix.lower(), "application/octet-stream")
        self._send_bytes(200, data, ctype, cache="max-age=3600")

    # ---- GET ----
    def do_GET(self):
        u = urlparse(self.path)
        path = u.path
        if not self._same_site_ok():
            self._send_json(403, {"ok": False, "error": "拒绝来自其它网站的请求"})
            return
        qs = parse_qs(u.query, keep_blank_values=True)

        if path == "/stickers":
            self._redirect("/#stickers")
            return
        if path in _STATIC_FILES:
            # 未登录只放行登录页资源
            if REMOTE_ENABLED and not self._authed() and path not in _LOGIN_PUBLIC:
                if path in ("/", "/index.html"):
                    self._send_login_page()
                    return
                self._send_json(401, {"ok": False, "error": "需要登录"})
                return
            if not self._serve_static(path):
                self._send_json(404, {"ok": False, "error": "UI 文件缺失，请检查 webpanel_ui/ 目录"})
            return

        # 远程未登录：API 一律 401，前端据此跳登录
        if REMOTE_ENABLED and not self._authed():
            self._send_json(401, {"ok": False, "error": "需要登录", "login_required": True})
            return

        try:
            if path == "/api/status":
                self._send_json(200, _status_payload())
                return
            if path == "/api/models":
                self._send_json(200, _models_payload())
                return
            if path == "/api/personas":
                self._send_json(200, {
                    "ok": True,
                    "current": personas.current_id(),
                    "items": [_persona_payload(x["id"]) for x in personas.list_personas()],
                })
                return
            if path == "/api/persona":
                pid = str(qs.get("pid", [""])[0] or "").strip() or personas.current_id()
                if not personas.exists(pid):
                    self._send_json(200, {"ok": False, "error": f"没有这个人设：{pid}"})
                    return
                self._send_json(200, {"ok": True, **_persona_payload(pid)})
                return
            if path == "/api/affinity":
                kind, sid = _scene_ref(qs.get("scene", [""])[0])
                self._send_json(200, {
                    "ok": True,
                    "scene": _scene_key(kind, sid),
                    "items": memory.list_affinity(sid, 200),
                })
                return
            if path == "/api/chatlog":
                self._send_json(200, _chatlog_payload(qs))
                return
            if path == "/api/config":
                self._send_json(200, _masked_config())
                return
            if path == "/api/env/params":
                model = _read_env_pairs().get("LLM_MODEL", "")
                self._send_json(200, {
                    "ok": True,
                    "model": model,
                    "features": _current_model_features(),
                    "groups": _env_quick_payload(),
                })
                return
            if path == "/api/settings":
                pairs = _read_env_pairs()
                self._send_json(200, {
                    "ok": True,
                    "allowed_groups": _env_list("ALLOWED_GROUPS"),
                    "bot_admin_qq": _env_list("BOT_ADMIN_QQ"),
                    "ai_group_trigger": pairs.get("AI_GROUP_TRIGGER", "at"),
                    "web_panel_port": PORT,
                    "restart_required": False,
                })
                return
            if path == "/api/phone":
                st = _phone_access_status()
                # 明文密码只给本机看：远程能拿到就等于密码形同虚设
                if self._client_is_local():
                    st["saved_password"] = st.pop("_saved_password", "")
                else:
                    st.pop("_saved_password", None)
                self._send_json(200, st)
                return
            if path == "/api/tutorial":
                guide = ROOT / "新手教程.md"
                try:
                    text = guide.read_text(encoding="utf-8")
                except Exception:
                    text = "# 新手教程\n\n教程文件缺失，请查看项目根目录的「新手教程.md」。"
                self._send_json(200, {"ok": True, "text": text})
                return
            if path == "/api/stickers":
                items = sticker_lib.load_index()
                self._send_json(200, {
                    "ok": True,
                    "count": len(items),
                    "dir": str(sticker_lib.LIB_DIR),
                    "catalog": sticker_lib.catalog(),
                    "items": items,
                    "sublibs": sticker_lib.sublib_counts(),
                })
                return
            if path == "/api/sticker-sublibs":
                self._send_json(200, {"ok": True, "sublibs": sticker_lib.sublib_counts()})
                return
            if path == "/api/commands":
                # 指令目录直接从插件源码解析，命令改了面板自动跟着变
                self._send_json(200, commands_doc.list_commands())
                return
            if path == "/api/diary":
                self._send_json(200, _diary_payload(qs))
                return
            if path.startswith("/persona-avatar/"):
                pid = path[len("/persona-avatar/"):]
                if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", pid):
                    self._send_json(404, {"ok": False, "error": "not found"})
                    return
                p = personas.avatar_path(pid)
                if not p:
                    self._send_json(404, {"ok": False, "error": "not found"})
                    return
                ctype = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                         ".webp": "image/webp", ".gif": "image/gif"}.get(p.suffix.lower(), "application/octet-stream")
                self._send_bytes(200, p.read_bytes(), ctype, "private, max-age=60")
                return
            if path.startswith("/sticker-img/"):
                self._serve_sticker(path[len("/sticker-img/"):])
                return
        except ValueError as e:
            self._send_json(400, {"ok": False, "error": str(e)})
            return
        except Exception as e:
            logger.warning(f"[webpanel] GET {path} 失败: {e}")
            self._send_json(500, {"ok": False, "error": str(e)})
            return

        self._send_json(404, {"ok": False, "error": "not found"})

    # ---- POST ----
    def _handle_auth(self, path: str) -> None:
        key = self.client_address[0]
        if path == "/api/logout":
            # 必须先读掉请求体再造响应：服务端发完响应就关连接，
            # 请求体还在路上时会触发 RST，客户端拿到 ConnectionAborted。
            try:
                self._body_json()
            except ValueError:
                pass
            if self._same_site_ok():
                _session_drop(self._session_sid())
                self._clear_session_cookie()
            self._send_json(200, {"ok": True})
            return

        # 登录：允许不带 Origin 的客户端（手机直接 POST）
        if not self._same_site_ok(allow_no_origin=True):
            self._send_json(403, {"ok": False, "error": "拒绝来自其它网站的请求"})
            return
        if _login_blocked(key):
            self._send_json(429, {
                "ok": False,
                "error": f"尝试次数过多，请 {LOGIN_LOCK_SECONDS // 60} 分钟后再试",
            })
            return
        if not _login_configured():
            self._send_json(503, {
                "ok": False,
                "error": "未配置远程访问密码，请在 .env 中设置 WEB_PANEL_PASSWORD",
            })
            return
        try:
            data = self._body_json()
        except ValueError as e:
            self._send_json(400, {"ok": False, "error": str(e)})
            return

        user = str(data.get("user") or "").strip() or REMOTE_USER
        password = str(data.get("password") or "")
        if user != REMOTE_USER or not _verify_password(password):
            _login_failed(key)
            self._send_json(401, {"ok": False, "error": "用户名或密码错误"})
            return

        _login_ok(key)
        sid = _new_session()
        self._set_session_cookie(sid)
        logger.info(f"[webpanel] 远程登录成功：{key}")
        self._send_json(200, {"ok": True, "expires_in": SESSION_HOURS * 3600})

    def do_POST(self):
        u = urlparse(self.path)
        path = u.path

        # 登录 / 登出：不需要令牌，靠密码与限速保护
        if path in ("/api/login", "/api/logout"):
            self._handle_auth(path)
            return

        # 同源 + 令牌：缺一个就拒，避免网页跨站改人设/清记忆
        # 拒绝前也要把请求体读掉：服务端发完响应即关连接，请求体滞留会触发 RST。
        if not self._same_site_ok():
            try:
                self._body_json()
            except ValueError:
                pass
            self._send_json(403, {"ok": False, "error": "拒绝来自其它网站的请求"})
            return
        if not self._token_ok():
            try:
                self._body_json()
            except ValueError:
                pass
            self._send_json(403, {"ok": False, "error": "缺少或错误的控制令牌，请刷新页面后重试"})
            return
        # 远程还必须已登录：令牌是随页面下发的，单靠它挡不住拿到页面的人
        if REMOTE_ENABLED and not self._authed():
            try:
                self._body_json()
            except ValueError:
                pass
            self._send_json(401, {"ok": False, "error": "需要登录", "login_required": True})
            return
        try:
            data = self._body_json()
        except ValueError as e:
            self._send_json(400, {"ok": False, "error": str(e)})
            return

        try:
            if path == "/api/persona":
                pid = str(data.get("pid") or "").strip() or personas.current_id()
                if not personas.exists(pid):
                    self._send_json(200, {"ok": False, "error": f"没有这个人设：{pid}"})
                    return
                text = str(data.get("text") or "")
                if not text.strip():
                    self._send_json(200, {"ok": False, "error": "人设正文不能为空"})
                    return
                (personas.PERSONA_DIR / pid / "persona.txt").write_text(text, encoding="utf-8")
                self._send_json(200, {"ok": True, "id": pid})
                return

            if path == "/api/persona/params":
                pid = str(data.get("pid") or "").strip() or personas.current_id()
                if not personas.exists(pid):
                    self._send_json(200, {"ok": False, "error": f"没有这个人设：{pid}"})
                    return
                params = data.get("params")
                if not isinstance(params, dict):
                    raise ValueError("params 必须是对象")
                meta = personas.read_meta(pid)
                cfg = dict(meta.get("config") or {})
                rules = {
                    "AI_MAX_CHARS": (50, 2000),
                    "AI_MSG_MAX_CHARS": (8, 500),
                    "AI_GROUP_AUTO_PROB": (0.0, 1.0),
                    "LLM_TEMPERATURE": (0.0, 2.0),
                    "AI_MAX_MSGS": (1, 20),
                    "AI_TYPE_CPS": (0.1, 100),
                    "AI_THINK_MIN": (0.0, 60),
                    "AI_THINK_MAX": (0.0, 60),
                    "AI_GAP_MIN": (0.0, 60),
                    "AI_GAP_MAX": (0.0, 60),
                    "AI_QUOTE_PROB": (0.0, 1.0),
                    "AI_STICKER_PROB": (0.0, 1.0),
                }
                for key, raw in params.items():
                    if key not in rules:
                        raise ValueError(f"不支持快捷修改：{key}")
                    if raw is None or str(raw).strip() == "":
                        cfg.pop(key, None)
                        continue
                    val = personas._coerce(key, raw)
                    lo, hi = rules[key]
                    if not (lo <= float(val) <= hi):
                        raise ValueError(f"{key} 超出范围 {lo}~{hi}")
                    cfg[key] = val
                payload = {
                    "name": meta.get("name") or pid,
                    "desc": meta.get("desc") or "",
                    "config": cfg,
                }
                f = personas.PERSONA_DIR / pid / "config.json"
                tmp = f.with_suffix(".tmp")
                tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                tmp.replace(f)
                if pid == personas.current_id():
                    personas.apply_config(cfg)
                self._send_json(200, {"ok": True, "id": pid, "config": cfg})
                return

            if path == "/api/persona/meta":
                pid = str(data.get("pid") or "").strip()
                if not personas.exists(pid):
                    self._send_json(200, {"ok": False, "error": f"没有这个人设：{pid}"})
                    return
                meta = personas.read_meta(pid)
                name = str(data.get("name") if data.get("name") is not None else meta.get("name") or pid).strip()[:40] or pid
                desc = str(data.get("desc") if data.get("desc") is not None else meta.get("desc") or "").strip()[:200]
                raw = data.get("config", meta.get("config") or {})
                if isinstance(raw, str):
                    raw = json.loads(raw or "{}")
                if not isinstance(raw, dict):
                    raise ValueError("config 必须是 JSON 对象")
                payload = {"name": name, "desc": desc, "config": raw}
                f = personas.PERSONA_DIR / pid / "config.json"
                tmp = f.with_suffix(".tmp")
                tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                tmp.replace(f)
                if data.get("avatar"):
                    personas.save_avatar(pid, str(data.get("avatar") or ""))
                elif data.get("avatar_clear"):
                    personas.clear_avatar(pid)
                self._send_json(200, {"ok": True, "id": pid, "meta": payload, "avatar": personas.avatar_url(pid)})
                return

            if path == "/api/personas/create":
                pid = str(data.get("id") or "").strip()
                if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", pid):
                    raise ValueError("人设 id 只能用字母/数字/下划线/短横线，1~32 位")
                target = personas.PERSONA_DIR / pid
                if target.exists():
                    self._send_json(200, {"ok": False, "error": f"人设已存在：{pid}"})
                    return
                text = str(data.get("text") or "")
                if not text.strip():
                    self._send_json(200, {"ok": False, "error": "人设正文不能为空"})
                    return
                name = str(data.get("name") or pid).strip()[:40] or pid
                desc = str(data.get("desc") or "").strip()[:200]
                raw = data.get("config") or {}
                if isinstance(raw, str):
                    raw = json.loads(raw or "{}")
                if not isinstance(raw, dict):
                    raise ValueError("config 必须是 JSON 对象")
                target.mkdir(parents=True, exist_ok=False)
                (target / "persona.txt").write_text(text, encoding="utf-8")
                (target / "config.json").write_text(
                    json.dumps({"name": name, "desc": desc, "config": raw}, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                if data.get("avatar"):
                    personas.save_avatar(pid, str(data.get("avatar") or ""))
                logger.info(f"[webpanel] 新增人设 {pid}（{name}）")
                self._send_json(200, {"ok": True, "id": pid, "avatar": personas.avatar_url(pid)})
                return

            if path == "/api/personas/switch":
                ok, msg = personas.switch(str(data.get("id") or ""))
                self._send_json(200, {"ok": ok, "msg": msg})
                return

            if path == "/api/personas/delete":
                pid = str(data.get("id") or "").strip()
                switched_to = personas.delete_persona(pid)
                self._send_json(200, {"ok": True, "deleted": pid, "switched_to": switched_to})
                return

            if path == "/api/scene_persona":
                kind = str(data.get("scene") or "").strip()
                sid = data.get("id")
                pid = str(data.get("pid") or "").strip()
                if kind not in ("group", "private") or sid in (None, ""):
                    raise ValueError("scene/id 不合法")
                _kind, _sid = _scene_ref(f"{kind}:{sid}")
                personas.set_scene_persona((_kind, _sid), pid or None)
                self._send_json(200, {"ok": True, "cleared": 0, "preserved": True})
                return

            if path == "/api/clear":
                kind = str(data.get("scene") or "group").strip()
                _kind, _sid = _scene_ref(f"{kind}:{data.get('id', 0)}")
                memory.clear_scene(_kind == "group", _sid)
                self._send_json(200, {"ok": True})
                return

            if path == "/api/reset_mood":
                _kind, _sid = _scene_ref(f"group:{data.get('id', 0)}")
                memory.reset_mood(_sid)
                self._send_json(200, {"ok": True})
                return

            if path == "/api/stickers/import":
                item = _sticker_import(
                    str(data.get("name") or ""),
                    str(data.get("data") or ""),
                    data.get("tags"),
                    str(data.get("sublib") or ""),
                    data.get("sublibs"),
                )
                self._send_json(200, {"ok": True, "item": item})
                return

            if path == "/api/stickers/sublib/create":
                name = sticker_lib.create_sublib(str(data.get("name") or ""))
                self._send_json(200, {"ok": True, "name": name, "sublibs": sticker_lib.sublib_counts()})
                return

            if path == "/api/stickers/sublib/delete":
                name = str(data.get("name") or "")
                moved = sticker_lib.delete_sublib(name)
                self._send_json(200, {"ok": True, "name": name, "moved": moved, "sublibs": sticker_lib.sublib_counts()})
                return

            if path == "/api/stickers/sublib":
                raw = data.get("sublibs")
                if not isinstance(raw, list):
                    raw = [data.get("sublib") or ""]
                item = sticker_lib.set_item_sublibs(str(data.get("file") or ""), raw)
                self._send_json(200, {"ok": True, "item": item, "sublibs": sticker_lib.sublib_counts()})
                return

            if path == "/api/stickers/delete":
                removed = _sticker_delete(str(data.get("file") or ""))
                self._send_json(200, {"ok": True, "removed": removed})
                return

            if path == "/api/chatlog/open-folder":
                memory.DATA_DIR.mkdir(parents=True, exist_ok=True)
                if os.name == "nt":
                    os.startfile(str(memory.DATA_DIR))  # type: ignore[attr-defined]
                else:
                    subprocess.Popen(["xdg-open", str(memory.DATA_DIR)])
                self._send_json(200, {"ok": True})
                return

            if path == "/api/stickers/open-folder":
                _open_sticker_folder()
                self._send_json(200, {"ok": True})
                return

            if path == "/api/stickers":
                fname = str(data.get("file") or "")
                if not re.fullmatch(r"[A-Za-z0-9._-]+", fname):
                    raise ValueError("非法文件名")
                raw = data.get("tags")
                if isinstance(raw, str):
                    raw = re.split(r"[,，、;；\s]+", raw)
                tags: list[str] = []
                for t in (raw or []):
                    t = str(t).strip()
                    if t and t not in tags:
                        tags.append(t)
                tags = tags[:8]
                items = sticker_lib.load_index()
                if not any(it.get("file") == fname for it in items):
                    raise ValueError("索引里没有这个文件")
                for it in items:
                    if it.get("file") == fname:
                        it["tags"] = tags
                        break
                sticker_lib.save_index(items)
                self._send_json(200, {"ok": True, "tags": tags})
                return

            if path == "/api/phone":
                act = str(data.get("action") or "").strip()
                if act == "enable":
                    res = _phone_access_enable()
                elif act == "disable":
                    res = _phone_access_disable()
                elif act == "reset":
                    res = _phone_access_reset()
                else:
                    self._send_json(200, {"ok": False, "error": f"未知操作：{act}"})
                    return
                # 一次性密码同样只下发给本机
                if not self._client_is_local():
                    res.pop("password", None)
                res.pop("_saved_password", None)
                self._send_json(200, res)
                return
            if path == "/api/env/params":
                updates = data.get("updates")
                if not isinstance(updates, dict) or not updates:
                    raise ValueError("没有要保存的参数")
                env_updates: dict[str, str] = {}
                typed: dict[str, object] = {}
                for key, raw in updates.items():
                    item = _ENV_QUICK_INDEX.get(str(key))
                    if not item:
                        raise ValueError(f"不支持快捷修改：{key}")
                    sval, val = _coerce_env_item(item, raw)
                    env_updates[item["key"]] = sval
                    typed[item["key"]] = val
                _update_env(env_updates)
                _apply_env_hot(typed)
                logger.info(f"[webpanel] 常用参数已更新：{', '.join(env_updates)}")
                self._send_json(200, {
                    "ok": True,
                    "groups": _env_quick_payload(),
                    "model": _read_env_pairs().get("LLM_MODEL", ""),
                    "features": _current_model_features(),
                    "changed": sorted(env_updates),
                    "restart_required": False,
                })
                return

            if path == "/api/config/allowed_groups":
                groups = _normalize_ids(data.get("groups"), "群号")
                _update_env({"ALLOWED_GROUPS": ",".join(groups)})
                logger.info(f"[webpanel] 群白名单已写入：{groups or '（空 = 所有群）'}")
                self._send_json(200, {"ok": True, "groups": groups, "restart_required": True})
                return

            if path == "/api/config/admin":
                admins = _normalize_ids(data.get("admins"), "管理员 QQ")
                _update_env({"BOT_ADMIN_QQ": ",".join(admins)})
                logger.info(f"[webpanel] 管理员 QQ 已写入：{admins or '（空）'}")
                self._send_json(200, {"ok": True, "admins": admins, "restart_required": True})
                return

        except ValueError as e:
            self._send_json(400, {"ok": False, "error": str(e)})
            return
        except Exception as e:
            logger.warning(f"[webpanel] POST {path} 失败: {e}")
            self._send_json(500, {"ok": False, "error": str(e)})
            return

        self._send_json(404, {"ok": False, "error": "not found"})


def _start() -> None:
    if not ENABLED:
        logger.info("[webpanel] 已禁用（WEB_PANEL=false）")
        return
    if not (UI_DIR / "index.html").is_file():
        logger.warning(f"[webpanel] 缺少前端文件：{UI_DIR / 'index.html'}")
    if not IS_LOOPBACK_BIND and not _login_configured():
        logger.warning(
            "[webpanel] 正在对外监听但没有设置密码，远程请求会被拒绝。"
            "请设置 WEB_PANEL_PASSWORD 或 WEB_PANEL_PASSWORD_HASH。"
        )
    try:
        server = ThreadingHTTPServer((BIND_HOST, PORT), _Handler)
    except Exception as e:
        logger.warning(f"[webpanel] 启动失败（{BIND_HOST}:{PORT} 可能被占用）：{e}")
        return
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    if IS_LOOPBACK_BIND:
        logger.info(f"[webpanel] 管理控制台已启动：http://127.0.0.1:{PORT}/")
    else:
        logger.info(
            f"[webpanel] 管理控制台已对外监听：http://{BIND_HOST}:{PORT}/ "
            f"（远程需登录，账号 {REMOTE_USER}）"
        )


# 插件加载即启动（仅一次）
_start()
