"""日记的定时生成：每天固定时间，给「每个有对话的场景 × 该场景当前人设」写一篇。

为什么在后台单独跑而不是挂在消息处理里：
写日记要调模型，慢（几秒到十几秒）。挂在消息里会让最后一条消息的回复变慢。
这里每天只跑一次，慢一点没人察觉。

时间：默认 23:30（`DIARY_HOUR` / `DIARY_MINUTE` 可改）。
跳过规则：
  · 当天已经写过的（人设×场景）不再重写
  · 当天对话少于 4 条的，不写（没素材，写了也是编）
  · 机器人没连上、模型不可用的，静默跳过
"""
from __future__ import annotations

import asyncio
import time

from nonebot import logger

from src import diary, diary_gen, memory, personas
from src.config import env_bool, env_int

DIARY_ENABLE = env_bool("DIARY_ENABLE", True)
DIARY_AUTO = env_bool("DIARY_AUTO", True)
DIARY_HOUR = max(0, min(23, env_int("DIARY_HOUR", 23)))
DIARY_MINUTE = max(0, min(59, env_int("DIARY_MINUTE", 30)))
DIARY_MIN_TURNS = max(2, env_int("DIARY_MIN_TURNS", 4))

_started = False


def describe() -> str:
    state = "已开启" if (DIARY_ENABLE and DIARY_AUTO) else "已关闭"
    return f"每日 {DIARY_HOUR:02d}:{DIARY_MINUTE:02d} 自动写日记（{state}）"


def _seconds_until_next_run() -> float:
    """距离下一个目标时刻还有几秒。"""
    now = time.localtime()
    target = time.mktime((
        now.tm_year, now.tm_mon, now.tm_mday, DIARY_HOUR, DIARY_MINUTE, 0, 0, 0, -1,
    ))
    if target <= time.time():
        target += 86400
    return max(60.0, target - time.time())


def active_scenes() -> list[tuple[bool, int]]:
    """今天有对话的场景（群/私聊），去重。"""
    out: list[tuple[bool, int]] = []
    seen: set[tuple[bool, int]] = set()
    try:
        logs = memory.read_chatlog(6000)
    except Exception:
        return out
    day = time.strftime("%Y-%m-%d")
    for rec in logs:
        if str(rec.get("ts") or "")[:10] != day:
            continue
        is_group = str(rec.get("scene") or "") == "group"
        try:
            sid = int(rec.get("scene_id") or 0)
        except (TypeError, ValueError):
            continue
        if not sid:
            continue
        key = (is_group, sid)
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


async def write_all(call_llm, force: bool = False) -> dict:
    """给所有活跃场景写今天的日记。call_llm 由调用方传入避免循环依赖。"""
    scenes = active_scenes()
    done = skipped = failed = 0
    for is_group, sid in scenes:
        try:
            pid = personas.resolve((("group" if is_group else "private"), sid))
        except Exception:
            pid = ""
        if not pid:
            continue
        try:
            entry = await diary_gen.generate(
                sid, is_group, pid, call_llm, force=force,
            )
        except Exception as e:
            failed += 1
            logger.warning(f"[diary] 场景 {sid} 写日记异常: {type(e).__name__}: {e}")
            continue
        if entry:
            done += 1
            logger.info(f"[diary] 已写：{entry.get('scene')}:{sid} / {entry.get('persona_name') or pid}")
        else:
            skipped += 1
    if scenes:
        logger.info(f"[diary] 本轮完成：新写 {done} 篇，跳过 {skipped}，失败 {failed}")
    return {"scenes": len(scenes), "done": done, "skipped": skipped, "failed": failed, "at": time.strftime("%Y-%m-%d %H:%M:%S")}


async def _loop(call_llm) -> None:
    await asyncio.sleep(20)      # 等机器人连上 NapCat、模型可用
    while True:
        try:
            wait = _seconds_until_next_run()
            logger.info(f"[diary] 下次写日记：约 {wait / 3600:.1f} 小时后")
            await asyncio.sleep(wait)
            if DIARY_ENABLE and DIARY_AUTO:
                await write_all(call_llm)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"[diary] 定时任务异常（不影响机器人）: {type(e).__name__}: {e}")
            await asyncio.sleep(600)


def start(call_llm) -> None:
    """启动后台日记任务（只启动一次）。"""
    global _started
    if _started:
        return
    if not (DIARY_ENABLE and DIARY_AUTO):
        logger.info(f"[diary] 自动写日记未开启：{describe()}")
        return
    try:
        asyncio.get_running_loop().create_task(_loop(call_llm))
    except RuntimeError:
        logger.warning("[diary] 没有运行中的事件循环，跳过日记定时任务")
        return
    _started = True
    logger.info(f"[diary] 已启动：{describe()}")


def which_entries_missing_today() -> list[dict]:
    """哪些「场景×人设」今天还没写（给面板显示用）。"""
    out = []
    for is_group, sid in active_scenes():
        try:
            pid = personas.resolve((("group" if is_group else "private"), sid))
        except Exception:
            continue
        if not pid:
            continue
        if not diary.has_today(is_group, sid, pid):
            out.append({
                "scene": "group" if is_group else "private",
                "scene_id": sid,
                "persona": pid,
                "persona_name": personas.display_name(pid),
            })
    return out
