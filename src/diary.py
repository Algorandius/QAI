"""机器人日记：每天按「人设 × 场景」写一篇，口吻跟随人设。

为什么按人设分：
同一个人在 A 群扮演一个角色、在 B 群扮演另一个，两边日记混在一起会精神分裂。
所以 key 是 `group:123|persona_id`，跟 memory 的人设隔离方式保持一致。

存储：`data/diary.json`
```json
{
  "version": 1,
  "entries": {
    "group:123456|persona_a": [
      {"date": "2026-10-30", "ts": 1730..., "text": "今天……",
       "persona": "persona_a", "persona_name": "示例角色",
       "scene": "group", "scene_id": 123456,
       "mood": 2.5, "turns": 87, "people": ["小王", "阿杰"]}
    ]
  }
}
```

设计：
- 一天一篇，同一天重写只更新内容（不叠加）
- 每篇都能单独删除
- 读 `.tmp` 再 rename，避免写坏
- 按场景 + 人设隔离，绝不串味
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any

from nonebot import logger

from src.config import ROOT, env_int

DATA_FILE = ROOT / "data" / "diary.json"
MAX_ENTRIES = max(30, env_int("DIARY_MAX_ENTRIES", 400))
MAX_TEXT = 1200

_LOCK = threading.RLock()
_DATA: dict[str, Any] | None = None


def _empty() -> dict[str, Any]:
    return {"version": 1, "entries": {}}


def _load() -> dict[str, Any]:
    global _DATA
    if _DATA is not None:
        return _DATA
    data = _empty()
    try:
        if DATA_FILE.exists():
            raw = json.loads(DATA_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("entries"), dict):
                data = raw
    except Exception as e:
        logger.warning(f"[diary] 读取失败，按空处理: {e}")
    _DATA = data
    return data


def _save() -> None:
    data = _load()
    try:
        DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = DATA_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(DATA_FILE)
    except Exception as e:
        logger.warning(f"[diary] 保存失败: {e}")


def reload() -> None:
    global _DATA
    with _LOCK:
        _DATA = None


# ---------------------------------------------------------------- key
def diary_key(is_group: bool, scene_id: int, persona_id: str) -> str:
    """一篇日记的唯一身份：场景 + 人设。"""
    scene = f"{'group' if is_group else 'private'}:{int(scene_id)}"
    pid = str(persona_id or "").strip() or "default"
    return f"{scene}|{pid}"


def split_key(key: str) -> tuple[str, int, str]:
    """把 key 拆回 (scene, scene_id, persona_id)，坏数据返回空值。"""
    try:
        scene_part, pid = str(key).split("|", 1)
        scene, sid = scene_part.split(":", 1)
        return scene, int(sid), pid
    except Exception:
        return "", 0, ""


def today() -> str:
    return time.strftime("%Y-%m-%d")


# ---------------------------------------------------------------- 写
def write(
    is_group: bool,
    scene_id: int,
    persona_id: str,
    text: str,
    *,
    persona_name: str = "",
    mood: float = 0.0,
    turns: int = 0,
    people: list[str] | None = None,
    date: str = "",
) -> dict:
    """写入/覆盖某一天某个人设的日记。同一天只保留最新一版。"""
    body = str(text or "").strip()
    if not body:
        raise ValueError("日记内容不能为空")
    if len(body) > MAX_TEXT:
        body = body[:MAX_TEXT].rstrip() + "…"
    day = str(date or "").strip() or today()
    key = diary_key(is_group, scene_id, persona_id)

    entry = {
        "date": day,
        "ts": time.time(),
        "text": body,
        "persona": str(persona_id or "").strip(),
        "persona_name": str(persona_name or "").strip(),
        "scene": "group" if is_group else "private",
        "scene_id": int(scene_id),
        "mood": round(float(mood or 0.0), 2),
        "turns": int(turns or 0),
        "people": [str(x)[:24] for x in (people or [])][:12],
    }

    with _LOCK:
        data = _load()
        items = data["entries"].setdefault(key, [])
        replaced = False
        for i, it in enumerate(items):
            if it.get("date") == day:
                items[i] = entry
                replaced = True
                break
        if not replaced:
            items.append(entry)
        # 只留最近的 N 篇
        items.sort(key=lambda x: x.get("date") or "")
        if len(items) > MAX_ENTRIES:
            data["entries"][key] = items[-MAX_ENTRIES:]
        _save()
    return entry


# ---------------------------------------------------------------- 读
def has_today(is_group: bool, scene_id: int, persona_id: str) -> bool:
    return any(e.get("date") == today() for e in list_entries(is_group, scene_id, persona_id, limit=1))


def list_entries(is_group: bool, scene_id: int, persona_id: str, limit: int = 0) -> list[dict]:
    """某人设在某场景的日记，按日期倒序（最新在前）。"""
    key = diary_key(is_group, scene_id, persona_id)
    with _LOCK:
        items = list(_load()["entries"].get(key) or [])
    items.sort(key=lambda x: x.get("date") or "", reverse=True)
    return items[:limit] if limit > 0 else items


def get_entry(is_group: bool, scene_id: int, persona_id: str, date: str) -> dict | None:
    for e in list_entries(is_group, scene_id, persona_id):
        if e.get("date") == date:
            return e
    return None


def delete(is_group: bool, scene_id: int, persona_id: str, date: str = "") -> int:
    """删一篇（给日期）或删某人设在该场景的全部（不给日期）。"""
    key = diary_key(is_group, scene_id, persona_id)
    with _LOCK:
        data = _load()
        items = data["entries"].get(key)
        if not items:
            return 0
        if not date:
            n = len(items)
            data["entries"].pop(key, None)
            _save()
            return n
        keep = [it for it in items if it.get("date") != date]
        n = len(items) - len(keep)
        if n:
            if keep:
                data["entries"][key] = keep
            else:
                data["entries"].pop(key, None)
            _save()
        return n


def all_keys() -> list[dict]:
    """所有有日记的 (场景, 人设) 组合，供面板列目录。"""
    with _LOCK:
        pairs = list(_load()["entries"].items())
    out = []
    for key, items in pairs:
        scene, sid, pid = split_key(key)
        if not scene or not items:
            continue
        latest = max(items, key=lambda x: x.get("date") or "")
        out.append({
            "key": key,
            "scene": scene,
            "scene_id": sid,
            "persona": pid,
            "persona_name": latest.get("persona_name") or "",
            "count": len(items),
            "latest_date": latest.get("date") or "",
            "latest_text": (latest.get("text") or "")[:80],
        })
    out.sort(key=lambda x: x["latest_date"], reverse=True)
    return out


def stats() -> dict:
    keys = all_keys()
    total = sum(k["count"] for k in keys)
    return {"entries": total, "scenes": len(keys), "path": str(DATA_FILE)}
