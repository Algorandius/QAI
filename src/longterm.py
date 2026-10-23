"""长期记忆：模型主动记下的、值得跨天保留的人和事。

和另外三份数据的分工：
- `data/state.json`     最近 N 轮原文 + 摘要（用来接上“刚才聊到哪”）
- `data/longterm.json`  模型主动挑出来的长期事实（偏好 / 约定 / 重要经历）
- `data/chatlog.jsonl`  所有对话的原始流水账（只读日志，不注入 prompt）

为什么要单独存：`state.json` 会按 `AI_HISTORY_TURNS` 滚动淘汰，摘要也会被重写；
“记住某人说过什么”不能依赖它们。

存储结构（可直接用文本编辑器打开）：
```json
{
  "version": 1,
  "scenes": {
    "group:1015171785": {
      "next_id": 4,
      "items": [
        {"id": 3, "ts": 1730..., "date": "2026-10-22",
         "who": "小王", "uid": 123456, "text": "小王下周要考试，最近在复习",
         "tags": ["小王", "考试"]}
      ]
    }
  }
}
```

设计原则：
- 按场景（群 / 私聊）隔离，绝不跨群串。
- 每个场景最多 `AI_LONGTERM_MAX` 条，超出淘汰最旧。
- 同一条文本重复 append 只更新时间，不重复占位。
- 写入先写 `.tmp` 再 rename，避免写一半损坏。
"""
from __future__ import annotations

import json
import re
import threading
import time
from typing import Any

from nonebot import logger

from src.config import ROOT, env_int

DATA_FILE = ROOT / "data" / "longterm.json"
MAX_ITEMS = max(10, env_int("AI_LONGTERM_MAX", 200))
MAX_TEXT = 240

_LOCK = threading.RLock()
_DATA: dict[str, Any] | None = None

_TAG_SPLIT = re.compile(r"[,，、;；\s]+")
_QUERY_SPLIT = re.compile(r"[\s,，、;；。.!?！？:：/\\|()（）\[\]【】\"'“”]+")


# ---------------------------------------------------------------- 读写
def _empty() -> dict[str, Any]:
    return {"version": 1, "scenes": {}}


def _today() -> str:
    return time.strftime("%Y-%m-%d")


def _norm_tags(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = _TAG_SPLIT.split(raw)
    out: list[str] = []
    for t in raw if isinstance(raw, (list, tuple, set)) else []:
        t = str(t).strip()
        if t and t not in out:
            out.append(t[:20])
    return out[:5]


def _norm_item(it: dict) -> dict:
    tags = _norm_tags(it.get("tags"))
    try:
        uid = int(it.get("uid") or 0)
    except (TypeError, ValueError):
        uid = 0
    try:
        ts = float(it.get("ts") or 0.0)
    except (TypeError, ValueError):
        ts = 0.0
    text = str(it.get("text") or "").strip()
    return {
        "id": int(it.get("id") or 0),
        "ts": ts or time.time(),
        "date": str(it.get("date") or _today())[:10],
        "who": str(it.get("who") or "").strip()[:32],
        "uid": uid,
        "text": text[:MAX_TEXT],
        "tags": tags,
    }


def _load() -> dict[str, Any]:
    """加载一次后缓存；文件损坏就按空库处理，绝不抛异常。"""
    global _DATA
    if _DATA is not None:
        return _DATA
    if not DATA_FILE.exists():
        _DATA = _empty()
        return _DATA
    try:
        raw = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"[longterm] 读取失败，按空库处理: {e}")
        _DATA = _empty()
        return _DATA
    if not isinstance(raw, dict) or not isinstance(raw.get("scenes"), dict):
        _DATA = _empty()
        return _DATA

    data = _empty()
    for key, val in raw["scenes"].items():
        if not isinstance(val, dict):
            continue
        items: list[dict] = []
        for it in val.get("items") or []:
            if isinstance(it, dict) and str(it.get("text") or "").strip():
                items.append(_norm_item(it))
        max_id = max((x["id"] for x in items), default=0)
        try:
            next_id = int(val.get("next_id") or 1)
        except (TypeError, ValueError):
            next_id = 1
        data["scenes"][str(key)] = {
            "next_id": max(1, next_id, max_id + 1),
            "items": items,
        }
    _DATA = data
    return _DATA


def _save() -> None:
    data = _load()
    try:
        DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = DATA_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(DATA_FILE)
    except Exception as e:
        logger.warning(f"[longterm] 保存失败: {e}")


def reload() -> None:
    """丢掉内存缓存、重新读盘（测试或手工改文件后用）。"""
    global _DATA
    with _LOCK:
        _DATA = None
        _load()


def _scene(data: dict, key: str) -> dict:
    key = str(key or "")
    sc = data["scenes"].get(key)
    if sc is None:
        sc = {"next_id": 1, "items": []}
        data["scenes"][key] = sc
    return sc


# ---------------------------------------------------------------- 场景 key
def scene_key(is_group: bool, scene_id: int) -> str:
    return f"{'group' if is_group else 'private'}:{scene_id}"


# ---------------------------------------------------------------- 增 / 查 / 删
def append(scene: str, text: str, tags: Any = None, who: str = "", uid: int = 0) -> dict:
    """记一条长期记忆。同文本重复记只更新时间。返回写入后的条目。"""
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if not text:
        raise ValueError("记忆内容不能为空")
    if len(text) > MAX_TEXT:
        text = text[:MAX_TEXT].rstrip() + "…"
    tags_n = _norm_tags(tags)
    who = str(who or "").strip()[:32]
    try:
        uid = int(uid or 0)
    except (TypeError, ValueError):
        uid = 0

    with _LOCK:
        data = _load()
        sc = _scene(data, scene)
        for idx, it in enumerate(sc["items"]):
            if it["text"] == text:
                it["ts"] = time.time()
                it["date"] = _today()
                if who:
                    it["who"] = who
                if uid:
                    it["uid"] = uid
                if tags_n:
                    it["tags"] = tags_n
                # 重新提及时挪到队尾，避免被“淘汰最旧”优先清掉
                sc["items"].pop(idx)
                sc["items"].append(it)
                _save()
                return dict(it)

        ent = {
            "id": int(sc["next_id"]),
            "ts": time.time(),
            "date": _today(),
            "who": who,
            "uid": uid,
            "text": text,
            "tags": tags_n,
        }
        sc["next_id"] = int(sc["next_id"]) + 1
        sc["items"].append(ent)
        if len(sc["items"]) > MAX_ITEMS:
            del sc["items"][: len(sc["items"]) - MAX_ITEMS]
        _save()
        logger.info(f"[longterm] {scene} 记住 #{ent['id']}：{text[:60]}")
        return dict(ent)


def _parse_id(raw: Any) -> int | None:
    m = re.search(r"\d+", str(raw or ""))
    return int(m.group()) if m else None


def _newest(scene: str, limit: int = 20) -> list[dict]:
    sc = _load()["scenes"].get(str(scene or ""))
    if not sc:
        return []
    items = list(sc["items"])
    items.sort(key=lambda x: float(x.get("ts") or 0), reverse=True)
    return [dict(x) for x in items[: max(1, int(limit))]]


def list_scene(scene: str, limit: int = 20) -> list[dict]:
    with _LOCK:
        return _newest(scene, limit)


def count(scene: str) -> int:
    with _LOCK:
        sc = _load()["scenes"].get(str(scene or ""))
        return len(sc["items"]) if sc else 0


def get(scene: str, mem_id: Any) -> dict | None:
    i = _parse_id(mem_id)
    if i is None:
        return None
    with _LOCK:
        sc = _load()["scenes"].get(str(scene or ""))
        if not sc:
            return None
        for it in sc["items"]:
            if int(it.get("id") or 0) == i:
                return dict(it)
    return None


def remove(scene: str, mem_id: Any) -> dict | None:
    """按编号删除；返回被删的条目（没找到返回 None）。"""
    i = _parse_id(mem_id)
    if i is None:
        return None
    with _LOCK:
        data = _load()
        sc = data["scenes"].get(str(scene or ""))
        if not sc:
            return None
        for idx, it in enumerate(sc["items"]):
            if int(it.get("id") or 0) == i:
                sc["items"].pop(idx)
                _save()
                logger.info(f"[longterm] {scene} 忘掉 #{i}")
                return dict(it)
    return None


def clear_scene(scene: str) -> int:
    """清空某个场景的长期记忆，返回清掉的条数。"""
    with _LOCK:
        data = _load()
        sc = data["scenes"].pop(str(scene or ""), None)
        n = len(sc["items"]) if sc else 0
        _save()
        return n


def prune(ttl_days: int = 0, now: float | None = None, save: bool = True) -> int:
    """删掉超过 ttl_days 的长期记忆；0 = 永久保留。返回删除条数。

    `save=False` 只改内存、不落盘，给测试用。
    """
    if not ttl_days or ttl_days <= 0:
        return 0
    cutoff = (time.time() if now is None else float(now)) - ttl_days * 86400
    removed = 0
    with _LOCK:
        data = _load()
        for key in list(data["scenes"].keys()):
            sc = data["scenes"][key]
            keep = [it for it in sc["items"] if float(it.get("ts") or 0) >= cutoff]
            if len(keep) != len(sc["items"]):
                removed += len(sc["items"]) - len(keep)
                sc["items"] = keep
            if not sc["items"]:
                data["scenes"].pop(key, None)
        if removed and save:
            _save()
    if removed:
        logger.info(f"[longterm] TTL 清理：删掉 {removed} 条（保留 {ttl_days} 天）")
    return removed


# ---------------------------------------------------------------- 检索 / 注入 prompt
def _haystack(it: dict) -> str:
    return f"{it.get('text', '')} {' '.join(it.get('tags') or [])} {it.get('who', '')}".casefold()


def query(scene: str, q: str = "", limit: int = 8) -> list[dict]:
    """按关键词检索；q 为空时返回最近的。中文按“子串 + 二元组”兜底，不依赖分词库。"""
    try:
        limit = max(1, min(int(limit or 8), 30))
    except (TypeError, ValueError):
        limit = 8
    with _LOCK:
        sc = _load()["scenes"].get(str(scene or ""))
        items = [dict(x) for x in (sc["items"] if sc else [])]
    items.sort(key=lambda x: float(x.get("ts") or 0), reverse=True)

    q = str(q or "").strip()
    if not q:
        return items[:limit]

    ql = q.casefold()
    tokens = [t for t in _QUERY_SPLIT.split(ql) if len(t) >= 2]
    bigrams = {ql[i : i + 2] for i in range(len(ql) - 1)} if len(ql) >= 2 else set()

    scored: list[tuple[int, dict]] = []
    for it in items:
        hay = _haystack(it)
        score = 0
        if ql and ql in hay:
            score += 10
        for t in tokens:
            if t in hay:
                score += 3
        if bigrams:
            score += sum(1 for b in bigrams if b in hay)
        if score > 0:
            scored.append((score, it))
    scored.sort(key=lambda x: (-x[0], -float(x[1].get("ts") or 0)))
    return [dict(it) for _, it in scored[:limit]]


def _short_date(d: str) -> str:
    d = str(d or "")
    return d[5:] if len(d) >= 10 else d


def format_entries(items: list[dict], with_id: bool = True) -> list[str]:
    """给命令 / 工具结果用的可读行。"""
    out = []
    for it in items:
        who = (it.get("who") or "").strip()
        prefix = f"[#{it.get('id')}] " if with_id else "- "
        date = _short_date(it.get("date") or "")
        out.append(
            f"{prefix}{date + ' ' if date else ''}{who + '：' if who else ''}{it.get('text', '')}"
        )
    return out


def prompt_block(scene: str, limit: int = 5) -> str:
    """把最近的长期记忆拼成一小段 system 提示；没有就返回空串。"""
    if not scene or limit <= 0:
        return ""
    with _LOCK:
        items = _newest(scene, max(1, int(limit)))
    if not items:
        return ""
    items.sort(key=lambda x: float(x.get("ts") or 0))  # 旧的在上、新的在下
    lines = [f"- {_short_date(it.get('date') or '')} {it.get('who') + '：' if it.get('who') else ''}{it.get('text', '')}" for it in items]
    return (
        "\n【你记得的事（长期记忆，越靠下越新）】\n"
        + "\n".join(lines)
        + "\n（这些是你以前记下的，不是这一轮刚发生的；相关才提，不要主动复述，也不要编造没记过的事。）"
    )
