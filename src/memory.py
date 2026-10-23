"""对话状态存储：上下文、心情、摘要、贴纸冷却和聊天日志。

状态写入 data/state.json，聊天日志追加到 data/chatlog.jsonl。
Web 面板读取同一份数据。
"""
from __future__ import annotations

import json
import threading
import time
from collections import defaultdict, deque
from pathlib import Path

from nonebot import logger

from src.config import ROOT, env_int

DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)
STATE_FILE = DATA_DIR / "state.json"
CHATLOG_FILE = DATA_DIR / "chatlog.jsonl"

# 连续对话保留轮数（1 轮 = 用户 1 条 + 机器人 1 条）。用户已设为 50。
HISTORY_TURNS = max(1, env_int("AI_HISTORY_TURNS", 50))
# 压缩后保留的"原文"轮数（更早的会被卷进 SUMMARY）
COMPRESS_KEEP_TURNS = max(2, env_int("AI_COMPRESS_KEEP_TURNS", 12))

# 群 / 私聊对话记忆
GROUP_CTX: dict[int, deque] = defaultdict(lambda: deque(maxlen=HISTORY_TURNS * 2))
PRIVATE_CTX: dict[int, deque] = defaultdict(lambda: deque(maxlen=HISTORY_TURNS * 2))
# 每个角色在每个场景的对话快照：key = "g:群号|人设id" / "p:QQ号|人设id"
PERSONA_CTX: dict[str, deque] = {}
PERSONA_SUMMARY: dict[str, str] = {}
PERSONA_MOOD: dict[str, float] = {}
# 心情值：per scene，范围 [-10, 10]，0 平静
MOOD: dict[int, float] = defaultdict(float)
# 压缩后的长期记忆摘要
SUMMARY: dict[int, str] = defaultdict(str)
# 每个 scene 上次发贴纸的时间（Unix 秒，用于冷却）
LAST_STICKER: dict[int, float] = defaultdict(float)
# 正在压缩的 scene（防止并发重复压缩）
COMPRESSING: dict[int, bool] = defaultdict(bool)
# 好感度：key = "场景id:用户id"，value = {"s": 分数, "n": 昵称, "t": 上次聊天加分时间}
AFFINITY: dict[str, dict] = {}
# 每个场景最后一次"真正发生对话"的时间（Unix 秒）。没有它的旧数据加载时用文件 mtime 兜底。
LAST_ACTIVE: dict[int, float] = {}

# 落盘/清场都用这把锁，保证 Web 面板线程与事件循环不会同时改坏状态。
# 用 RLock（可重入）：clear_scene/reset_mood 会在持锁状态下调用 save_state（再次加锁），
# 非重入锁会死锁。
STATE_LOCK = threading.RLock()
# 聊天日志单独一把锁：清理时要整体重写文件，不能和追加写撞车
CHATLOG_LOCK = threading.Lock()


def get_ctx(is_group: bool, scene_id: int) -> deque:
    return (GROUP_CTX if is_group else PRIVATE_CTX)[scene_id]


def _persona_prefix(is_group: bool, scene_id: int) -> str:
    return f"{'g' if is_group else 'p'}:{scene_id}|"


def _persona_key(is_group: bool, scene_id: int, pid: str) -> str:
    return _persona_prefix(is_group, scene_id) + str(pid)


def switch_persona_ctx(
    is_group: bool,
    scene_id: int,
    old_pid: str | None,
    new_pid: str | None,
) -> None:
    """切换角色时，把该场景的上下文换成新角色自己的快照。"""
    if old_pid == new_pid:
        return
    with STATE_LOCK:
        store = GROUP_CTX if is_group else PRIVATE_CTX
        if old_pid:
            current = store.get(scene_id)
            key = _persona_key(is_group, scene_id, old_pid)
            if current:
                PERSONA_CTX[key] = deque(current, maxlen=HISTORY_TURNS * 2)
            else:
                PERSONA_CTX.pop(key, None)
            if SUMMARY.get(scene_id):
                PERSONA_SUMMARY[key] = SUMMARY[scene_id]
            else:
                PERSONA_SUMMARY.pop(key, None)
            PERSONA_MOOD[key] = float(MOOD.get(scene_id, 0) or 0)
        if new_pid:
            newkey = _persona_key(is_group, scene_id, new_pid)
            saved = PERSONA_CTX.get(newkey)
            store[scene_id] = deque(saved, maxlen=HISTORY_TURNS * 2) if saved else deque(maxlen=HISTORY_TURNS * 2)
            SUMMARY[scene_id] = PERSONA_SUMMARY.get(newkey, "") or ""
            MOOD[scene_id] = float(PERSONA_MOOD.get(newkey, 0) or 0)
        else:
            store[scene_id] = deque(maxlen=HISTORY_TURNS * 2)
            SUMMARY.pop(scene_id, None)
            MOOD[scene_id] = 0.0


# ---------------------------------------------------------------- 加载/保存
def load_state() -> None:
    """从磁盘恢复历史对话和状态。"""
    if not STATE_FILE.exists():
        return
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"[memory] 读取状态文件失败，跳过加载: {e}")
        return
    try:
        file_mtime = STATE_FILE.stat().st_mtime
    except OSError:
        file_mtime = time.time()
    try:
        for key, val in data.get("group", {}).items():
            gid = int(key)
            for e in val.get("ctx", []):
                if isinstance(e, dict) and "role" in e and "content" in e:
                    if "ts" not in e:
                        # 老数据没有时间戳：用状态文件最后一次写入时间兜底，
                        # 这样升级后不会把现有记忆立刻当成“过期”清掉。
                        e["ts"] = file_mtime
                    GROUP_CTX[gid].append(e)
            MOOD[gid] = float(val.get("mood", 0))
            SUMMARY[gid] = val.get("summary", "") or ""
            LAST_ACTIVE[gid] = float(val.get("last_active") or file_mtime)
        for key, val in data.get("private", {}).items():
            uid = int(key)
            for e in val.get("ctx", []):
                if isinstance(e, dict) and "role" in e and "content" in e:
                    if "ts" not in e:
                        e["ts"] = file_mtime
                    PRIVATE_CTX[uid].append(e)
            MOOD[uid] = float(val.get("mood", 0))
            SUMMARY[uid] = val.get("summary", "") or ""
            LAST_ACTIVE[uid] = float(val.get("last_active") or file_mtime)
        for key, val in (data.get("persona_ctx") or {}).items():
            if not isinstance(val, list):
                continue
            dq = deque(maxlen=HISTORY_TURNS * 2)
            for e in val:
                if isinstance(e, dict) and "role" in e and "content" in e:
                    if "ts" not in e:
                        e["ts"] = file_mtime
                    dq.append(e)
            if dq:
                PERSONA_CTX[str(key)] = dq
        for key, val in (data.get("persona_summary") or {}).items():
            if isinstance(val, str) and val:
                PERSONA_SUMMARY[str(key)] = val
        for key, val in (data.get("persona_mood") or {}).items():
            try:
                PERSONA_MOOD[str(key)] = float(val)
            except (TypeError, ValueError):
                pass
        for key, val in (data.get("affinity") or {}).items():
            if isinstance(val, dict):
                AFFINITY[key] = {
                    "s": float(val.get("s", 0)),
                    "n": str(val.get("n") or ""),
                    "t": float(val.get("t", 0)) or file_mtime,
                }
        if GROUP_CTX or PRIVATE_CTX:
            logger.info("[memory] 已恢复历史对话记忆（跨重启保留生效）")
    except Exception as e:
        logger.warning(f"[memory] 解析状态文件出错: {e}")


def save_state() -> None:
    """把当前状态快照写到磁盘。先写临时文件再 rename，避免写一半损坏。"""
    snap = {"group": {}, "private": {}, "affinity": {}, "persona_ctx": {}, "persona_summary": {}, "persona_mood": {}}
    with STATE_LOCK:
        for key, val in AFFINITY.items():
            snap["affinity"][key] = dict(val)
        for key, dq in PERSONA_CTX.items():
            if dq:
                snap["persona_ctx"][key] = [dict(e) for e in dq]
        snap["persona_summary"] = {k: v for k, v in PERSONA_SUMMARY.items() if v}
        snap["persona_mood"] = {k: v for k, v in PERSONA_MOOD.items() if v}
        for gid, dq in GROUP_CTX.items():
            snap["group"][gid] = {
                "ctx": [dict(e) for e in dq],
                "mood": MOOD.get(gid, 0),
                "summary": SUMMARY.get(gid, "") or "",
                "last_active": LAST_ACTIVE.get(gid, 0.0),
            }
        for uid, dq in PRIVATE_CTX.items():
            snap["private"][uid] = {
                "ctx": [dict(e) for e in dq],
                "mood": MOOD.get(uid, 0),
                "summary": SUMMARY.get(uid, "") or "",
                "last_active": LAST_ACTIVE.get(uid, 0.0),
            }
    try:
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
        tmp.replace(STATE_FILE)
    except Exception as e:
        logger.warning(f"[memory] 保存状态失败: {e}")


# ---------------------------------------------------------------- 聊天日志
def log_chat(
    scene: str,
    scene_id: int,
    user_id: int,
    user_text: str,
    bot_reply: str,
    media: list[dict] | None = None,
    bot_media: list[dict] | None = None,
    persona: str = "",
    persona_name: str = "",
) -> None:
    """追加一条对话记录，供 Web 面板回看。"""
    try:
        rec = {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "scene": scene,
            "scene_id": scene_id,
            "user_id": user_id,
            "user": user_text[:500],
            "bot": bot_reply[:500],
            "persona": persona,
            "persona_name": persona_name,
        }
        if media:
            rec["media"] = media[:8]
        if bot_media:
            rec["bot_media"] = bot_media[:8]
        with CHATLOG_LOCK:
            with CHATLOG_FILE.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning(f"[memory] 写聊天日志失败: {e}")


def read_chatlog(n: int = 50) -> list[dict]:
    if not CHATLOG_FILE.exists():
        return []
    try:
        lines = CHATLOG_FILE.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    out = []
    for ln in lines[-n:]:
        try:
            out.append(json.loads(ln))
        except Exception:
            continue
    return out


# ---------------------------------------------------------------- 好感度
def affinity_key(scene_id: int, user_id: int) -> str:
    return f"{scene_id}:{user_id}"


def get_affinity(scene_id: int, user_id: int) -> dict:
    return AFFINITY.get(affinity_key(scene_id, user_id)) or {"s": 0.0, "n": "", "t": 0.0}


def top_affinity(scene_id: int, n: int = 5) -> list[dict]:
    """返回某个场景里好感度最高的前 n 个人。"""
    prefix = f"{scene_id}:"
    rows = [(k, v) for k, v in AFFINITY.items() if k.startswith(prefix)]
    rows.sort(key=lambda x: float(x[1].get("s", 0)), reverse=True)
    out = []
    for k, v in rows[:n]:
        uid = k.split(":", 1)[1]
        out.append({
            "user_id": uid,
            "score": round(float(v.get("s", 0)), 1),
            "nick": v.get("n") or uid,
        })
    return out


def list_affinity(scene_id: int, limit: int = 100) -> list[dict]:
    """按分数倒序列出某个场景的全部好感度记录（给 Web 面板用）。"""
    prefix = f"{scene_id}:"
    out: list[dict] = []
    with STATE_LOCK:
        for k, v in AFFINITY.items():
            if not k.startswith(prefix):
                continue
            uid = k.split(":", 1)[1]
            out.append({
                "user_id": uid,
                "score": round(float(v.get("s", 0)), 1),
                "nick": v.get("n") or uid,
                "last_chat": float(v.get("t", 0)),
            })
    out.sort(key=lambda x: x["score"], reverse=True)
    return out[: max(1, int(limit))]


# ---------------------------------------------------------------- 清理/查询
def clear_all_conversations() -> int:
    """清空**所有**场景的对话记忆、心情、摘要、好感度。返回清掉的场景数。

    换人设时用：人格是全局的，留着上一个人的聊天记录会让模型人格串味。
    """
    with STATE_LOCK:
        n = len(set(GROUP_CTX) | set(PRIVATE_CTX))
        GROUP_CTX.clear()
        PRIVATE_CTX.clear()
        PERSONA_CTX.clear()
        PERSONA_SUMMARY.clear()
        PERSONA_MOOD.clear()
        MOOD.clear()
        SUMMARY.clear()
        AFFINITY.clear()
        LAST_ACTIVE.clear()
    save_state()
    return n


def clear_scene(is_group: bool, scene_id: int) -> None:
    """清空某个场景的对话记忆、心情、摘要、好感度。"""
    with STATE_LOCK:
        (GROUP_CTX if is_group else PRIVATE_CTX).pop(scene_id, None)
        for k in [k for k in PERSONA_CTX if k.startswith(_persona_prefix(is_group, scene_id))]:
            PERSONA_CTX.pop(k, None)
        for k in [k for k in PERSONA_SUMMARY if k.startswith(_persona_prefix(is_group, scene_id))]:
            PERSONA_SUMMARY.pop(k, None)
        for k in [k for k in PERSONA_MOOD if k.startswith(_persona_prefix(is_group, scene_id))]:
            PERSONA_MOOD.pop(k, None)
        MOOD.pop(scene_id, None)
        SUMMARY.pop(scene_id, None)
        LAST_ACTIVE.pop(scene_id, None)
        prefix = f"{scene_id}:"
        for k in [k for k in AFFINITY if k.startswith(prefix)]:
            AFFINITY.pop(k, None)
    save_state()


def touch_scene(is_group: bool, scene_id: int) -> None:
    """标记这个场景刚发生了一轮真实对话（供 TTL 清理判断活跃度）。"""
    with STATE_LOCK:
        LAST_ACTIVE[scene_id] = time.time()


def append_turn(
    is_group: bool,
    scene_id: int,
    user_content: str,
    user_mid: int | None,
    bot_content: str,
) -> deque:
    """把一轮（用户 + 机器人）写进记忆，并打时间戳/更新活跃时间。

    加锁是必要的：后台 TTL 清理会把整个 deque 换掉，面板线程也可能在 save_state，
    这里必须拿到“当前那个 deque”再 append，避免写到被替换掉的旧对象里。
    返回当前场景的 ctx（调用方后续 len() 用它）。
    """
    ts = time.time()
    with STATE_LOCK:
        ctx = (GROUP_CTX if is_group else PRIVATE_CTX)[scene_id]
        ctx.append({"role": "user", "content": user_content, "mid": user_mid, "ts": ts})
        ctx.append({"role": "assistant", "content": bot_content, "mid": None, "ts": ts})
        LAST_ACTIVE[scene_id] = ts
    return ctx


def prune(now: float | None = None, ttl_days: int = 30, save: bool = True) -> dict:
    """按时间清理过期对话记忆。

    - 超过 ttl_days 的单条 ctx 删掉；
    - 整个场景超过 ttl_days 没有真实对话：连摘要/心情/好感度一起清掉；
    - 好感度按最后聊天时间过期；
    - ttl_days <= 0 表示关闭（不删）。

    返回 {"entries": n, "scenes": n, "affinity": n}。测试时可传 save=False，避免覆盖真实 state.json。
    """
    stats = {"entries": 0, "scenes": 0, "affinity": 0}
    if not ttl_days or ttl_days <= 0:
        return stats
    now = time.time() if now is None else float(now)
    cutoff = now - ttl_days * 86400

    with STATE_LOCK:
        # 1) 单条对话按时间戳过期
        for store in (GROUP_CTX, PRIVATE_CTX):
            for sid in list(store.keys()):
                dq = store[sid]
                keep = deque(
                    (e for e in dq if float(e.get("ts") or 0) >= cutoff),
                    maxlen=dq.maxlen,
                )
                removed = len(dq) - len(keep)
                if removed:
                    stats["entries"] += removed
                    store[sid] = keep

        for key in list(PERSONA_CTX.keys()):
            dq = PERSONA_CTX[key]
            keep = deque(
                (e for e in dq if float(e.get("ts") or 0) >= cutoff),
                maxlen=dq.maxlen,
            )
            removed = len(dq) - len(keep)
            if removed:
                stats["entries"] += removed
            if keep:
                PERSONA_CTX[key] = keep
            else:
                PERSONA_CTX.pop(key, None)

        # 2) 整段场景过期：摘要/心情/好感度一起清掉，避免留下“没有上下文的壳”
        for sid in list(LAST_ACTIVE.keys()):
            if float(LAST_ACTIVE.get(sid) or 0) >= cutoff:
                continue
            removed_any = False
            for store in (GROUP_CTX, PRIVATE_CTX):
                dq = store.pop(sid, None)
                if dq is not None:
                    removed_any = True
                    stats["entries"] += len(dq)
            if MOOD.pop(sid, None) is not None:
                removed_any = True
            if SUMMARY.pop(sid, None):
                removed_any = True
            LAST_ACTIVE.pop(sid, None)
            for k in [k for k in PERSONA_CTX if k.startswith(f"g:{sid}|") or k.startswith(f"p:{sid}|")]:
                PERSONA_CTX.pop(k, None)
            for k in [k for k in PERSONA_SUMMARY if k.startswith(f"g:{sid}|") or k.startswith(f"p:{sid}|")]:
                PERSONA_SUMMARY.pop(k, None)
            for k in [k for k in PERSONA_MOOD if k.startswith(f"g:{sid}|") or k.startswith(f"p:{sid}|")]:
                PERSONA_MOOD.pop(k, None)
            prefix = f"{sid}:"
            for k in [k for k in AFFINITY if k.startswith(prefix)]:
                AFFINITY.pop(k, None)
                stats["affinity"] += 1
                removed_any = True
            if removed_any:
                stats["scenes"] += 1

        # 3) 对话被清空、又没有摘要/好感度的场景，连空壳一起删
        for sid in set(list(GROUP_CTX) + list(PRIVATE_CTX)):
            if GROUP_CTX.get(sid) or PRIVATE_CTX.get(sid):
                continue
            if SUMMARY.get(sid):
                continue
            if any(k.startswith(f"{sid}:") for k in AFFINITY):
                continue
            GROUP_CTX.pop(sid, None)
            PRIVATE_CTX.pop(sid, None)
            MOOD.pop(sid, None)
            LAST_ACTIVE.pop(sid, None)

        # 4) 好感度按最后聊天时间过期
        for k in list(AFFINITY.keys()):
            t = float(AFFINITY[k].get("t") or 0)
            if t and t < cutoff:
                AFFINITY.pop(k, None)
                stats["affinity"] += 1

    if save and (stats["entries"] or stats["scenes"] or stats["affinity"]):
        save_state()
    if any(stats.values()):
        logger.info(
            f"[memory] TTL 清理：对话 {stats['entries']} 条、"
            f"场景 {stats['scenes']} 个、好感度 {stats['affinity']} 条"
        )
    return stats


def prune_chatlog(keep_days: int = 30, now: float | None = None) -> int:
    """裁剪 data/chatlog.jsonl，只保留最近 keep_days 天。返回删除的行数。"""
    if not keep_days or keep_days <= 0 or not CHATLOG_FILE.exists():
        return 0
    now = time.time() if now is None else float(now)
    cutoff = now - keep_days * 86400
    # ts 格式固定为 "%Y-%m-%d %H:%M:%S"，字符串比较即可
    cutoff_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(cutoff))
    removed = 0
    kept: list[str] = []
    with CHATLOG_LOCK:
        try:
            lines = CHATLOG_FILE.read_text(encoding="utf-8").splitlines()
        except Exception as e:
            logger.warning(f"[memory] 读取聊天日志失败: {e}")
            return 0
        for ln in lines:
            try:
                rec = json.loads(ln)
                ts = str(rec.get("ts") or "")
            except Exception:
                kept.append(ln)      # 坏行保留，别误删
                continue
            if not ts or ts >= cutoff_str:
                kept.append(ln)
            else:
                removed += 1
        if removed:
            try:
                tmp = CHATLOG_FILE.with_suffix(".tmp")
                tmp.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
                tmp.replace(CHATLOG_FILE)
            except Exception as e:
                logger.warning(f"[memory] 重写聊天日志失败: {e}")
                return 0
    if removed:
        logger.info(f"[memory] 聊天日志清理：删掉 {removed} 条（保留 {keep_days} 天）")
    return removed


def reset_mood(scene_id: int) -> None:
    with STATE_LOCK:
        MOOD[scene_id] = 0.0
    save_state()


def list_scenes() -> list[dict]:
    out = []
    with STATE_LOCK:
        for gid, dq in GROUP_CTX.items():
            out.append({
                "scene": "group", "id": gid, "size": len(dq),
                "mood": round(MOOD.get(gid, 0), 1),
                "summary_len": len(SUMMARY.get(gid, "")),
            })
        for uid, dq in PRIVATE_CTX.items():
            out.append({
                "scene": "private", "id": uid, "size": len(dq),
                "mood": round(MOOD.get(uid, 0), 1),
                "summary_len": len(SUMMARY.get(uid, "")),
            })
    return out


# 模块加载即恢复历史记忆
load_state()
