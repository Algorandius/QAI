"""
连接生命周期插件：掉线重连提示

通过 NoneBot driver 钩子监听 NapCat 的连接与断开：
- 首次连接只记录，不播报。
- 检测到断开后重新连接，才在配置的群发送提示。
"""
from __future__ import annotations

import asyncio

from nonebot import get_driver, logger
from nonebot.adapters.onebot.v11 import Bot

from src import maintenance
from src.config import env_set

ADMIN_QQS = env_set("BOT_ADMIN_QQ")
ALLOWED_GROUPS = env_set("ALLOWED_GROUPS")
# 回归播报目标群：优先用 AI_WELCOME_GROUPS，没配则用 ALLOWED_GROUPS，都为空则静默
WELCOME_GROUPS = env_set("AI_WELCOME_GROUPS")

# 绘梨衣说话很短，掉线回来也只是一句
WELCOME_MSG = "我回来了"

driver = get_driver()

# 已经"第一次连接过"的 bot，用来区分首连与重连
_seen: set[int] = set()


@driver.on_bot_disconnect
async def _on_disconnect(bot: Bot) -> None:
    logger.warning(f"[lifecycle] 机器人 {bot.self_id} 连接已断开")


@driver.on_bot_connect
async def _on_connect(bot: Bot) -> None:
    if bot.self_id in _seen:
        # 之前连上过又断了，这次是重连 → 播报回归
        logger.info(f"[lifecycle] 检测到重连，开始播报回归消息（self_id={bot.self_id}）")
        await asyncio.sleep(2)  # 等握手稳定，避免一上来就发失败
        await _announce(bot)
    _seen.add(bot.self_id)


@driver.on_startup
async def _start_maintenance() -> None:
    """NoneBot 启动后拉起后台清理任务（过期对话/长期记忆/日志）。"""
    maintenance.start()


async def _announce(bot: Bot) -> None:
    targets = WELCOME_GROUPS or ALLOWED_GROUPS
    if not targets:
        logger.info("[lifecycle] 未配置欢迎群（AI_WELCOME_GROUPS / ALLOWED_GROUPS 为空），跳过回归播报")
        return
    for g in targets:
        try:
            gid = int(g)
        except ValueError:
            logger.warning(f"[lifecycle] 忽略非法群号：{g!r}")
            continue
        try:
            await bot.send_group_msg(group_id=gid, message=WELCOME_MSG)
        except Exception as e:
            logger.warning(f"[lifecycle] 回归播报失败（群 {gid}）：{e}")
