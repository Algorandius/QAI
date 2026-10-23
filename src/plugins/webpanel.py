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
import json
import os
import re
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from nonebot import logger

from src import longterm, memory, personas, sticker_lib
from src.config import ROOT, env_bool, env_int
from src.stats import STATS, uptime_str

ENABLED = env_bool("WEB_PANEL", True)
PORT = max(1, env_int("WEB_PANEL_PORT", 8081))
UI_DIR = ROOT / "webpanel_ui"
ENV_FILE = ROOT / ".env"
MAX_BODY = 12 * 1024 * 1024

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
        for key, attr in (("AI_VISION", "AI_VISION"), ("AI_POKE", "AI_POKE"),
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
    def log_message(self, *a):
        pass

    # ---- 响应 ----
    def _send_json(self, code: int, obj) -> None:
        body = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, code: int, body: bytes, ctype: str, cache: str = "no-cache") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
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
        self._send_bytes(200, body, ctype)
        return True

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
        qs = parse_qs(u.query, keep_blank_values=True)

        if path == "/stickers":
            self._redirect("/#stickers")
            return
        if path in _STATIC_FILES:
            if not self._serve_static(path):
                self._send_json(404, {"ok": False, "error": "UI 文件缺失，请检查 webpanel_ui/ 目录"})
            return

        try:
            if path == "/api/status":
                self._send_json(200, _status_payload())
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
    def do_POST(self):
        u = urlparse(self.path)
        path = u.path
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
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), _Handler)
    except Exception as e:
        logger.warning(f"[webpanel] 启动失败（端口 {PORT} 可能被占用）：{e}")
        return
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    logger.info(f"[webpanel] 管理控制台已启动：http://127.0.0.1:{PORT}/")


# 插件加载即启动（仅一次）
_start()
