"""日记生成：从当天真实聊天里取材，用人设自己的口吻写。

原则：
  · 素材只给当天的真实对话，不让模型凭空编人编事
  · 口吻直接用人设正文（不是另写一套 prompt），这样和平时说话一致
  · 只用「说话人和内容」，不喂 QQ 号、图片链接这类无关信息
  · 生成失败绝不写空日记，宁可跳过
"""
from __future__ import annotations

import random
import re
import time

from nonebot import logger

from src import diary, memory, personas

MAX_MATERIAL_TURNS = 120      # 最多喂多少条对话给模型
MAX_MATERIAL_CHARS = 4000     # 素材总长度上限，防止 token 爆掉

_STRIP = re.compile(r"\[(?:图片|表情包?|语音|视频|文件|卡片消息/分享链接|骰子|猜拳)[^\]]*\]")


def _today_prefix() -> str:
    return time.strftime("%Y-%m-%d")


def today_material(scene_id: int, is_group: bool) -> tuple[list[dict], list[str]]:
    """取当天该场景的对话记录，返回 (素材列表, 出现的人)。

    chatlog 里只有 user_id 没有昵称（记录时就只存了 id），所以这里
    按 user_id 聚合，人名交给调用方用群成员接口补（拿不到就只写 id）。
    """
    day = _today_prefix()
    rows: list[dict] = []
    seen_uids: list[int] = []
    try:
        logs = memory.read_chatlog(4000)
    except Exception as e:
        logger.warning(f"[diary] 读聊天记录失败: {e}")
        return [], []

    wanted_scene = "group" if is_group else "private"
    for rec in logs:
        if str(rec.get("ts") or "")[:10] != day:
            continue
        if str(rec.get("scene") or "") != wanted_scene:
            continue
        try:
            if int(rec.get("scene_id") or 0) != int(scene_id):
                continue
        except (TypeError, ValueError):
            continue
        uid = rec.get("user_id")
        try:
            uid = int(uid or 0)
        except (TypeError, ValueError):
            uid = 0
        if uid and uid not in seen_uids:
            seen_uids.append(uid)
        user_text = str(rec.get("user") or "").strip()
        bot_text = str(rec.get("bot") or "").strip()
        if user_text:
            rows.append({"who": "对方", "uid": uid, "text": user_text})
        if bot_text:
            rows.append({"who": "我", "uid": 0, "text": bot_text})
        if len(rows) >= MAX_MATERIAL_TURNS:
            break

    return rows, [str(u) for u in seen_uids[:12]]


def build_prompt(persona_text: str, material: list[dict], persona_name: str) -> str:
    """拼提示词。人设正文在前，素材在后，明确「只准用这些素材」。"""
    lines = []
    total = 0
    for row in material:
        text = _STRIP.sub("", row["text"]).strip()
        if not text:
            continue
        line = f"{row['who']}：{text}"
        if total + len(line) > MAX_MATERIAL_CHARS:
            break
        lines.append(line)
        total += len(line)

    material_block = "\n".join(lines) if lines else "（今天几乎没怎么说话）"
    return (
        f"{persona_text}\n\n"
        "————\n"
        "现在请用你上面这个人设的语气，写一篇**只给自己看**的日记，记录今天。\n\n"
        "要求：\n"
        "1. 用第一人称，就像真人在手机备忘录里随手写的那样，不要写成作文。\n"
        "2. 语气、用词、标点习惯必须和平时说话完全一致——你就是那个人。\n"
        "3. 只能写下面素材里真实发生过的事、真实出现的人。**绝对不许编造**没发生的事。\n"
        "4. 可以写自己的心情、对某个人的印象变化、觉得好笑或在意的小细节。\n"
        "5. 长度 80~250 字，2~4 段，不要用 Markdown 标题和列表。\n"
        "6. 不要写日期、不要写「今天」以外的元信息，不要提到「素材」「记录」这类词。\n\n"
        f"【今天的对话素材】\n{material_block}\n"
    )


async def generate(
    scene_id: int,
    is_group: bool,
    persona_id: str,
    call_llm,
    *,
    force: bool = False,
) -> dict | None:
    """给「某场景 + 某人设」生成今天的日记。

    call_llm 由调用方传入（ai_chat 里的那个），避免这个模块反向依赖插件。
    返回写入的条目；素材不足或模型失败返回 None。
    """
    if not force and diary.has_today(is_group, scene_id, persona_id):
        logger.debug(f"[diary] 场景 {scene_id}/{persona_id} 今天已有日记，跳过")
        return None

    material, people = today_material(scene_id, is_group)
    if len(material) < 4:
        logger.info(f"[diary] 场景 {scene_id} 今天对话太少（{len(material)} 条），不写日记")
        return None

    persona_text = personas.text_of(persona_id) or ""
    name = personas.display_name(persona_id) or persona_id
    if not persona_text.strip():
        logger.warning(f"[diary] 人设 {persona_id} 正文为空，跳过")
        return None

    prompt = build_prompt(persona_text, material, name)
    messages = [{"role": "user", "content": prompt}]
    try:
        text = await call_llm(messages, temperature=0.9)
    except Exception as e:
        logger.warning(f"[diary] 生成失败: {type(e).__name__}: {e}")
        return None

    text = (text or "").strip()
    if len(text) < 20:
        logger.warning(f"[diary] 生成内容太短，丢弃：{text[:40]!r}")
        return None

    # 清掉模型自作主张加的前缀（「日记：」「2026-10-30」等）
    text = re.sub(r"^\s*(?:【?日记】?[:：]?\s*)", "", text)
    text = re.sub(r"^\s*\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?\s*", "", text)

    mood = float(memory.MOOD.get(scene_id, 0.0) or 0.0)
    try:
        return diary.write(
            is_group, scene_id, persona_id, text,
            persona_name=name, mood=mood,
            turns=len(material), people=people,
        )
    except Exception as e:
        logger.warning(f"[diary] 写入失败: {e}")
        return None


def pick_fallback_people(people: list[str], n: int = 3) -> list[str]:
    """从当天出现过的人里随机挑几个（给「日记里提到谁」做摘要用）。"""
    pool = [p for p in people if p]
    random.shuffle(pool)
    return pool[:n]
