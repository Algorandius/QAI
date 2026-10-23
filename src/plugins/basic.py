"""
基础功能演示插件
覆盖 OneBot v11 最常用的能力：收发消息 / @ / 表情 / 图片 / 引用回复 / 撤回 /
群成员查询 / 群列表 / 禁言 / 事件监听（戳一戳、新人入群、加好友请求）
"""
from __future__ import annotations

import random
import time

from nonebot import get_bot, logger, on_command, on_notice, on_request
from nonebot.adapters.onebot.v11 import (
    Bot,
    FriendRequestEvent,
    GroupIncreaseNoticeEvent,
    GroupMessageEvent,
    GroupRequestEvent,
    Message,
    MessageEvent,
    MessageSegment,
    PokeNotifyEvent,
)
from nonebot.params import CommandArg

from src.config import ROOT, env_set
from src.stats import STATS, uptime_str

ADMIN_QQS = env_set("BOT_ADMIN_QQ")
ALLOWED_GROUPS = env_set("ALLOWED_GROUPS")
ASSETS = ROOT / "assets"

# 记录机器人自己在每个群最后发出去的消息 id，供 /recall 演示使用
LAST_BOT_MSG: dict[int, int] = {}


def group_allowed(group_id: int) -> bool:
    """群白名单为空表示不限制"""
    if not ALLOWED_GROUPS:
        return True
    return str(group_id) in ALLOWED_GROUPS


async def say(event: MessageEvent, msg: str | Message) -> None:
    """统一出口：发送并记录，统计计数

    注意：必须用 event.self_id 拿到「收到这条消息的那个 bot」，
    不能用 get_bot()（无参时返回默认 bot，多账号场景下会发错号甚至报错）。
    """
    try:
        bot = get_bot(str(event.self_id))
        result = await bot.send(event, msg)
        STATS["sent"] += 1
        mid = (result or {}).get("message_id") if isinstance(result, dict) else None
        if mid and isinstance(event, GroupMessageEvent):
            LAST_BOT_MSG[event.group_id] = int(mid)
    except Exception as e:  # 发送失败不能让整个机器人挂掉
        logger.warning(f"发送失败: {e}")


async def is_manager(bot: Bot, group_id: int, user_id: int) -> bool:
    """判断是否为群管理/群主/配置里的管理员"""
    if str(user_id) in ADMIN_QQS:
        return True
    try:
        info = await bot.get_group_member_info(group_id=group_id, user_id=user_id)
        return info.get("role") in ("admin", "owner")
    except Exception:
        return False


# ---------------------------------------------------------------- 帮助
help_cmd = on_command("help", aliases={"帮助", "菜单"}, priority=10, block=True)


@help_cmd.handle()
async def _(event: MessageEvent):
    text = (
        "【基础功能】\n"
        "/ping            存活与延迟\n"
        "/echo <文本>      原样回显\n"
        "/me              查看你自己的群内身份\n"
        "/at <QQ号> [文本]  @某人\n"
        "/face            随机表情\n"
        "/img <URL或文件名> 发图片\n"
        "/reply <文本>     引用你的消息回复\n"
        "/recall          撤回机器人上一条消息（或回复某条消息后撤回它）\n"
        "/members         群成员列表（前 15 人）\n"
        "/groups          机器人所在群列表（仅管理员，私聊发送）\n"
        "/ban @某人 <分钟>  禁言（仅管理）\n"
        "/unban @某人       解除禁言（仅管理）\n"
        "/status          运行状态\n"
        "\n【AI 对话】\n"
        "群里 @机器人 / 叫名字 / 私聊直接说话\n"
        "/人格            列出人设；/人格 名字 切换（会清空全部记忆）\n"
        "/接话 开|关       自主接话开关（单独发送查看当前参数）\n"
        "/好感            查看自己 + 本群好感度\n"
        "/心情            查看当前心情（同 /mood）\n"
        "/记忆            查看长期记忆；/记忆 删 <编号> 忘掉某条\n"
        "/清理记忆         立即清理过期对话/日志（管理员或私聊）\n"
        "/表情包          从收藏里随机发一个表情包\n"
        "/建表情库         拉取收藏 → 视觉自动打标签（管理员或私聊）\n"
        "/aireset         清空本群/本人的对话记忆\n"
        "戳一戳机器人      她会用当前人设回你一句\n"
    )
    await say(event, text)


# ---------------------------------------------------------------- ping
ping = on_command("ping", aliases={"pong", "存活"}, priority=10, block=True)


@ping.handle()
async def _(event: MessageEvent):
    evt_ts = getattr(event, "time", None)
    delay = int(time.time() * 1000) - int(evt_ts) * 1000 if evt_ts else 0
    await say(event, f"pong! 消息往返约 {delay} ms\n已运行：{uptime_str()}")


# ---------------------------------------------------------------- echo
echo = on_command("echo", aliases={"回显"}, priority=10, block=True)


@echo.handle()
async def _(event: MessageEvent, args: Message = CommandArg()):
    text = args.extract_plain_text().strip()
    if not text:
        await echo.finish("用法：/echo 你要回显的内容")
    await say(event, text)


# ---------------------------------------------------------------- /me
me = on_command("me", aliases={"我是谁"}, priority=10, block=True)


@me.handle()
async def _(bot: Bot, event: MessageEvent):
    lines = [f"你的 QQ：{event.user_id}", f"消息 ID：{event.message_id}"]
    if isinstance(event, GroupMessageEvent):
        lines.append(f"群号：{event.group_id}")
        try:
            info = await bot.get_group_member_info(
                group_id=event.group_id, user_id=event.user_id
            )
            role_map = {"owner": "群主", "admin": "管理员", "member": "普通成员"}
            lines.append(f"群名片：{info.get('card') or info.get('nickname')}")
            lines.append(f"角色：{role_map.get(info.get('role'), info.get('role'))}")
            lines.append(f"入群时间：{time.strftime('%Y-%m-%d %H:%M', time.localtime(info.get('join_time', 0)))}")
        except Exception as e:
            lines.append(f"（读取群成员信息失败：{e}）")
    else:
        lines.append("场景：私聊")
    await say(event, "\n".join(lines))


# ---------------------------------------------------------------- /at
at_cmd = on_command("at", priority=10, block=True)


@at_cmd.handle()
async def _(event: GroupMessageEvent, args: Message = CommandArg()):
    text = args.extract_plain_text().strip()
    parts = text.split(maxsplit=1)
    if not parts or not parts[0].isdigit():
        await at_cmd.finish("用法：/at 12345678 喊你吃饭")
    target = parts[0]
    tail = parts[1] if len(parts) > 1 else "有人找你～"
    await say(event, MessageSegment.at(target) + " " + tail)


# ---------------------------------------------------------------- /face
face = on_command("face", aliases={"表情"}, priority=10, block=True)


@face.handle()
async def _(event: MessageEvent):
    # QQ 经典表情 id，取几个肯定存在的
    face_ids = [1, 2, 3, 4, 5, 12, 13, 14, 21, 32, 38, 49, 63, 76, 106, 111]
    await say(event, MessageSegment.face(random.choice(face_ids)))


# ---------------------------------------------------------------- /img
img = on_command("img", aliases={"图片", "发图"}, priority=10, block=True)


@img.handle()
async def _(event: MessageEvent, args: Message = CommandArg()):
    src = args.extract_plain_text().strip()
    if not src:
        await img.finish("用法：/img https://xxx.png  或  /img 本地文件名.png（放在 assets/ 目录）")
    if src.startswith("http://") or src.startswith("https://"):
        file_ = src
    else:
        p = ASSETS / src
        if not p.exists():
            await img.finish(f"assets/ 下找不到文件：{src}")
        file_ = str(p.resolve())
    try:
        await say(event, MessageSegment.image(file_))
    except Exception as e:
        await say(event, f"发图失败：{e}")


# ---------------------------------------------------------------- /reply
reply = on_command("reply", aliases={"引用回复"}, priority=10, block=True)


@reply.handle()
async def _(event: MessageEvent, args: Message = CommandArg()):
    text = args.extract_plain_text().strip() or "这是一条引用回复"
    await say(event, MessageSegment.reply(event.message_id) + text)


# ---------------------------------------------------------------- /recall
recall = on_command("recall", aliases={"撤回"}, priority=10, block=True)


@recall.handle()
async def _(bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()):
    target: int | None = None
    if event.reply:  # 回复某条消息后 /recall → 撤被回复的那条
        target = event.reply.message_id
    else:
        txt = args.extract_plain_text().strip()
        if txt.isdigit():
            target = int(txt)
        else:
            target = LAST_BOT_MSG.get(event.group_id)
    if target is None:
        await recall.finish("用法：回复一条消息后发送 /recall，或 /recall <消息ID>")
    try:
        await bot.delete_msg(message_id=target)
        await say(event, f"已撤回消息 {target}")
    except Exception as e:
        await say(event, f"撤回失败（机器人需要是管理员，且只能撤 2 分钟内的消息）：{e}")


# ---------------------------------------------------------------- /members
members = on_command("members", aliases={"成员"}, priority=10, block=True)


@members.handle()
async def _(bot: Bot, event: GroupMessageEvent):
    try:
        lst = await bot.get_group_member_list(group_id=event.group_id)
    except Exception as e:
        await members.finish(f"获取失败：{e}")
    role_map = {"owner": "群主", "admin": "管理", "member": "成员"}
    lines = [
        f"{m.get('card') or m.get('nickname')}（{m['user_id']}）"
        f"[{role_map.get(m.get('role'), '成员')}]"
        for m in lst[:15]
    ]
    await say(event, f"本群共 {len(lst)} 人，前 15 人：\n" + "\n".join(lines))


# ---------------------------------------------------------------- /groups
groups_cmd = on_command("groups", aliases={"群列表"}, priority=10, block=True)


@groups_cmd.handle()
async def _(bot: Bot, event: MessageEvent):
    if str(event.user_id) not in ADMIN_QQS:
        await groups_cmd.finish("仅管理员可用（请在 .env 的 BOT_ADMIN_QQ 里填上你的 QQ 号）")
    try:
        lst = await bot.get_group_list()
    except Exception as e:
        await groups_cmd.finish(f"获取失败：{e}")
    lines = [f"{g['group_id']} · {g.get('group_name')} · {g.get('member_count')} 人" for g in lst[:20]]
    await say(event, f"机器人所在群共 {len(lst)} 个：\n" + "\n".join(lines))


# ---------------------------------------------------------------- /ban /unban
ban = on_command("ban", aliases={"禁言"}, priority=10, block=True)


@ban.handle()
async def _(bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()):
    if not await is_manager(bot, event.group_id, event.user_id):
        await ban.finish("你没有权限（需要是群主/管理员，或已被列入 BOT_ADMIN_QQ）")

    # 注意：@机器人 的段会被适配器从 event.message 移除，
    # 所以取目标时优先看 original_message（最原始的完整消息）
    target = None
    for seg in event.original_message:
        if seg.type == "at" and str(seg.data.get("qq")) != str(event.self_id):
            target = int(seg.data["qq"])
            break
    if target is None:
        for seg in args:
            if seg.type == "at":
                target = int(seg.data["qq"])
                break
    minutes = 10
    for token in args.extract_plain_text().split():
        if token.isdigit():
            minutes = int(token)
            break
    if target is None:
        await ban.finish("用法：/ban @某人 10（分钟）")
    minutes = max(1, min(minutes, 60))  # 上限保护，别一抬手禁几天
    try:
        await bot.set_group_ban(group_id=event.group_id, user_id=target, duration=minutes * 60)
        await say(event, f"已禁言 {target} {minutes} 分钟")
    except Exception as e:
        await say(event, f"禁言失败（机器人需要管理员权限，且不能禁管理员）：{e}")


unban = on_command("unban", aliases={"解禁"}, priority=10, block=True)


@unban.handle()
async def _(bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()):
    if not await is_manager(bot, event.group_id, event.user_id):
        await unban.finish("你没有权限")
    target = None
    for seg in args:
        if seg.type == "at":
            target = int(seg.data["qq"])
            break
    if target is None:
        await unban.finish("用法：/unban @某人")
    try:
        await bot.set_group_ban(group_id=event.group_id, user_id=target, duration=0)
        await say(event, f"已解除 {target} 的禁言")
    except Exception as e:
        await say(event, f"操作失败：{e}")


# ---------------------------------------------------------------- /status
status = on_command("status", aliases={"状态"}, priority=10, block=True)


@status.handle()
async def _(event: MessageEvent):
    import os

    from src.config import env

    llm_ready = "已配置" if env("LLM_API_KEY") else "❌未配置（AI 功能不可用）"
    lines = [
        f"运行时长：{uptime_str()}",
        f"收到消息：{STATS['recv']} 条 / 发出：{STATS['sent']} 条",
        f"大模型调用：{STATS['ai_calls']} 次（失败 {STATS['ai_fail']} 次）",
        f"被限流拦下：{STATS['blocked']} 次",
        f"AI 接口：{llm_ready}",
        f"群白名单：{sorted(ALLOWED_GROUPS) if ALLOWED_GROUPS else '未限制'}",
        f"管理员：{sorted(ADMIN_QQS) if ADMIN_QQS else '未设置'}",
    ]
    await say(event, "\n".join(lines))


# ---------------------------------------------------------------- 事件监听：戳一戳
# 回复由当前人设临时生成，避免写死某套人设的台词。
_POKE_LAST: dict[tuple[int, int], float] = {}
POKE_COOLDOWN = 20          # 同一个人 20 秒内连戳不重复回，防止刷屏


async def _poke_reply(group_id: int) -> str:
    """让当前人设生成一句回应（不写死台词）。"""
    try:
        from src.plugins.ai_chat import call_llm, load_persona

        persona = load_persona(("group", group_id))
        ans = await call_llm([
            {"role": "system", "content": persona + (
                "\n【当前场景】群里有人用 QQ 的「拍一拍」戳了你一下。"
                "用你自己的语气回一句：一句话、一行、很短，不要解释、不要客服腔、不要提自己是 AI。"
            )},
            {"role": "user", "content": "[戳一戳]"},
        ])
        txt = (ans or "").strip().splitlines()
        if txt and txt[0].strip():
            return txt[0].strip()[:40]
    except Exception as e:
        logger.warning(f"[poke] 生成回应失败，用兜底: {e}")
    return "？"


notice = on_notice(priority=10, block=False)


@notice.handle()
async def _(bot: Bot, event: PokeNotifyEvent):
    if event.group_id is None:
        return
    if not group_allowed(event.group_id):
        return
    if event.target_id != event.self_id:  # 只回应戳机器人的
        return
    # 冷却：连着戳不会一直回
    key = (event.group_id, event.user_id)
    now = time.monotonic()
    if now - _POKE_LAST.get(key, 0.0) < POKE_COOLDOWN:
        return
    _POKE_LAST[key] = now

    reply = await _poke_reply(event.group_id)
    await bot.send_group_msg(
        group_id=event.group_id,
        message=MessageSegment.at(event.user_id) + " " + reply,
    )
    STATS["sent"] += 1


# ---------------------------------------------------------------- 事件监听：新人入群
@notice.handle()
async def _(bot: Bot, event: GroupIncreaseNoticeEvent):
    if not group_allowed(event.group_id):
        return
    await bot.send_group_msg(
        group_id=event.group_id,
        message=MessageSegment.at(event.user_id) + " 欢迎进群！发送 /help 看看我能做什么～",
    )
    STATS["sent"] += 1


# ---------------------------------------------------------------- 事件监听：加好友/加群请求
req = on_request(priority=10, block=False)


@req.handle()
async def _(bot: Bot, event: FriendRequestEvent):
    logger.info(f"收到加好友请求：{event.user_id} flag={event.flag}")
    for admin in ADMIN_QQS:
        try:
            await bot.send_private_msg(
                user_id=int(admin),
                message=f"收到加好友请求\nQQ：{event.user_id}\n验证信息：{event.comment}\n"
                        f"未自动处理，需要同意请手动操作或自行扩展代码。",
            )
            STATS["sent"] += 1
        except Exception as e:
            logger.warning(f"通知管理员失败: {e}")


@req.handle()
async def _(bot: Bot, event: GroupRequestEvent):
    logger.info(f"收到入群请求：{event.user_id} -> 群 {event.group_id} flag={event.flag}")
    for admin in ADMIN_QQS:
        try:
            await bot.send_private_msg(
                user_id=int(admin),
                message=f"收到入群请求\n群：{event.group_id}\nQQ：{event.user_id}\n"
                        f"验证信息：{event.comment}",
            )
            STATS["sent"] += 1
        except Exception as e:
            logger.warning(f"通知管理员失败: {e}")
