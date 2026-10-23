"""多套人设管理：人设正文与角色专属参数。

目录结构：
    personas/
      huiliyi/
        persona.txt     人设正文
        config.json     {"name": "上杉绘梨衣", "desc": "...", "config": {参数: 值}}
      dayuyu/
        persona.txt
        config.json

参数以 .env 为默认值，config.json 中出现的键覆盖默认值。
切换场景人设时载入该角色自己的对话记忆，切回可恢复。
"""
from __future__ import annotations

import base64
import json
import re
import shutil
from pathlib import Path
from typing import Any

from nonebot import logger

from src.config import ROOT, env
from src.model_catalog import PROVIDER_KEY_ENV, base_for

PERSONA_DIR = ROOT / "personas"
STATE_FILE = ROOT / "data" / "current_persona.json"
SCENE_FILE = ROOT / "data" / "scene_persona.json"      # 群/私聊 绑定的独立人设
DEFAULT_ID = "huiliyi"

# 人设配置键 -> ai_chat 模块里的变量名（多数同名，只有 MAX_CHARS 不一样）
VAR_MAP: dict[str, str] = {
    "AI_MAX_CHARS": "MAX_CHARS",
    "AI_MSG_MAX_CHARS": "AI_MSG_MAX_CHARS",
    "AI_MAX_MSGS": "AI_MAX_MSGS",
    "AI_COOLDOWN_SEC": "COOLDOWN",
    "AI_THINK_MIN": "THINK_MIN",
    "AI_THINK_MAX": "THINK_MAX",
    "AI_TYPE_CPS": "TYPE_CPS",
    "AI_GAP_MIN": "GAP_MIN",
    "AI_GAP_MAX": "GAP_MAX",
    "AI_QUOTE_PROB": "AI_QUOTE_PROB",
    "AI_STICKER_PROB": "AI_STICKER_PROB",
    "AI_STICKER_SUBLIB": "AI_STICKER_SUBLIB",
    "AI_STICKER_COOLDOWN": "AI_STICKER_COOLDOWN",
    "AI_CUSTOM_STICKER_PROB": "AI_CUSTOM_STICKER_PROB",
    "AI_CUSTOM_STICKER_COOLDOWN": "AI_CUSTOM_STICKER_COOLDOWN",
    "AI_AUTO_STICKER_PROB": "AI_AUTO_STICKER_PROB",
    "AI_AUTO_STICKER_COOLDOWN": "AI_AUTO_STICKER_COOLDOWN",
    "AI_IMAGE_TRIGGER_PROB": "AI_IMAGE_TRIGGER_PROB",
    "AI_GROUP_AUTO_PROB": "AI_GROUP_AUTO_PROB",
    "AI_GROUP_AUTO_COOLDOWN": "AI_GROUP_AUTO_COOLDOWN",
    "AI_NAME_ALIASES": "AI_NAME_ALIASES",
    "LLM_TEMPERATURE": "LLM_TEMPERATURE",
}

INT_KEYS = {
    "AI_MAX_CHARS", "AI_MSG_MAX_CHARS", "AI_MAX_MSGS", "AI_COOLDOWN_SEC",
    "AI_STICKER_COOLDOWN", "AI_CUSTOM_STICKER_COOLDOWN",
    "AI_AUTO_STICKER_COOLDOWN", "AI_GROUP_AUTO_COOLDOWN",
}
FLOAT_KEYS = {
    "AI_THINK_MIN", "AI_THINK_MAX", "AI_TYPE_CPS", "AI_GAP_MIN", "AI_GAP_MAX",
    "AI_QUOTE_PROB", "AI_STICKER_PROB", "AI_CUSTOM_STICKER_PROB",
    "AI_AUTO_STICKER_PROB", "AI_IMAGE_TRIGGER_PROB", "AI_GROUP_AUTO_PROB",
    "LLM_TEMPERATURE",
}
PROB_KEYS = {
    "AI_QUOTE_PROB", "AI_STICKER_PROB", "AI_CUSTOM_STICKER_PROB",
    "AI_AUTO_STICKER_PROB", "AI_IMAGE_TRIGGER_PROB", "AI_GROUP_AUTO_PROB",
}
SET_KEYS = {"AI_NAME_ALIASES"}
AVATAR_NAMES = ("avatar.png", "avatar.jpg", "avatar.jpeg", "avatar.webp", "avatar.gif")


def avatar_path(pid: str) -> Path | None:
    for name in AVATAR_NAMES:
        p = _dir(pid) / name
        if p.is_file():
            return p
    return None


def avatar_url(pid: str) -> str:
    p = avatar_path(pid)
    if not p:
        return ""
    try:
        v = int(p.stat().st_mtime)
    except Exception:
        v = 0
    return f"/persona-avatar/{pid}?v={v}"


def save_avatar(pid: str, data_url: str) -> None:
    m = re.match(r"^data:image/(gif|png|jpe?g|webp);base64,(.+)$", str(data_url or ""), re.S | re.I)
    if not m:
        raise ValueError("头像只支持 GIF/PNG/JPG/WebP")
    ext = m.group(1).lower().replace("jpeg", "jpg")
    raw = base64.b64decode(m.group(2), validate=True)
    if not raw:
        raise ValueError("头像为空")
    if len(raw) > 5 * 1024 * 1024:
        raise ValueError("头像不能超过 5MB")
    target = _dir(pid)
    target.mkdir(parents=True, exist_ok=True)
    for name in AVATAR_NAMES:
        try:
            (target / name).unlink()
        except FileNotFoundError:
            pass
    (target / f"avatar.{ext}").write_bytes(raw)


def clear_avatar(pid: str) -> None:
    for name in AVATAR_NAMES:
        try:
            (_dir(pid) / name).unlink()
        except FileNotFoundError:
            pass



# ---------------------------------------------------------------- 读取
def _dir(pid: str) -> Path:
    return PERSONA_DIR / pid


def exists(pid: str) -> bool:
    return bool(pid) and (_dir(pid) / "persona.txt").is_file()


def read_meta(pid: str) -> dict[str, Any]:
    """读 config.json；坏文件/缺文件都返回空 dict，不抛异常。"""
    f = _dir(pid) / "config.json"
    if not f.is_file():
        return {}
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning(f"[persona] 读 {pid}/config.json 失败: {e}")
        return {}


def list_personas() -> list[dict[str, Any]]:
    """列出所有人设：[{id, name, desc, config}]"""
    out: list[dict[str, Any]] = []
    if not PERSONA_DIR.is_dir():
        return out
    for d in sorted(PERSONA_DIR.iterdir()):
        if not d.is_dir() or not (d / "persona.txt").is_file():
            continue
        meta = read_meta(d.name)
        out.append({
            "id": d.name,
            "name": meta.get("name") or d.name,
            "desc": meta.get("desc") or "",
            "config": meta.get("config") or {},
            "avatar": avatar_url(d.name),
        })
    return out


def current_id() -> str:
    """当前人设 id；文件丢了/目录不存在就回退默认。"""
    pid = ""
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            pid = str(data.get("id") or "")
    except Exception:
        pass
    if exists(pid):
        return pid
    return DEFAULT_ID if exists(DEFAULT_ID) else (list_personas()[0]["id"] if list_personas() else "")


def text_of(pid: str | None = None) -> str:
    pid = pid or current_id()
    f = _dir(pid) / "persona.txt"
    try:
        return f.read_text(encoding="utf-8").strip()
    except Exception as e:
        logger.warning(f"[persona] 读取 {pid}/persona.txt 失败: {e}")
        return ""


def display_name(pid: str | None = None) -> str:
    pid = pid or current_id()
    return read_meta(pid).get("name") or pid


# ---------------------------------------------------------------- 按场景绑定人设
def scene_key(key: Any) -> str:
    """把 ("group", 123) / ("private", 456) 统一成 "group:123"。"""
    if isinstance(key, (tuple, list)) and len(key) == 2:
        return f"{key[0]}:{key[1]}"
    return str(key or "")


def load_scene_map() -> dict[str, str]:
    try:
        data = json.loads(SCENE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    # 人设目录被删了就当没绑过
    return {str(k): str(v) for k, v in data.items() if exists(str(v))}


def save_scene_map(m: dict[str, str]) -> None:
    try:
        SCENE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = SCENE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(SCENE_FILE)
    except Exception as e:
        logger.warning(f"[persona] 保存群里人设失败: {e}")


def scene_persona(key: Any) -> str | None:
    """这个群/私聊单独绑的人设；没绑返回 None（跟随全局）。"""
    pid = load_scene_map().get(scene_key(key))
    return pid if pid and exists(pid) else None


def set_scene_persona(key: Any, pid: str | None) -> None:
    """给某个群绑人设；pid 留空 = 解除绑定。切换时载入该角色自己的对话记忆。"""
    m = load_scene_map()
    k = scene_key(key)
    old_pid = scene_persona(k) or current_id()
    new_pid = pid if pid and exists(pid) else current_id()
    if old_pid != new_pid:
        try:
            from src import memory
            kind, raw_id = k.split(":", 1)
            memory.switch_persona_ctx(kind == "group", int(raw_id), old_pid, new_pid)
            memory.save_state()
        except Exception as e:
            logger.warning(f"[persona] 切换场景记忆失败: {e}")
    if pid and exists(pid):
        m[k] = pid
    else:
        m.pop(k, None)
    save_scene_map(m)


def resolve(key: Any = None) -> str:
    """这个场景该用谁：群里单独绑的 > 全局选中的 > 默认。"""
    return scene_persona(key) or current_id()


def _norm(cfg: dict) -> dict:
    out: dict = {}
    for k, v in (cfg or {}).items():
        if k not in VAR_MAP:
            continue
        try:
            out[k] = _coerce(k, v)
        except (TypeError, ValueError):
            continue
    return out


def model_for(key: Any = None) -> dict[str, str]:
    """这个场景实际使用的模型；人设没选时继承 .env 默认模型。"""
    pid = resolve(key)
    cfg = read_meta(pid).get("config") or {}
    provider = str(cfg.get("LLM_PROVIDER") or "").strip()
    model = str(cfg.get("LLM_MODEL") or "").strip()
    base = str(cfg.get("LLM_BASE_URL") or "").strip().rstrip("/")
    source = "persona" if (provider or model) else "global"

    global_provider = str(env("LLM_PROVIDER", "") or "").strip()
    global_model = str(env("LLM_MODEL", "") or "").strip()
    global_base = str(env("LLM_BASE_URL", "") or "").strip().rstrip("/")
    if not provider:
        provider = global_provider
    if not model:
        model = global_model
    if not base:
        base = base_for(provider, model) or global_base
    if not provider:
        probe = (base + " " + model).lower()
        if "deepseek" in probe:
            provider = "deepseek"
        elif "bigmodel" in probe:
            provider = "zhipu"
        elif "dashscope" in probe:
            provider = "qwen"
        elif "moonshot" in probe:
            provider = "kimi"
        elif "tencent" in probe:
            provider = "hunyuan"

    api_key = str(env(PROVIDER_KEY_ENV.get(provider, ""), "") or "").strip()
    same_global = (
        provider == global_provider
        or (not global_provider and (provider in (global_base + " " + global_model).lower() or base == global_base))
    )
    if not api_key and same_global:
        api_key = str(env("LLM_API_KEY", "") or "").strip()
    if not api_key and provider == "hunyuan":
        api_key = str(env("LLM_KEY_TENCENT", "") or env("LLM_KEY_TENCENT_INTL", "") or "").strip()

    return {
        "provider": provider,
        "model": model,
        "base_url": base,
        "api_key": api_key,
        "source": source,
        "persona": pid,
    }


def params_for(key: Any = None, module=None) -> dict:
    """这个场景最终生效的参数（.env 默认 → 全局人设 → 该群单独人设，后者覆盖前者）。"""
    if module is None:
        from src.plugins import ai_chat as module
    out = dict(getattr(module, "PARAM_DEFAULTS", {}) or {})
    # 只用该场景实际角色的配置，不把全局角色的参数混进其他角色
    spid = scene_persona(key) if key else None
    eff_pid = spid or current_id()
    eff_cfg = read_meta(eff_pid).get("config") or {}
    out.update(_norm(eff_cfg))
    # 表情子库按这个场景实际使用的角色解析，不跨角色继承
    out["AI_STICKER_SUBLIB"] = str(eff_cfg.get("AI_STICKER_SUBLIB") or "").strip()
    # 组合约束（和人设文件写错时的兜底一致）
    if out.get("AI_GAP_MAX", 0) < out.get("AI_GAP_MIN", 0):
        out["AI_GAP_MAX"] = out["AI_GAP_MIN"] + 1
    if out.get("AI_THINK_MAX", 0) < out.get("AI_THINK_MIN", 0):
        out["AI_THINK_MAX"] = out["AI_THINK_MIN"] + 1
    return out


# ---------------------------------------------------------------- 应用参数
def _coerce(key: str, raw: Any) -> Any:
    if key in SET_KEYS:
        if isinstance(raw, (list, tuple, set)):
            return {str(x).strip() for x in raw if str(x).strip()}
        return {x.strip() for x in str(raw).split(",") if x.strip()}
    if key in INT_KEYS:
        return max(0, int(raw))
    if key in FLOAT_KEYS:
        v = float(raw)
        if key in PROB_KEYS:
            v = max(0.0, min(1.0, v))
        return v
    return raw


def apply_config(cfg: dict[str, Any], module=None) -> list[str]:
    """把人设的参数写进 ai_chat 的全局变量，返回实际生效的键名列表。"""
    if module is None:
        from src.plugins import ai_chat as module  # 延迟导入，避免循环依赖

    applied: list[str] = []
    for key, raw in (cfg or {}).items():
        var = VAR_MAP.get(key)
        if not var or not hasattr(module, var):
            if var is None:
                logger.debug(f"[persona] 忽略未知配置项 {key}")
            continue
        try:
            setattr(module, var, _coerce(key, raw))
            applied.append(key)
        except (TypeError, ValueError) as e:
            logger.warning(f"[persona] 配置项 {key}={raw!r} 无效，跳过: {e}")

    # 组合约束：条间停顿上限不能小于下限；打字速度不能为 0
    gap_min = getattr(module, "GAP_MIN", 0.0)
    if getattr(module, "GAP_MAX", 0.0) < gap_min:
        setattr(module, "GAP_MAX", gap_min + 1.0)
    if getattr(module, "THINK_MAX", 0.0) < getattr(module, "THINK_MIN", 0.0):
        setattr(module, "THINK_MAX", getattr(module, "THINK_MIN", 0.0) + 1.0)
    if getattr(module, "TYPE_CPS", 0) <= 0:
        setattr(module, "TYPE_CPS", 8.0)
    return applied


def apply_current(module=None) -> str:
    """启动时调用：把当前人设的参数覆盖上去。返回人设 id。"""
    pid = current_id()
    if not pid:
        return ""
    applied = apply_config(read_meta(pid).get("config") or {}, module)
    if applied:
        logger.info(f"[persona] 当前人设「{display_name(pid)}」，已应用 {len(applied)} 项参数")
    return pid


# ---------------------------------------------------------------- 切换
def save_current(pid: str) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(
            json.dumps({"id": pid}, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    except Exception as e:
        logger.warning(f"[persona] 保存当前人设失败: {e}")


def switch(pid: str, module=None) -> tuple[bool, str]:
    """切换人设：参数立即生效 + 记住选择；对话记忆保留。

    返回 (是否成功, 给用户看的说明)。
    """
    if not exists(pid):
        names = "、".join(x["id"] for x in list_personas()) or "（没有人设）"
        return False, f"没有这个人设：{pid}\n可用：{names}"

    old_current = current_id()
    try:
        from src import memory
        for scene in memory.list_scenes():
            kind = str(scene.get("scene") or "")
            sid = int(scene.get("id") or 0)
            if kind not in ("group", "private") or not sid:
                continue
            override = scene_persona((kind, sid))
            old_pid = override or old_current
            new_pid = override or pid
            if old_pid != new_pid:
                memory.switch_persona_ctx(kind == "group", sid, old_pid, new_pid)
        memory.save_state()
    except Exception as e:
        logger.warning(f"[persona] 切换角色记忆失败: {e}")

    save_current(pid)
    applied = apply_config(read_meta(pid).get("config") or {}, module)

    name = display_name(pid)
    return True, (
        f"已切换到「{name}」\n"
        f"参数已生效 {len(applied)} 项（不用重启）\n"
        f"已载入该角色自己的对话记忆；切回原角色可恢复"
    )


def delete_persona(pid: str, module=None) -> str | None:
    """删除人设目录、头像和场景绑定；返回自动切换到的新人设 id。"""
    pid = str(pid or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", pid):
        raise ValueError("人设 id 不合法")
    if not exists(pid):
        raise ValueError(f"没有这个人设：{pid}")
    items = list_personas()
    if len(items) <= 1:
        raise ValueError("至少保留一套人设，不能删除最后一套")

    switched_to = None
    if pid == current_id():
        target = next((p["id"] for p in items if p["id"] != pid), "")
        if not target:
            raise ValueError("没有可切换的其他人设")
        ok, _msg = switch(target, module)
        if not ok:
            raise ValueError("删除前自动切换人设失败")
        switched_to = target

    scene_map = load_scene_map()
    try:
        from src import memory
        global_pid = current_id()
        for key, bound in scene_map.items():
            if bound != pid:
                continue
            try:
                kind, raw_id = key.split(":", 1)
                memory.switch_persona_ctx(kind == "group", int(raw_id), pid, global_pid)
            except Exception:
                pass
        for store in (memory.PERSONA_CTX, memory.PERSONA_SUMMARY, memory.PERSONA_MOOD):
            for key in [k for k in store if k.endswith(f"|{pid}")]:
                store.pop(key, None)
        memory.save_state()
    except Exception as e:
        logger.warning(f"[persona] 清理删除人设的记忆失败: {e}")

    kept = {k: v for k, v in scene_map.items() if v != pid}
    if len(kept) != len(scene_map):
        save_scene_map(kept)

    shutil.rmtree(_dir(pid), ignore_errors=False)
    logger.info(f"[persona] 已删除人设 {pid}" + (f"，自动切换到 {switched_to}" if switched_to else ""))
    return switched_to
