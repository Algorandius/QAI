"""定期清理过期数据，防止场景/对话无限增长。

清理三样东西：
- `memory.prune`：超过 `AI_MEMORY_TTL_DAYS` 的单条对话、整段不活跃场景（含摘要/心情/好感度）
- `longterm.prune`：超过 `AI_LONGTERM_TTL_DAYS` 的长期记忆（0 = 永久，默认永久）
- `memory.prune_chatlog`：超过 `AI_CHATLOG_KEEP_DAYS` 的聊天日志行

调度：`lifecycle.py` 在 NoneBot startup 时调 `start()`，先等 15 秒，之后每
`AI_MEMORY_CLEAN_INTERVAL_HOURS` 小时跑一次（默认 6 小时）。也可以手动 `/清理记忆`。
"""
from __future__ import annotations

import asyncio
import time

from nonebot import logger

from src import longterm, memory
from src.config import env_int

# 0 = 关闭对应清理
MEMORY_TTL_DAYS = max(0, env_int("AI_MEMORY_TTL_DAYS", 30))
LONGTERM_TTL_DAYS = max(0, env_int("AI_LONGTERM_TTL_DAYS", 0))
CHATLOG_KEEP_DAYS = max(0, env_int("AI_CHATLOG_KEEP_DAYS", 30))
INTERVAL_HOURS = max(1, env_int("AI_MEMORY_CLEAN_INTERVAL_HOURS", 6))

_started = False


def run_cleanup(now: float | None = None) -> dict:
    """同步执行一次清理，返回各类删除数量。"""
    now = time.time() if now is None else float(now)
    res = {
        "state": memory.prune(now, MEMORY_TTL_DAYS),
        "longterm": longterm.prune(LONGTERM_TTL_DAYS, now),
        "chatlog": memory.prune_chatlog(CHATLOG_KEEP_DAYS, now),
    }
    changed = (
        res["state"].get("entries", 0)
        or res["state"].get("scenes", 0)
        or res["state"].get("affinity", 0)
        or res["longterm"]
        or res["chatlog"]
    )
    if changed:
        logger.info(
            "[maintenance] 清理完成："
            f"对话 {res['state'].get('entries', 0)} 条、"
            f"场景 {res['state'].get('scenes', 0)} 个、"
            f"好感度 {res['state'].get('affinity', 0)} 条、"
            f"长期记忆 {res['longterm']} 条、"
            f"日志 {res['chatlog']} 条"
        )
    return res


def describe() -> str:
    """给命令/日志用的一句话说明。"""
    return (
        f"对话 TTL {MEMORY_TTL_DAYS} 天 · "
        f"长期记忆 {'永久' if LONGTERM_TTL_DAYS <= 0 else str(LONGTERM_TTL_DAYS) + ' 天'} · "
        f"日志保留 {CHATLOG_KEEP_DAYS} 天 · 每 {INTERVAL_HOURS} 小时一次"
    )


async def _loop() -> None:
    await asyncio.sleep(15)          # 等启动稳定，避免和 NapCat 首连抢资源
    while True:
        try:
            await asyncio.to_thread(run_cleanup)
        except Exception as e:
            logger.warning(f"[maintenance] 自动清理失败（不影响机器人）: {e}")
        await asyncio.sleep(INTERVAL_HOURS * 3600)


def start() -> None:
    """在事件循环里启动后台清理任务（只启动一次）。"""
    global _started
    if _started:
        return
    try:
        asyncio.get_running_loop().create_task(_loop())
    except RuntimeError:
        logger.warning("[maintenance] 没有运行中的事件循环，跳过自动清理")
        return
    _started = True
    logger.info(f"[maintenance] 自动清理已启动：{describe()}")
