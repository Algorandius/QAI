"""
收消息诊断日志（排查用）

把每条收到的消息的真实结构写进 logs/inbound.log，
用来确认 NapCat 到底把「表情 / 表情包 / 引用」转成了什么段。

看日志：
    type logs\\inbound.log

不需要时可以整个删掉这个文件。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from nonebot import logger, on_message
from nonebot.adapters.onebot.v11 import MessageEvent

from src.config import ROOT

LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "inbound.log"

probe = on_message(priority=1, block=False)


@probe.handle()
async def _(event: MessageEvent):
    try:
        reply_info = None
        if getattr(event, "reply", None):
            try:
                who = event.reply.sender.nickname or str(event.reply.sender.user_id)
            except Exception:
                who = "?"
            try:
                txt = event.reply.message.extract_plain_text()
            except Exception:
                txt = str(event.reply.message)
            reply_info = {"who": who, "text": txt[:200]}

        rec = {
            "ts": time.strftime("%H:%M:%S"),
            "scene": getattr(event, "message_type", "?"),
            "group_id": getattr(event, "group_id", None),
            "user_id": event.user_id,
            "raw": event.raw_message[:200],
            "segments": [{"type": s.type, "data": s.data}
                         for s in getattr(event, "original_message", event.message)],
            "reply": reply_info,
            "to_me": event.is_tome(),
        }
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:  # 日志绝不能影响主流程
        logger.warning(f"写诊断日志失败: {e}")
