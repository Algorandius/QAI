"""消息类增强：已读、输入状态、表情回应、合并转发、历史消息、戳一戳。

这些都是「可选增强」——协议端不支持、QQ 版本变了、机器人没权限，
都不应该影响正常聊天。所以每个入口都吞异常，只记 debug 日志。

NapCat 相关接口：
  mark_msg_as_read / mark_all_as_read   标记已读
  set_input_status                      "正在输入"状态
  set_msg_emoji_like                    给消息贴表情回应
  send_forward_msg                      合并转发
  get_group_msg_history / get_friend_msg_history  历史消息
  get_msg                               按 id 取消息
"""
from __future__ import annotations

import time

from nonebot import get_driver, logger
from nonebot.adapters.onebot.v11 import Bot

from src.config import env_bool, env_int

# 各功能开关。默认全开，但都做了失败降级，所以开着也不会坏事。
MSG_READ_MARK = env_bool("MSG_READ_MARK", True)
MSG_INPUT_STATUS = env_bool("MSG_INPUT_STATUS", True)
MSG_EMOJI_LIKE = env_bool("MSG_EMOJI_LIKE", True)
MSG_EMOJI_LIKE_PROB = 0.35
MSG_HISTORY_BACKFILL = env_bool("MSG_HISTORY_BACKFILL", True)
MSG_HISTORY_LIMIT = max(1, env_int("MSG_HISTORY_LIMIT", 20))

# 记录已经探测过的能力，避免每轮都试一次不支持的接口。
_UNSUPPORTED: set[str] = set()


async def _try(bot: Bot, api: str, **params):
    """调一个可选接口；不支持就记下来，以后不再尝试。"""
    if api in _UNSUPPORTED:
        return None
    try:
        return await bot.call_api(api, **params)
    except Exception as e:
        text = str(e)
        # retcode 1404/1400 或 "不支持" 一类，视为协议端没有这个动作
        if any(k in text for k in ("不支持的", "unsupported", "not found", "1404", "未知")):
            _UNSUPPORTED.add(api)
            logger.debug(f"[msg+] {api} 协议端不支持，已跳过")
        else:
            logger.debug(f"[msg+] {api} 失败: {type(e).__name__}: {text[:120]}")
        return None


# ---------------------------------------------------------------- 已读
async def mark_read(bot: Bot, message_id: int) -> None:
    """把消息标记为已读，对方那边不再显示未读小红点。"""
    if not MSG_READ_MARK or not message_id:
        return
    await _try(bot, "mark_msg_as_read", message_id=int(message_id))


# ---------------------------------------------------------------- 正在输入
async def set_typing(bot: Bot, is_group: bool, target_id: int, seconds: float = 0.0) -> None:
    """显示"正在输入"。

    NapCat 没有"取消输入"的接口，状态会在对方那边停留一段时间后自动消失，
    所以只在真的会等的时候（模型思考）调用，不要在每句话前后乱刷。
    """
    if not MSG_INPUT_STATUS or not target_id:
        return
    if seconds > 1:
        return          # 只有明确要等待的调用方才传 0；传大值说明调用方不想显示
    try:
        if is_group:
            await _try(bot, "set_input_status", group_id=int(target_id), event_type=1)
        else:
            await _try(bot, "set_input_status", user_id=int(target_id), event_type=1)
    except Exception:
        pass


# ---------------------------------------------------------------- 表情回应
_EMOJI_IDS = {
    # QQ 表情回应用的 id：赞 / 笑 / 爱心 / 鼓掌 / 加油
    "praise": 128077,
    "laugh": 128514,
    "love": 10084,
    "clap": 128079,
    "cheer": 128170,
}


async def react(bot: Bot, message_id: int, kind: str = "praise") -> None:
    """给消息贴一个表情回应（不占消息条数，比再发一句自然）。"""
    if not MSG_EMOJI_LIKE or not message_id:
        return
    emoji_id = _EMOJI_IDS.get(kind)
    if not emoji_id:
        return
    await _try(bot, "set_msg_emoji_like",
               message_id=int(message_id), emoji_id=str(emoji_id), set=True)


def pick_react_kind(text: str) -> str | None:
    """根据对方说了什么挑一个合适的回应；不合适返回 None。"""
    t = str(text or "")
    if not t:
        return None
    if any(w in t for w in ("牛", "厉害", "绝了", "太强", "666", "优秀", "靠谱")):
        return "praise"
    if any(w in t for w in ("哈哈", "笑死", "草", "绷不住", "乐")):
        return "laugh"
    if any(w in t for w in ("爱你", "喜欢你", "抱抱", "亲亲", "想你了")):
        return "love"
    if any(w in t for w in ("谢谢", "感谢", "多谢")):
        return "clap"
    if any(w in t for w in ("加油", "冲", "干就完了", "挺住")):
        return "cheer"
    return None


# ---------------------------------------------------------------- 合并转发
async def forward_nodes(bot: Bot, is_group: bool, target_id: int,
                        messages: list[dict], title: str = "聊天记录") -> bool:
    """把一组消息打包成合并转发发出去。失败返回 False。"""
    if not messages:
        return False
    nodes = []
    for item in messages:
        nodes.append({
            "type": "node",
            "data": {
                "name": str(item.get("name") or "记录"),
                "uin": str(item.get("uin") or "0"),
                "content": str(item.get("content") or ""),
            },
        })
    params = {"messages": nodes, "prompt": title}
    if is_group:
        params["group_id"] = int(target_id)
    else:
        params["user_id"] = int(target_id)
    result = await _try(bot, "send_forward_msg", **params)
    return result is not None


# ---------------------------------------------------------------- 历史消息
async def fetch_history(bot: Bot, is_group: bool, target_id: int,
                        count: int | None = None) -> list[dict]:
    """拉最近的聊天记录（含机器人自己发的），用于冷启动补上下文。

    返回 [{"role": "user"/"assistant", "content": "...", "mid": ...}]。
    """
    if not MSG_HISTORY_BACKFILL:
        return []
    limit = count or MSG_HISTORY_LIMIT
    api = "get_group_msg_history" if is_group else "get_friend_msg_history"
    params = {"count": int(limit)}
    if is_group:
        params["group_id"] = int(target_id)
    else:
        params["user_id"] = int(target_id)
    data = await _try(bot, api, **params)
    if not isinstance(data, dict):
        return []
    raw = data.get("messages") or []
    if not isinstance(raw, list):
        return []

    self_id = str(getattr(bot, "self_id", "") or "")
    out: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        sender = item.get("sender") or {}
        who = sender.get("card") or sender.get("nickname") or ""
        text = _history_text(item)
        if not text:
            continue
        is_bot = str(item.get("user_id")) == self_id
        out.append({
            "role": "assistant" if is_bot else "user",
            "content": text if is_bot else (f"{who}：{text}" if who else text),
            "mid": item.get("message_id"),
            "ts": item.get("time"),
        })
    return out


def _history_text(item: dict) -> str:
    """从历史消息的 message 字段里取出可读文本。"""
    msg = item.get("message")
    if isinstance(msg, str):
        return msg.strip()[:300]
    if isinstance(msg, list):
        parts = []
        for seg in msg:
            if not isinstance(seg, dict):
                continue
            data = seg.get("data") or {}
            t = seg.get("type")
            if t == "text":
                parts.append(str(data.get("text") or ""))
            elif t == "at":
                parts.append(f"@{data.get('qq')}")
            elif t == "face":
                parts.append("[表情]")
            elif t == "image":
                parts.append("[图片]")
            elif t == "reply":
                continue
        return "".join(parts).strip()[:300]
    return ""


# ---------------------------------------------------------------- 单条消息
async def get_message(bot: Bot, message_id: int) -> dict | None:
    """按 id 取一条消息（引用回复、查撤回记录时用）。"""
    data = await _try(bot, "get_msg", message_id=int(message_id))
    return data if isinstance(data, dict) else None


# ---------------------------------------------------------------- 频次统计
class RateTracker:
    """按场景统计发言频次，供刷屏检测用。只保留最近 60 秒。"""

    __slots__ = ("_hits",)

    def __init__(self) -> None:
        self._hits: dict[int, list[float]] = {}

    def hit(self, scene_id: int, now: float | None = None) -> int:
        """记一次发言，返回最近 60 秒内的条数。"""
        t = time.time() if now is None else float(now)
        bucket = self._hits.setdefault(int(scene_id), [])
        bucket.append(t)
        cutoff = t - 60
        if len(bucket) > 40:
            bucket[:] = [x for x in bucket if x >= cutoff]
        return sum(1 for x in bucket if x >= cutoff)

    def reset(self, scene_id: int) -> None:
        self._hits.pop(int(scene_id), None)


RATE = RateTracker()
