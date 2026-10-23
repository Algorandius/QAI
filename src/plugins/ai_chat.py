"""
AI 对话插件（OpenAI 兼容协议）

群内：@机器人说话，或按前缀触发（见 .env 的 AI_GROUP_TRIGGER）
私聊：直接说话即可
"""
from __future__ import annotations

import asyncio
import base64
import json
import random
import re
import time
from collections import defaultdict, deque
from pathlib import Path

import httpx
from nonebot import logger, on_command, on_message
from nonebot.params import CommandArg
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    Message,
    MessageEvent,
    MessageSegment,
    PrivateMessageEvent,
)

from src.config import ROOT, env, env_bool, env_float, env_int, env_set
from src.stats import STATS

# ---------------- 配置 ----------------
from src import image_search, longterm, maintenance, memory, personas, sticker_lib, web_tools

ADMIN_QQS = env_set("BOT_ADMIN_QQ")
ALLOWED_GROUPS = env_set("ALLOWED_GROUPS")

AI_GROUP_TRIGGER = env("AI_GROUP_TRIGGER", "at").lower()   # at / prefix / off
AI_PREFIX = env("AI_PREFIX", "ai").lower()
# 群聊"自主接话"：没被 @ 时也按概率自己插一句（0=关闭，只在被 @ 时回；0.1=约一成消息会接）
# 自主接话总开关：false = 只在被 @ 时回复，永不主动接话（比把概率设 0 更直观）
AI_GROUP_AUTO = env_bool("AI_GROUP_AUTO", True)
AI_GROUP_AUTO_PROB = max(0.0, min(1.0, env_float("AI_GROUP_AUTO_PROB", 0.0)))
# 两次自主接话之间的最小间隔（秒），避免群里连续插话招人烦
AI_GROUP_AUTO_COOLDOWN = max(0, env_int("AI_GROUP_AUTO_COOLDOWN", 30))
AI_PRIVATE = env_bool("AI_PRIVATE", True)
HISTORY_TURNS = max(1, env_int("AI_HISTORY_TURNS", 50))    # 连续对话保留轮数（用户：50）
COOLDOWN = max(0, env_int("AI_COOLDOWN_SEC", 5))
MAX_CHARS = max(50, env_int("AI_MAX_CHARS", 600))
HUMANIZE = env_bool("AI_HUMANIZE", True)
# 拟人节奏：收到消息后先"愣一下"再开始打字（秒，在这个区间随机取）
THINK_MIN = env_float("AI_THINK_MIN", 1.2)
THINK_MAX = env_float("AI_THINK_MAX", 3.5)
# 打字速度（字/秒）。越小越慢越像真人，正常人手打中文大概 10~25 字/秒
TYPE_CPS = max(1.0, env_float("AI_TYPE_CPS", 16))
# 两条消息之间的停顿（秒，区间内随机）
GAP_MIN = env_float("AI_GAP_MIN", 1.8)
GAP_MAX = max(GAP_MIN, env_float("AI_GAP_MAX", 4.0))
# 单轮最多连发几条消息（防止模型话痨刷屏）
AI_MAX_MSGS = max(1, env_int("AI_MAX_MSGS", 5))
# 单条消息最多几个字，超出就在这里硬切（绘梨衣人设：一条只发一行、1~8 字，所以默认很小）
AI_MSG_MAX_CHARS = max(8, env_int("AI_MSG_MAX_CHARS", 20))
# 被叫到这些名字时一定回复（绘梨衣 / 小怪兽 / 月读命）
AI_NAME_ALIASES = env_set("AI_NAME_ALIASES") or {"绘梨衣", "小怪兽", "月读命"}
# 政治/时政话题硬拦截（人设要求最高优先级拒绝，程序层再兜一道）
AI_POLITICS_GUARD = env_bool("AI_POLITICS_GUARD", True)
# 对方是「引用某条消息」发问时，机器人回话也带上引用
AI_QUOTE_ON_REPLY = env_bool("AI_QUOTE_ON_REPLY", True)
# 主动引用：对方没引用时，机器人也有概率主动用 QQ「引用」框引用对方这条消息（更有人味）
AI_QUOTE_PROACTIVE = env_bool("AI_QUOTE_PROACTIVE", True)
# 主动引用概率（0~1）。1=每次都引用，0=关闭主动引用
AI_QUOTE_PROB = max(0.0, min(1.0, env_float("AI_QUOTE_PROB", 0.6)))
# 主动引用「更早」的历史消息：机器人判断该引用你哪条旧消息，用 [引N] 标记
AI_QUOTE_HIST = env_bool("AI_QUOTE_HIST", True)
# 图片识别（多模态）。默认关：DeepSeek 文本模型看不了图，开着会报错。
# 想开就换支持视觉的模型（如智谱 GLM-4.5V、硅基流动的 Qwen/Qwen2.5-VL）
AI_VISION = env_bool("AI_VISION", False)
AI_VISION_MAX_IMAGES = max(1, env_int("AI_VISION_MAX_IMAGES", 2))
# 识图后允许模型结合联网结果核对图片内容
AI_VISION_SEARCH = env_bool("AI_VISION_SEARCH", True)
# 单轮最多发几张图（模型可能一次要好几张，限制一下别刷屏）
AI_IMAGE_MAX_PER_TURN = max(1, env_int("AI_IMAGE_MAX_PER_TURN", 1))
# 主动配图概率：不是被明确要求时，只有这个概率才允许发图（调小 = 更少发图）
AI_IMAGE_TRIGGER_PROB = max(0.0, min(1.0, env_float("AI_IMAGE_TRIGGER_PROB", 0.4)))
# 两次发图的最小间隔（秒）
AI_IMAGE_COOLDOWN = max(0, env_int("AI_IMAGE_COOLDOWN", 90))
# 自主接话诊断日志：每条群消息都打一行，说明"接了/没接、为什么"。排查用，嫌吵可关
AI_AUTO_DEBUG = env_bool("AI_AUTO_DEBUG", True)
# 允许机器人发你 QQ 收藏的表情包（走 NapCat 的 fetch_custom_face 接口）
AI_CUSTOM_STICKER = env_bool("AI_CUSTOM_STICKER", True)
# 模型自主发收藏表情的概率与冷却（独立于图片，不受 AI_IMAGE_* 那套限制）
AI_CUSTOM_STICKER_PROB = max(0.0, min(1.0, env_float("AI_CUSTOM_STICKER_PROB", 0.25)))
AI_CUSTOM_STICKER_COOLDOWN = max(0, env_int("AI_CUSTOM_STICKER_COOLDOWN", 30))
# 是否把"表情库里有哪些类别"告诉模型（让它挑 mood 时有依据，而不是瞎猜）
AI_STICKER_CATALOG = env_bool("AI_STICKER_CATALOG", True)
# 主动补表情：每轮文字发完后，程序自己按概率追加一个表情包（不依赖模型调工具）
AI_AUTO_STICKER = env_bool("AI_AUTO_STICKER", True)
AI_AUTO_STICKER_PROB = max(0.0, min(1.0, env_float("AI_AUTO_STICKER_PROB", 0.3)))
AI_AUTO_STICKER_COOLDOWN = max(0, env_int("AI_AUTO_STICKER_COOLDOWN", 40))
# 主动戳一拍（QQ 的拍一拍，走 NapCat 的 group_poke）
AI_POKE = env_bool("AI_POKE", True)
AI_POKE_COOLDOWN = max(0, env_int("AI_POKE_COOLDOWN", 90))

# 历史压缩：把较早的对话卷成摘要，保留最近若干轮原文
AI_COMPRESS = env_bool("AI_COMPRESS", True)
COMPRESS_KEEP_TURNS = max(2, env_int("AI_COMPRESS_KEEP_TURNS", 12))
COMPRESS_TRIGGER = max(2, env_int("AI_COMPRESS_TRIGGER", 8))

# 心情系统
AI_MOOD = env_bool("AI_MOOD", True)

# 好感度：按群成员记录
AI_AFFINITY = env_bool("AI_AFFINITY", True)

# 长期记忆：模型主动记录值得跨天保留的人和事
AI_LONGTERM = env_bool("AI_LONGTERM", True)
AI_LONGTERM_RECALL = max(0, min(20, env_int("AI_LONGTERM_RECALL", 5)))

# 主动发表情
AI_STICKER = env_bool("AI_STICKER", True)
AI_STICKER_PROB = max(0.0, min(1.0, env_float("AI_STICKER_PROB", 0.35)))
AI_STICKER_COOLDOWN = max(0, env_int("AI_STICKER_COOLDOWN", 30))     # 两条贴纸最小间隔（秒）
AI_STICKER_SUBLIB = env("AI_STICKER_SUBLIB", "")

LLM_API_KEY = env("LLM_API_KEY")
LLM_BASE_URL = env("LLM_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
LLM_MODEL = env("LLM_MODEL", "deepseek-chat")
LLM_PROVIDER = env("LLM_PROVIDER", "")
LLM_TIMEOUT = env_float("LLM_TIMEOUT", 60)
LLM_TEMPERATURE = env_float("LLM_TEMPERATURE", 0.8)
_IS_DEEPSEEK = (
    LLM_PROVIDER == "deepseek"
    or "deepseek" in LLM_BASE_URL.lower()
    or LLM_MODEL.lower().startswith("deepseek")
)
_IS_OPENAI_REASONING = (
    LLM_PROVIDER == "openai"
    and re.match(r"^(o[134]|gpt-5)", LLM_MODEL.lower()) is not None
)

# .env 参数快照：作为"按场景解析人设参数"的基线。
# 群 A 可以挂话痨人设、群 B 挂寡言人设，参数各自不同，所以不能只靠全局变量。
PARAM_DEFAULTS = {
    "AI_MAX_CHARS": MAX_CHARS,
    "AI_MSG_MAX_CHARS": AI_MSG_MAX_CHARS,
    "AI_MAX_MSGS": AI_MAX_MSGS,
    "AI_COOLDOWN_SEC": COOLDOWN,
    "AI_THINK_MIN": THINK_MIN,
    "AI_THINK_MAX": THINK_MAX,
    "AI_TYPE_CPS": TYPE_CPS,
    "AI_GAP_MIN": GAP_MIN,
    "AI_GAP_MAX": GAP_MAX,
    "AI_QUOTE_PROB": AI_QUOTE_PROB,
    "AI_STICKER_PROB": AI_STICKER_PROB,
    "AI_STICKER_COOLDOWN": AI_STICKER_COOLDOWN,
    "AI_STICKER_SUBLIB": AI_STICKER_SUBLIB,
    "AI_CUSTOM_STICKER_PROB": AI_CUSTOM_STICKER_PROB,
    "AI_CUSTOM_STICKER_COOLDOWN": AI_CUSTOM_STICKER_COOLDOWN,
    "AI_AUTO_STICKER_PROB": AI_AUTO_STICKER_PROB,
    "AI_AUTO_STICKER_COOLDOWN": AI_AUTO_STICKER_COOLDOWN,
    "AI_IMAGE_TRIGGER_PROB": AI_IMAGE_TRIGGER_PROB,
    "AI_GROUP_AUTO_PROB": AI_GROUP_AUTO_PROB,
    "AI_GROUP_AUTO_COOLDOWN": AI_GROUP_AUTO_COOLDOWN,
    "AI_NAME_ALIASES": set(AI_NAME_ALIASES),
    "LLM_TEMPERATURE": LLM_TEMPERATURE,
}

PERSONA_FILE = ROOT / "persona.txt"          # 旧版单文件人设（仅作兼容回退）
DEFAULT_PERSONA = "你是一个在 QQ 群里聊天的助手，回答简洁自然，像真人一样说话，不要用 Markdown 语法。"
_PERSONA_CACHE: tuple[str, float, str] | None = None

# 复用 memory 模块里的跨场景/跨重启状态
GROUP_CTX = memory.GROUP_CTX
PRIVATE_CTX = memory.PRIVATE_CTX
MOOD = memory.MOOD
SUMMARY = memory.SUMMARY
LAST_STICKER = memory.LAST_STICKER

# 冷却： (场景id, 用户id) -> 上次调用时间戳
LAST_CALL: dict[tuple[int, int], float] = {}
# 群聊最近消息缓冲：群号 -> deque[(昵称, 文本)]，供"自主接话"时看懂大家在聊什么
GROUP_RECENT: dict[int, deque] = defaultdict(lambda: deque(maxlen=10))
# 自主接话的运行时开关（/接话 命令可切换；重启后回到 .env 的 AI_GROUP_AUTO）
AUTO_ENABLED = AI_GROUP_AUTO
# 每个群上次自主接话的时间戳，用于群级冷却
LAST_AUTO: dict[int, float] = {}
# 每个场景上次发图的时间（event loop 时钟），用于发图冷却
LAST_IMAGE: dict[int, float] = {}
# 每个场景上次发「收藏表情包」的时间，独立冷却（不受图片冷却影响）
LAST_CUSTOM_STICKER: dict[int, float] = {}
LAST_POKE: dict[int, float] = {}              # 每个场景上次戳人的时间，防频繁戳


def load_persona(scene_key=None) -> str:
    """读当前场景该用的人设正文。

    优先级：该群单独绑的人设 > 全局选中的人设 > 老的单文件 persona.txt。
    按 (id, mtime) 缓存：改文件立即生效，又不用每轮读一次磁盘。
    """
    global _PERSONA_CACHE
    pid = personas.resolve(scene_key)
    if pid:
        f = personas.PERSONA_DIR / pid / "persona.txt"
        try:
            mtime = f.stat().st_mtime
        except OSError:
            mtime = 0.0
        if _PERSONA_CACHE and _PERSONA_CACHE[0] == pid and _PERSONA_CACHE[1] == mtime:
            return _PERSONA_CACHE[2]
        txt = personas.text_of(pid)
        if txt:
            _PERSONA_CACHE = (pid, mtime, txt)
            return txt
    # 兼容：老的单文件 persona.txt
    if PERSONA_FILE.exists():
        try:
            return PERSONA_FILE.read_text(encoding="utf-8").strip()
        except Exception as e:
            logger.warning(f"读取 persona.txt 失败: {e}")
    return DEFAULT_PERSONA


def plain_text(text: str) -> str:
    """QQ 里渲染不了 Markdown，降级成纯文本"""
    text = re.sub(r"```[\s\S]*?```", "[代码块已省略]", text)
    text = re.sub(r"`([^`\n]*)`", r"\1", text)
    text = re.sub(r"^\s*#{1,6}\s*", "", text, flags=re.M)
    text = re.sub(r"\*\*([^*]*)\*\*", r"\1", text)
    text = re.sub(r"^\s*[-*+]\s*", "· ", text, flags=re.M)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# 把模型回复里的富文本标记还原成真正的 QQ 富文本：
#   [表情：名字]        → MessageSegment.face(id)（名字查不到就丢弃这串标记）
#   @qq号              → MessageSegment.at(qq)（渲染成高亮@，自动显示对方昵称）
#   [表情包：..]/[图片：..] 等元标记 → 整段丢弃（我们没法还原，发了也是乱码）
_RICH_RE = re.compile(
    r"\[表情[：:]([^\]]+)\]"                                       # 表情名标记（兼容全角/半角冒号）
    r"|@\s*(\d{5,})"                                              # @qq号
    r"|\[(?:(?:表情包|图片|语音|视频|文件|卡片消息/分享链接|戳一戳|骰子|猜拳)[^\]]*)\]"  # 其他不可发的元标记
)


# 模型有时只写裸标记（没给名字），这些都得兜底，否则会原样变成文字发出去
_BARE_FACE_RE = re.compile(r"[\[【]\s*表情\s*[\]】]|\[表情[：:]\s*\]")
# 兜底用的表情名（必须在 FACE_NAME_TO_ID 里存在）
_FALLBACK_FACE_NAMES = ("微笑", "憨笑", "偷笑", "呲牙", "调皮", "可爱")


def build_message(chunk: str) -> Message:
    # 先把 " [表情] / 【表情】 / [表情：] " 这类无参数标记补成一个真实表情名
    if _BARE_FACE_RE.search(chunk):
        pick_name = random.choice(_FALLBACK_FACE_NAMES)
        chunk = _BARE_FACE_RE.sub(f"[表情：{pick_name}]", chunk)

    msg = Message()
    pos = 0
    for m in _RICH_RE.finditer(chunk):
        if m.start() > pos:
            msg += chunk[pos:m.start()]
        if m.group(1) is not None:                       # [表情：名字]
            fid = FACE_NAME_TO_ID.get(m.group(1).strip())
            if fid is not None:
                msg += MessageSegment.face(fid)
            # 查不到：丢弃整串标记，不发出 "[表情：xxx]"
        elif m.group(2) is not None:                     # @qq号
            try:
                msg += MessageSegment.at(int(m.group(2)))
            except (ValueError, TypeError):
                pass
        # 其他元标记：整段丢弃
        pos = m.end()
    if pos < len(chunk):
        msg += chunk[pos:]
    return msg


def sticker_segment(data: bytes | str | Path) -> MessageSegment:
    """把表情图发成 QQ 的「动画表情」而不是普通图片。

    NapCat 内部判断：picSubType == 0 → "[图片]"，否则 → "[动画表情]"。
    默认 sub_type 是 0，所以在 QQ 里会显示成一张可点开的大图（看起来就像随手发的照片），
    而不是表情包那种贴纸效果。显式给 1 才是"表情包"。

    data 可以是图片字节、本地路径，也可以是 http(s) URL（NapCat 会自己去拉）。
    """
    seg = MessageSegment.image(data)
    seg.data["sub_type"] = 1
    return seg


async def send_sticker_file(bot: Bot, event: MessageEvent, path: Path) -> bool:
    """发送本地表情文件（读字节 + sub_type=1，发成"动画表情"）。返回是否成功。"""
    try:
        size = path.stat().st_size
    except OSError:
        logger.warning(f"[sticker] 文件读不到: {path.name}")
        return False
    if size > 5 * 1024 * 1024:
        # QQ/NapCat 对大 GIF 支持不好，硬发容易失败且没人看得到结果
        logger.warning(f"[sticker] 跳过过大表情 {path.name}（{size // 1024 // 1024}MB）")
        return False
    try:
        await bot.send(event, sticker_segment(path.read_bytes()))
        logger.info(f"[sticker] 已发表情 {path.name}（{size // 1024}KB, sub_type=1）")
        return True
    except Exception as e:
        logger.warning(f"[sticker] 发送失败 {type(e).__name__}: {e}")
        return False


async def send_sticker_url(bot: Bot, event: MessageEvent, url: str) -> bool:
    """发送一张远程收藏表情（QQ 收藏表情的 URL）。

    本地表情库为空/文件丢失时，`send_custom_sticker` 会退回 `fetch_custom_face` 拿 URL，
    这条路径必须和本地文件一样发成「动画表情」，否则会显示成普通图片。
    """
    if not url.startswith(("http://", "https://")):
        return False
    try:
        await bot.send(event, sticker_segment(url))
        logger.info(f"[sticker] 已发表情（远程）{url[:80]}")
        return True
    except Exception as e:
        logger.warning(f"[sticker] 远程表情发送失败 {type(e).__name__}: {e}")
        return False


def split_sentences(text: str, limit: int = 60) -> list[str]:
    """把回复拆成多条短消息，模拟真人一句一句连发。

    先按句末标点或换行切，超长的句子再按逗号/分号拆，还太长就按字符硬切。
    （模型被教了"句号能省就省"，多数时候用换行分句，所以换行必须算切分点。）
    """
    # 注意：省略号（……）不算切分点。它多用在句首/句中表示停顿，
    # 若当成切分点，"……这个我不懂。" 会被切成 "…" "…" "这个我不懂。" 三条。
    parts = re.split(r"(?<=[。！？!?~～]|\n)", text)
    chunks = []
    for seg in parts:
        seg = seg.strip()
        if not seg:
            continue
        if len(seg) > limit:
            for sub in re.split(r"(?<=[，,、；;])", seg):
                sub = sub.strip()
                if not sub:
                    continue
                if len(sub) > limit:
                    # 已经在逗号处切过了，还没有更细的标点：硬切但要避免把尾巴切得稀碎
                    while len(sub) > limit:
                        tail = sub[limit:]
                        if len(tail) < 5:
                            break       # 尾巴太短（如"的鱼"），宁可整条发出，也别切碎
                        chunks.append(sub[:limit])
                        sub = tail
                    if sub:
                        chunks.append(sub)
                else:
                    chunks.append(sub)
        else:
            chunks.append(seg)
    return [c for c in chunks if c] or [text]


# QQ 经典表情 id 与名称，编号需与 FACE_NAME_TO_ID 保持一致。
FACE_NAMES = {
    1: "撇嘴", 2: "色", 3: "发呆", 4: "得意", 5: "流泪", 6: "害羞", 7: "闭嘴",
    8: "睡", 9: "大哭", 10: "尴尬", 11: "发怒", 12: "调皮", 13: "呲牙", 14: "微笑",
    15: "惊讶", 16: "难过", 17: "酷", 18: "冷汗", 19: "抓狂", 20: "吐", 21: "偷笑",
    22: "可爱", 23: "白眼", 24: "傲慢", 25: "饥饿", 26: "困", 27: "惊恐", 28: "流汗",
    29: "憨笑", 30: "大兵", 31: "奋斗", 32: "咒骂", 33: "疑问", 34: "嘘", 35: "晕",
    36: "折磨", 37: "衰", 38: "骷髅", 39: "敲打", 40: "再见", 41: "擦汗", 42: "抠鼻",
    43: "鼓掌", 44: "糗大了", 45: "坏笑", 46: "左哼哼", 47: "右哼哼", 48: "哈欠",
    49: "鄙视", 50: "委屈", 51: "快哭了", 52: "阴险", 53: "亲亲", 54: "吓", 55: "可怜",
    56: "眨眼睛", 57: "笑哭", 58: "doge", 59: "妙啊", 60: "好的", 61: "打call",
    63: "旺柴", 66: "汗", 76: "笑", 106: "点赞", 111: "抱拳", 116: "庆祝",
    144: "社会社会", 175: "捂脸", 176: "奸笑", 177: "机智", 178: "皱眉", 179: "耶",
    180: "吃瓜", 183: "加油", 212: "狂笑", 277: "龇牙",
}

# 表情名 → face id（用于把模型写的 [表情：名字] 还原成真实 QQ 表情）
# 仅覆盖常见表情；模型若写表里没有的名字会被安全忽略（不发出那串文字）
FACE_NAME_TO_ID = {
    "微笑": 14, "撇嘴": 1, "色": 2, "发呆": 3, "得意": 4, "流泪": 5, "害羞": 6,
    "闭嘴": 7, "睡": 8, "大哭": 9, "尴尬": 10, "发怒": 11, "调皮": 12, "呲牙": 13,
    "惊讶": 15, "难过": 16, "酷": 17, "冷汗": 18, "抓狂": 19, "吐": 20, "偷笑": 21,
    "可爱": 22, "白眼": 23, "傲慢": 24, "饥饿": 25, "困": 26, "惊恐": 27, "流汗": 28,
    "憨笑": 29, "大兵": 30, "奋斗": 31, "咒骂": 32, "疑问": 33, "嘘": 34, "晕": 35,
    "折磨": 36, "衰": 37, "骷髅": 38, "敲打": 39, "再见": 40, "擦汗": 41, "抠鼻": 42,
    "鼓掌": 43, "糗大了": 44, "坏笑": 45, "左哼哼": 46, "右哼哼": 47, "哈欠": 48,
    "鄙视": 49, "委屈": 50, "快哭了": 51, "阴险": 52, "亲亲": 53, "吓": 54, "可怜": 55,
    "眨眼睛": 56, "笑哭": 57, "doge": 58, "妙啊": 59, "好的": 60, "打call": 61,
    "旺柴": 63, "汗": 66, "点赞": 106, "抱拳": 111, "庆祝": 116, "社会社会": 144,
    "捂脸": 175, "奸笑": 176, "机智": 177, "皱眉": 178, "耶": 179, "吃瓜": 180,
    "加油": 183, "狂笑": 212, "龇牙": 277,
}


def message_to_text(message) -> tuple[str, list[str], list[dict]]:
    """把 QQ 消息段转换成可读文本，并收集图片和媒体信息。

    返回：(文本, 图片 URL 列表, 媒体列表)
    """
    parts: list[str] = []
    images: list[str] = []
    media: list[dict] = []
    images: list[str] = []

    for seg in message:
        t, d = seg.type, seg.data
        if t == "text":
            parts.append(d.get("text", ""))
        elif t == "at":
            parts.append(f"@{d.get('qq')}")
        elif t == "face":
            # NapCat 会把表情名放在 data.raw.faceText（形如 "/大怨种"），
            # 商城表情/新表情的 id 查不到内置表，必须靠这个字段，否则只能显示"[表情]"
            name = ""
            raw = d.get("raw")
            if isinstance(raw, dict):
                name = (raw.get("faceText") or "").strip().lstrip("/").strip()
            elif isinstance(raw, str) and "faceText" in raw:
                import re as _re
                m = _re.search(r'"faceText":"([^"]+)"', raw)
                if m:
                    name = m.group(1).lstrip("/").strip()
            if not name:
                fid = d.get("id")
                name = FACE_NAMES.get(int(fid), "") if str(fid).isdigit() else ""
            parts.append(f"[表情:{name}]" if name else "[表情]")
        elif t in ("mface", "sticker", "marketface"):
            summary = (d.get("summary") or d.get("text") or "").strip()
            parts.append(f"[表情包:{summary}]" if summary else "[表情包]")
            sticker_url = d.get("url") or d.get("file") or ""
            if isinstance(sticker_url, str) and sticker_url.startswith("http"):
                media.append({"kind": "sticker", "url": sticker_url, "name": summary})
        elif t == "image":
            summary = (d.get("summary") or "").strip()
            parts.append(f"[图片:{summary}]" if summary else "[图片]")
            url = d.get("url") or d.get("file") or ""
            if isinstance(url, str) and url.startswith("http"):
                images.append(url)
                media.append({"kind": "image", "url": url, "name": summary})
        elif t == "record":
            parts.append("[语音]")
        elif t == "video":
            parts.append("[视频]")
        elif t == "file":
            parts.append(f"[文件:{d.get('file', '')}]")
        elif t in ("json", "xml", "ark"):
            parts.append("[卡片消息/分享链接]")
        elif t in ("forward", "forward_msg"):
            # 合并转发/聊天记录：协议端给不到里面的正文，
            # 必须明确告诉模型"看不到"，否则它会拿一串乱码去硬猜内容
            parts.append("[对方转发了一段聊天记录，我看不到里面的内容]")
        elif t == "poke":
            parts.append("[戳一戳]")
        elif t == "dice":
            parts.append("[骰子]")
        elif t == "rps":
            parts.append("[猜拳]")
        elif t == "reply":
            continue  # 引用单独处理
        else:
            parts.append(f"[{t}]")

    return "".join(parts).strip(), images, media


async def call_llm_raw(
    messages: list[dict],
    tools: list[dict] | None = None,
    temperature: float | None = None,
    no_thinking: bool = False,
) -> dict:
    """调用 chat/completions，返回原始 message（可能带 tool_calls）。"""
    if not LLM_API_KEY:
        raise RuntimeError("未配置 LLM_API_KEY")
    headers = {
        "Authorization": f"Bearer {LLM_API_KEY}",
        "Content-Type": "application/json",
    }
    if LLM_PROVIDER == "openrouter":
        headers["HTTP-Referer"] = "http://127.0.0.1:8081"
        headers["X-Title"] = "QAI"
    payload: dict = {
        "model": LLM_MODEL,
        "messages": messages,
        "stream": False,
    }
    if not _IS_OPENAI_REASONING:
        payload["temperature"] = LLM_TEMPERATURE if temperature is None else temperature
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    if _IS_DEEPSEEK and (tools or no_thinking):
        # DeepSeek 专有字段；其他厂商不认识，发了会 400。
        payload["thinking"] = {"type": "disabled"}
    async with httpx.AsyncClient(timeout=LLM_TIMEOUT) as client:
        resp = await client.post(f"{LLM_BASE_URL}/chat/completions", json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    return data["choices"][0]["message"]


async def call_llm(messages: list[dict], temperature: float | None = None) -> str:
    msg = await call_llm_raw(messages, temperature=temperature)
    return (msg.get("content") or "").strip()


# ---------------------------------------------------------------- 发图工具（Tool Calls）
IMAGE_TOOL_NAME = "search_and_send_image"
STICKER_TOOL_NAME = "send_custom_sticker"
POKE_TOOL = "send_poke"
WEB_SEARCH_TOOL = "web_search"
WEB_FETCH_TOOL = "web_fetch"
MEMORY_APPEND_TOOL = "memory_append"
MEMORY_QUERY_TOOL = "memory_query"
MEMORY_REMOVE_TOOL = "memory_remove"


async def fetch_custom_faces(bot: Bot, count: int = 50) -> list[str]:
    """取机器人账号「QQ 收藏表情」的 URL 列表（NapCat 扩展接口 fetch_custom_face）。

    NapCat 返回的是字符串数组（表情 URL），可直接交给 MessageSegment.image 发送。
    """
    try:
        data = await bot.call_api("fetch_custom_face", count=count)
    except Exception as e:
        logger.warning(f"[sticker] 获取收藏表情失败: {e}")
        return []
    if isinstance(data, dict):
        data = data.get("data") or data.get("urls") or []
    if not isinstance(data, list):
        return []
    return [u for u in data if isinstance(u, str) and u]


async def _download_sticker(url: str) -> tuple[bytes, str] | None:
    """下载一张收藏表情，返回 (字节, 扩展名)。"""
    try:
        async with httpx.AsyncClient(
            trust_env=False, timeout=25, follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) QQBot/1.0"},
        ) as c:
            r = await c.get(url)
            r.raise_for_status()
        data = r.content
        if not data or len(data) > 10 * 1024 * 1024:
            return None
        ct = (r.headers.get("content-type") or "").lower()
        if "gif" in ct:
            ext = "gif"
        elif "png" in ct:
            ext = "png"
        elif "webp" in ct:
            ext = "webp"
        else:
            ext = "jpg"
        return data, ext
    except Exception as e:
        logger.warning(f"[sticker] 下载失败 {type(e).__name__}: {url[:60]}")
        return None


async def label_sticker(data: bytes, ext: str) -> list[str]:
    """用视觉模型给一张表情打 2-4 个中文标签（情绪/动作）。"""
    mime = "image/gif" if ext == "gif" else f"image/{'jpeg' if ext == 'jpg' else ext}"
    b64 = base64.b64encode(data).decode()
    try:
        msg = await call_llm_raw(
            [{"role": "user", "content": [
                {"type": "text", "text": (
                    "这是一张聊天用的表情包。用 2-4 个中文词概括它适合表达什么，"
                    "只用逗号分隔，不要解释、不要写句子。\n"
                    "有明显情绪或动作的，例如：笑,开心,哈哈哈；无语,服了；赞,牛；哭,委屈；生气,暴躁；卖萌,可爱\n"
                    "如果它没有明确情绪、只是玩梗/抽象/整活/沙雕图，就输出：整活,玩闹,梗图\n"
                    "打招呼、点赞、感谢、鞠躬这类也照实写。"
                )},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            ]}],
            temperature=0.3,
            no_thinking=True,
        )
    except Exception as e:
        logger.warning(f"[sticker] 标注失败: {e}")
        return []
    text = (msg.get("content") or "").strip()
    tags = [t.strip() for t in re.split(r"[,，、;；\s]+", text) if t.strip()]
    return tags[:6]


async def build_sticker_library(bot: Bot, limit: int = 200) -> tuple[int, int]:
    """拉取收藏表情 → 下载到本地 → 视觉打标签 → 写索引。返回 (成功数, 总数)。"""
    urls = await fetch_custom_faces(bot, count=limit)
    if not urls:
        return 0, 0
    index: list[dict] = []
    ok = 0
    for i, url in enumerate(urls, 1):
        got = await _download_sticker(url)
        if not got:
            continue
        data, ext = got
        path = sticker_lib.file_for(url, ext)
        try:
            path.write_bytes(data)
        except Exception as e:
            logger.warning(f"[sticker] 写文件失败: {e}")
            continue
        tags = await label_sticker(data, ext)
        index.append({"file": path.name, "tags": tags, "url": url})
        ok += 1
        logger.info(f"[sticker] 已处理 {i}/{len(urls)}：{tags}")
    sticker_lib.save_index(index)
    return ok, len(urls)


def build_tools(
    allow_search: bool = True,
    allow_sticker: bool = True,
    allow_web: bool = False,
    allow_poke: bool = False,
    allow_memory: bool = False,
) -> list[dict]:
    """按本次放行情况，决定给模型哪些工具。

    五类互相独立：戳一下 / 搜图 / 发收藏表情 / 联网搜资料 / 长期记忆。
    只给需要的，避免模型在纯闲聊时乱调工具、也避免白花搜索费用。
    """
    tools: list[dict] = []
    if allow_poke:
        tools.append({
            "type": "function",
            "function": {
                "name": POKE_TOOL,
                "description": (
                    "轻轻戳对方一下（QQ 的「拍一拍」）。"
                    "适合作答不了的小撒娇、想引起注意、回应对方的玩笑、或者被戳了回戳一下。"
                    "非常克制：一整段对话最多用一次，没事不要乱戳。"
                ),
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        })
    if allow_search:
        tools.append({
            "type": "function",
            "function": {
                "name": IMAGE_TOOL_NAME,
                "description": (
                    "根据关键词搜索一张图片并发给对方。只在对方明确想看图片、"
                    "或当前话题确实适合配图时调用；纯闲聊、讲道理、回答问题时不要调用。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "keyword": {
                            "type": "string",
                            "description": "简短的中文名词，例如「橘猫」「海边日落」「火锅」",
                        }
                    },
                    "required": ["keyword"],
                },
            },
        })
    if allow_sticker:
        tools.append({
            "type": "function",
            "function": {
                "name": STICKER_TOOL_NAME,
                "description": (
                    "从你收藏的表情包里挑一个发出去（是图片，不是小黄脸表情）。"
                    "对方让你发表情包、斗图、来点表情时，必须调用本工具——"
                    "不要用 [表情：xx] 文本代替，那不是表情包。"
                    "平时聊天气氛到了也可以主动发一个，但一条回复最多一个。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "mood": {
                            "type": "string",
                            "description": (
                                "想表达的情绪，1-2 个词：笑 / 无语 / 赞 / 哭 / 生气 / 卖萌 / 谢谢。"
                                "只是想活跃气氛、接梗、整活、或者图本身没什么意义就想发一个时，填「玩闹」"
                            ),
                        }
                    },
                    "required": [],
                },
            },
        })
    if allow_web:
        tools.append({
            "type": "function",
            "function": {
                "name": WEB_SEARCH_TOOL,
                "description": (
                    "联网搜索网页。用于需要实时/准确信息，或需要核实图片中的人物、地点、"
                    "事件、商品、作品和文字内容的场合；新闻、价格、天气、赛事、"
                    "某个东西现在怎么样了、你不确定的事实也可以搜。"
                    "日常闲聊、情绪对话、你已经知道的常识，不要搜。"
                    "搜到结果后如果摘要不够，可以用 " + WEB_FETCH_TOOL + " 打开最相关那一页看正文。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "搜索关键词，简短明确，例如「上海 天气」「DeepSeek 最新版本」",
                        }
                    },
                    "required": ["query"],
                },
            },
        })
        tools.append({
            "type": "function",
            "function": {
                "name": WEB_FETCH_TOOL,
                "description": (
                    "打开一个网页，读它的正文（Markdown）。"
                    "在 " + WEB_SEARCH_TOOL + " 的结果里看到合适的链接、但摘要信息不够时再用。"
                    "一次只开一两个最相关的页面，不要把所有链接都点一遍。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "完整网址，必须以 http 开头"}
                    },
                    "required": ["url"],
                },
            },
        })
    if allow_memory:
        tools.append({
            "type": "function",
            "function": {
                "name": MEMORY_APPEND_TOOL,
                "description": (
                    "把一件值得跨天保留的事记进长期记忆（对方的偏好、约定、重要经历、在意的人）。"
                    "只在对方明确让你记住、或这件事明显以后还会用到时调用；不要记流水账，"
                    "不要记密码/住址/身份证号等敏感信息。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "text": {
                            "type": "string",
                            "description": "一句话，具体、第三人称，例如「小王下周要考试，最近在复习」",
                        },
                        "tags": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "2~3 个关键词，方便以后检索，例如 [\"小王\",\"考试\"]",
                        },
                        "who": {
                            "type": "string",
                            "description": "和谁有关（昵称）；不确定就留空",
                        },
                    },
                    "required": ["text"],
                },
            },
        })
        tools.append({
            "type": "function",
            "function": {
                "name": MEMORY_QUERY_TOOL,
                "description": (
                    "检索本场景的长期记忆。被问「你还记得吗」「上次说的…」，"
                    "或需要回忆某人的偏好/约定时，先查再答。query 留空 = 返回最近几条。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "关键词，例如「小王 考试」「生日」；留空看最近几条",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "最多返回几条，默认 8",
                        },
                    },
                    "required": [],
                },
            },
        })
        tools.append({
            "type": "function",
            "function": {
                "name": MEMORY_REMOVE_TOOL,
                "description": (
                    "忘掉一条长期记忆。对方明确说「忘掉这个」「别记着」时使用；"
                    "id 用 memory_query 返回的编号（例如 3 或 #3）。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "string",
                            "description": "要删除的记忆编号，例如 \"3\"",
                        }
                    },
                    "required": ["id"],
                },
            },
        })
    return tools


async def chat_with_image_tool(
    messages: list[dict],
    pending: list,
    bot: Bot | None = None,
    tools: list[dict] | None = None,
    temperature: float | None = None,
    event: MessageEvent | None = None,
    scene_key: str = "",
    sticker_sublib: str = "",
) -> str:
    """带工具的多轮对话（搜图 / 发收藏表情 / 联网搜资料 / 戳一下对方 / 长期记忆）。

    模型调用工具时，图片/表情先攒进 pending、不立刻发出；
    等上层把文字发完再甩出去（先说话、后发图，更像真人）。
    pending 里可能是图片 bytes，也可能是收藏表情的 URL 字符串。
    联网搜索的结果则直接以 role=tool 回给模型，让它接着把话说完。
    长期记忆按 scene_key 隔离（"group:123" / "private:456"）。
    """
    msgs = list(messages)
    if tools is None:
        tools = build_tools()
    web_used = 0
    for _ in range(4):                      # 最多四轮工具往返（搜索要多走几步，但要防死循环）
        msg = await call_llm_raw(msgs, tools=tools, temperature=temperature)
        calls = msg.get("tool_calls") or []
        if not calls:
            return (msg.get("content") or "").strip()
        msgs.append({
            "role": "assistant",
            "content": msg.get("content") or "",
            "tool_calls": calls,
        })
        for tc in calls:
            fn = tc.get("function") or {}
            name = fn.get("name")
            result = "没有找到相关图片"
            if name == IMAGE_TOOL_NAME:
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except (json.JSONDecodeError, TypeError):
                    args = {}
                kw = str(args.get("keyword") or "").strip()
                if not kw:
                    result = "关键词为空，没搜"
                elif len(pending) >= AI_IMAGE_MAX_PER_TURN:
                    result = "这轮已经发过了，不要再发"
                else:
                    data = await image_search.fetch_image(kw)
                    if data:
                        pending.append(data)
                        result = f"已找到「{kw}」的图片"
                    else:
                        result = f"没搜到「{kw}」的图片"
            elif name == STICKER_TOOL_NAME:
                if bot is None:
                    result = "现在发不了表情包"
                elif len(pending) >= AI_IMAGE_MAX_PER_TURN:
                    result = "这轮已经发过了，不要再发"
                else:
                    try:
                        args = json.loads(fn.get("arguments") or "{}")
                    except (json.JSONDecodeError, TypeError):
                        args = {}
                    mood = str(args.get("mood") or "").strip()
                    # 优先用本地表情库（带标签，能按情绪挑）
                    local = sticker_lib.pick([mood] if mood else [], sublib=sticker_sublib)
                    if local is not None:
                        pending.append(str(local))
                        result = f"已挑了一个表情（{mood or '随机'}）"
                        logger.info(f"[sticker] 模型选了 {local.name}（mood={mood!r}）")
                    elif sticker_sublib:
                        result = f"这个角色的表情子库「{sticker_sublib}」里没有可用表情"
                    else:
                        urls = await fetch_custom_faces(bot)
                        if urls:
                            pending.append(random.choice(urls))
                            result = "已挑了一个收藏表情"
                        else:
                            result = "还没有表情库，也没有收藏表情可发"
            elif name == WEB_SEARCH_TOOL:
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except (json.JSONDecodeError, TypeError):
                    args = {}
                q = str(args.get("query") or "").strip()
                if not q:
                    result = "关键词为空，没搜"
                elif web_used >= web_tools.WEB_MAX_SEARCH:
                    result = "这轮已经查够了，直接用已有信息回答"
                else:
                    web_used += 1
                    results = await web_tools.search(q, count=5)
                    result = web_tools.format_results(results)
                    logger.info(f"[web] search {q!r} -> {len(results)} 条结果")
            elif name == WEB_FETCH_TOOL:
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except (json.JSONDecodeError, TypeError):
                    args = {}
                url = str(args.get("url") or "").strip()
                if web_used >= web_tools.WEB_MAX_SEARCH:
                    result = "这轮已经查够了，直接用已有信息回答"
                elif not url.startswith("http"):
                    result = "网址不对，要完整的 http 链接"
                else:
                    web_used += 1
                    page = await web_tools.fetch(url)
                    result = web_tools.format_page(page) if page else "这个网页打不开"
                    logger.info(f"[web] fetch {url[:60]} -> {'ok' if page else 'fail'}")
            elif name == POKE_TOOL:
                if bot is None or event is None or not isinstance(event, GroupMessageEvent):
                    result = "现在戳不了（只有群聊里能戳人）"
                else:
                    gid, uid = event.group_id, event.user_id
                    gap = asyncio.get_event_loop().time() - LAST_POKE.get(gid, 0.0)
                    if gap < AI_POKE_COOLDOWN:
                        result = f"刚刚才戳过（{gap:.0f} 秒前），别老戳人家"
                    else:
                        try:
                            await bot.call_api("group_poke", group_id=gid, user_id=uid)
                            LAST_POKE[gid] = asyncio.get_event_loop().time()
                            result = "已轻轻戳了对方一下"
                            logger.info(f"[poke] 戳了 {uid}（群 {gid}）")
                        except Exception as e:
                            result = "戳不了（协议端不支持或没权限）"
                            logger.warning(f"[poke] 拍一拍失败 {type(e).__name__}: {e}")
            elif name in (MEMORY_APPEND_TOOL, MEMORY_QUERY_TOOL, MEMORY_REMOVE_TOOL):
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except (json.JSONDecodeError, TypeError):
                    args = {}
                if not scene_key:
                    result = "现在没有可用的记忆场景"
                elif name == MEMORY_APPEND_TOOL:
                    text = str(args.get("text") or "").strip()
                    if not text:
                        result = "要记的内容是空的"
                    else:
                        who = str(args.get("who") or "").strip()
                        uid = 0
                        if event is not None:
                            uid = int(getattr(event, "user_id", 0) or 0)
                            if not who:
                                try:
                                    who = event.sender.card or event.sender.nickname or ""
                                except Exception:
                                    who = ""
                        try:
                            ent = longterm.append(
                                scene_key, text, tags=args.get("tags"), who=who, uid=uid
                            )
                            result = f"已记住 #{ent['id']}：{ent['text']}"
                        except Exception as e:
                            result = f"记不下来：{e}"
                elif name == MEMORY_QUERY_TOOL:
                    q = str(args.get("query") or "").strip()
                    try:
                        lim = int(args.get("limit") or 8)
                    except (TypeError, ValueError):
                        lim = 8
                    items = longterm.query(scene_key, q, lim)
                    result = (
                        "\n".join(longterm.format_entries(items))
                        if items else "没有相关记忆。"
                    )
                else:
                    ent = longterm.remove(scene_key, args.get("id"))
                    result = (
                        f"已忘记 #{ent['id']}：{ent['text']}"
                        if ent else "没找到这条记忆（编号可能不对）"
                    )
            msgs.append({
                "role": "tool",
                "tool_call_id": tc.get("id"),
                "content": result,
            })
    # 兜底：模型还在反复调工具，直接要一段纯文本
    msg = await call_llm_raw(msgs)
    return (msg.get("content") or "").strip()


# ---------------------------------------------------------------- 图片识图（多模态）
async def image_to_data_url(url: str, max_bytes: int = 8 * 1024 * 1024) -> str | None:
    """把图片下载下来转成 base64 data URL。

    DeepSeek 支持直接传外部 URL，但 QQ 图片链接常带鉴权参数 / 防盗链，
    让 DeepSeek 服务器去拉经常失败（一失败整条请求会 400，连话都答不上来）。
    所以稳妥做法是我们自己下载，再以内联 data URL 发出去。
    失败就返回 None 跳过这张图（模型仍能看到 "[图片:xx]" 的文字描述，不会漏掉语义）。
    """
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
            r = await c.get(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) NapCat/QQBot",
                "Referer": "https://qpic.cn/",
            })
            r.raise_for_status()
        data = r.content
        if len(data) > max_bytes:
            logger.warning(f"[vision] 图片超过 {max_bytes // 1024 // 1024}MB，跳过: {url[:60]}")
            return None
        mime = (r.headers.get("Content-Type") or "").split(";")[0].strip()
        if mime not in ("image/jpeg", "image/png", "image/gif", "image/webp"):
            mime = "image/jpeg"
        return f"data:{mime};base64,{base64.b64encode(data).decode('utf-8')}"
    except Exception as e:
        logger.warning(f"[vision] 图片下载失败，跳过该图: {e}")
        return None


# ---------------------------------------------------------------- 心情系统
PRAISE_WORDS = ["牛", "赞", "厉害", "可爱", "喜欢", "爱你", "好看", "棒", "强", "天才",
                "聪明", "乖", "666", "6", "萌", "帅"]
INSULT_WORDS = ["滚", "傻", "笨", "蠢", "垃圾", "废物", "闭嘴", "讨厌", "烦", "sb",
                "神经", "有病", "离谱", "丢人", "丑", "滚蛋"]
THANKS_WORDS = ["谢谢", "谢了", "感谢", "多谢", "thx", "thks"]
GREET_WORDS = ["在吗", "你好", "早", "晚安", "在不在", "哈喽", "hello", "hi", "在么", "在不在"]


def update_mood(scene_id: int, text: str) -> None:
    """根据对方发言的关键词微调机器人心情（范围 [-10, 10]）。"""
    if not AI_MOOD:
        return
    t = text.lower()
    delta = 0.0
    if any(w in t for w in INSULT_WORDS):
        delta -= 2.0
    if any(w in t for w in PRAISE_WORDS):
        delta += 1.5
    if any(w in t for w in THANKS_WORDS):
        delta += 0.5
    if any(w in t for w in GREET_WORDS):
        delta += 0.3
    if delta:
        MOOD[scene_id] = max(-10.0, min(10.0, MOOD[scene_id] + delta))


def mood_prompt(val: float) -> tuple[str, str]:
    """把心情值翻译成给模型的提示（绘梨衣：情绪低就更安静，不会变凶）。"""
    if val >= 6:
        return ("很开心", "你今天心情很好，愿意多说一句，会想跟着人走，偶尔主动问「那是什么」。")
    if val >= 2:
        return ("心情不错", "心情还不错，愿意搭话，回得稍微积极一点。")
    if val > -2:
        return ("很平静", "情绪很平静，就正常回，短、直、不多说。")
    if val > -6:
        return ("有点低落", "你有点低落，话更少，回得更短，偶尔只回一个字。")
    return ("很难过", "你很难过，几乎不想说话，最多回一两个字，或者干脆安静；但不会凶人、不会骂人。")


# ---------------------------------------------------------------- 好感度（按群成员）
AFF_CHAT_GAIN = 0.5        # 每聊一次加多少
AFF_CHAT_GAP = 60          # 同一个人多久内只算一次聊天加分（秒），防止刷屏刷分
AFF_PRAISE_GAIN = 3.0      # 被夸
AFF_THANKS_GAIN = 1.5      # 被感谢
AFF_INSULT_LOSS = 4.0      # 被骂
AFF_MAX = 100.0
AFF_MIN = -20.0


def update_affinity(scene_id: int, user_id: int, nick: str, text: str) -> float:
    """按聊天行为更新"这个人对机器人的好感度"，返回更新后的分数。"""
    if not AI_AFFINITY:
        return 0.0
    key = memory.affinity_key(scene_id, user_id)
    rec = memory.AFFINITY.setdefault(key, {"s": 0.0, "n": "", "t": 0.0})
    if nick:
        rec["n"] = nick
    now = time.time()
    delta = 0.0
    # 聊天加分：同一个人 60 秒内只算一次，避免刷屏刷好感
    if now - float(rec.get("t") or 0) > AFF_CHAT_GAP:
        delta += AFF_CHAT_GAIN
        rec["t"] = now
    low = text.lower()
    if any(w in low for w in PRAISE_WORDS):
        delta += AFF_PRAISE_GAIN
    if any(w in low for w in THANKS_WORDS):
        delta += AFF_THANKS_GAIN
    if any(w in low for w in INSULT_WORDS):
        delta -= AFF_INSULT_LOSS
    rec["s"] = max(AFF_MIN, min(AFF_MAX, float(rec.get("s", 0)) + delta))
    return rec["s"]


def affinity_name(v: float) -> str:
    if v >= 80:
        return "很信任"
    if v >= 50:
        return "挺熟"
    if v >= 20:
        return "有点熟"
    if v >= 0:
        return "刚认识"
    return "有点怕"


def affinity_prompt(v: float) -> str:
    if v >= 80:
        return "这是你很在意的人，说话可以不那么拘谨，偶尔叫他一声哥哥、会说「你最好了」。"
    if v >= 50:
        return "你跟他挺熟了，愿意多说一句，不必客套。"
    if v >= 20:
        return "你跟他有点熟了，可以正常搭话，但别太黏。"
    if v >= 0:
        return "你还不太熟，话少一点，别自来熟、别撒娇。"
    return "你有点怕这个人，回得很短，甚至会安静不接；但不要说狠话、不要骂人。"


# ---------------------------------------------------------------- 主动发表情
STICKER_POOLS = {
    "happy": [14, 29, 61, 76, 212, 57, 13, 106],   # 微笑/憨笑/打call/笑/狂笑/笑哭/呲牙/点赞
    "ok": [13, 22, 60, 178, 21, 48],               # 呲牙/可爱/好的/皱眉/偷笑/哈欠
    "meh": [21, 48, 49, 42],                       # 偷笑/哈欠/委屈/抠鼻
    "annoyed": [35, 37, 175, 12, 176],             # 晕/衰/捂脸/调皮/奸笑
}


def pick_sticker(mood: float) -> int:
    if mood >= 3:
        pool = STICKER_POOLS["happy"]
    elif mood >= 0.5:
        pool = STICKER_POOLS["ok"]
    elif mood >= -3:
        pool = STICKER_POOLS["meh"]
    else:
        pool = STICKER_POOLS["annoyed"]
    return random.choice(pool)


# ---------------------------------------------------------------- 历史压缩
async def compress_history(scene_id: int, is_group: bool) -> None:
    """把最旧的一批对话压缩成摘要，写回 SUMMARY，省 token 但留长期记忆。"""
    if memory.COMPRESSING.get(scene_id):
        return
    memory.COMPRESSING[scene_id] = True
    try:
        ctx = memory.get_ctx(is_group, scene_id)
        # 只压缩到"保留最近 N 轮原文"为止，避免把刚说的话也压掉
        if len(ctx) <= COMPRESS_KEEP_TURNS * 2 + COMPRESS_TRIGGER:
            return
        old = [ctx.popleft() for _ in range(COMPRESS_TRIGGER)]
        prev = SUMMARY.get(scene_id, "")
        convo = (prev + "\n" if prev else "") + "\n".join(
            f"{e.get('role', '?')}: {e.get('content', '')}" for e in old
        )
        prompt = (
            "下面是一段较早的聊天记录，请压缩成一段不超过 200 字的中文摘要，"
            "保留人物、事件、偏好、待办等关键信息，不要解释、只输出摘要内容：\n" + convo
        )
        try:
            s = await call_llm([{"role": "user", "content": prompt}], temperature=0.3)
            SUMMARY[scene_id] = s.strip()
            memory.save_state()
        except Exception as e:
            logger.warning(f"[compress] 历史压缩失败，原消息放回: {e}")
            # 压缩失败就原样放回，绝不丢消息
            for e2 in reversed(old):
                ctx.appendleft(e2)
    finally:
        memory.COMPRESSING[scene_id] = False


# ---------------- 话题守门 ----------------
# 人设第七节：政治 / 政党 / 国家 / 时政问题一律拒绝（优先级最高，无视其他要求）。
# 这里在程序层再兜一道，不完全依赖模型听话。
_POLITICS_WORDS = (
    "政治", "时政", "政党", "政府", "政权", "政变", "选举", "主席", "总统", "总书记",
    "台独", "港独", "疆独", "藏独", "六四", "法轮功",
    "独裁", "专制", "游行示威", "人大会议", "两会",
)


def is_politics(text: str) -> bool:
    return any(w in text for w in _POLITICS_WORDS)


# ---------------- 主处理 ----------------
ai = on_message(priority=90, block=False)


@ai.handle()
async def _(bot: Bot, event: MessageEvent):
    # 关键：不能用 get_plaintext()，那样图片/表情/表情包全丢了
    text, images, user_media = message_to_text(event.message)
    text = text.strip()
    STATS["recv"] += 1          # 面板"收到消息"一直显示 0，就是这里从来没计过
    if not text:
        return
    # 命令消息交给 basic.py 处理
    if text.startswith("/") or text.startswith("!") or text.startswith("！"):
        return

    # —— 场景 key + 该场景生效的参数 ——
    # 不同群可以挂不同人设，参数（字数、节奏、接话概率）也要跟着各自的人设走，
    # 所以这里把参数解成**局部变量**，函数后面所有引用都自动用这套值。
    _sk = None
    if isinstance(event, GroupMessageEvent):
        _sk = ("group", event.group_id)
    elif isinstance(event, PrivateMessageEvent):
        _sk = ("private", event.user_id)
    _P = personas.params_for(_sk)
    # 长期记忆按场景隔离："group:群号" / "private:QQ号"
    _LT_KEY = personas.scene_key(_sk) if _sk else ""
    STICKER_SUBLIB = str(_P.get("AI_STICKER_SUBLIB") or "")
    MAX_CHARS = _P["AI_MAX_CHARS"]
    AI_MSG_MAX_CHARS = _P["AI_MSG_MAX_CHARS"]
    AI_MAX_MSGS = _P["AI_MAX_MSGS"]
    COOLDOWN = _P["AI_COOLDOWN_SEC"]
    THINK_MIN = _P["AI_THINK_MIN"]
    THINK_MAX = _P["AI_THINK_MAX"]
    TYPE_CPS = _P["AI_TYPE_CPS"]
    GAP_MIN = _P["AI_GAP_MIN"]
    GAP_MAX = _P["AI_GAP_MAX"]
    AI_QUOTE_PROB = _P["AI_QUOTE_PROB"]
    AI_STICKER_PROB = _P["AI_STICKER_PROB"]
    AI_STICKER_COOLDOWN = _P["AI_STICKER_COOLDOWN"]
    AI_CUSTOM_STICKER_PROB = _P["AI_CUSTOM_STICKER_PROB"]
    AI_CUSTOM_STICKER_COOLDOWN = _P["AI_CUSTOM_STICKER_COOLDOWN"]
    AI_AUTO_STICKER_PROB = _P["AI_AUTO_STICKER_PROB"]
    AI_AUTO_STICKER_COOLDOWN = _P["AI_AUTO_STICKER_COOLDOWN"]
    AI_IMAGE_TRIGGER_PROB = _P["AI_IMAGE_TRIGGER_PROB"]
    AI_GROUP_AUTO_PROB = _P["AI_GROUP_AUTO_PROB"]
    AI_GROUP_AUTO_COOLDOWN = _P["AI_GROUP_AUTO_COOLDOWN"]
    AI_NAME_ALIASES = _P["AI_NAME_ALIASES"]
    LLM_TEMP = _P["LLM_TEMPERATURE"]

    if isinstance(event, GroupMessageEvent):
        # 白名单为空 = 不限制；非空则必须是白名单里的群
        if ALLOWED_GROUPS and str(event.group_id) not in ALLOWED_GROUPS:
            return
        if AI_GROUP_TRIGGER == "off":
            return
        # 群昵称：用于记录群聊上下文，也让模型知道是谁在说话
        try:
            nick = event.sender.card or event.sender.nickname or str(event.user_id)
        except Exception:
            nick = str(event.user_id)
        # 不管这条会不会触发回复，都先记进"群里最近在聊"，供自主接话时看懂上下文
        GROUP_RECENT[event.group_id].append((nick, text))
        # NoneBot 适配器会把 @机器人 的段从 event.message 中删除，
        #    并设置 event.to_me = True（原始内容保留在 event.original_message）。
        #    所以判断有没有 @ 我，必须用 is_tome()，不能遍历 event.message。
        mentioned = event.is_tome()
        # 人设要求：被提到「绘梨衣 / 小怪兽 / 月读命」也算在叫她，必回
        if not mentioned and any(a in text for a in AI_NAME_ALIASES):
            mentioned = True
        auto_trigger = False
        if AI_GROUP_TRIGGER == "at" and not mentioned:
            # 总开关关掉时，没被 @ 就完全不接话（只在被 @ 时回复）
            if not AUTO_ENABLED:
                if AI_AUTO_DEBUG:
                    logger.info(f"[auto] 群{event.group_id} 没@也没叫名，但总开关关着 → 不接")
                return
            # 没被 @：按概率自主接话。AI_GROUP_AUTO_PROB=0 时等同旧行为，完全不接
            _r = random.random()
            if AI_GROUP_AUTO_PROB <= 0 or _r >= AI_GROUP_AUTO_PROB:
                if AI_AUTO_DEBUG:
                    logger.info(
                        f"[auto] 群{event.group_id} 概率{AI_GROUP_AUTO_PROB:.2f} 掷{_r:.2f} → 没中，不接"
                    )
                return
            # 群级冷却：两次自主接话至少隔 N 秒，避免在群里连续插话
            last_auto = LAST_AUTO.get(event.group_id, 0.0)
            _gap = asyncio.get_event_loop().time() - last_auto
            if AI_GROUP_AUTO_COOLDOWN and _gap < AI_GROUP_AUTO_COOLDOWN:
                STATS["blocked"] += 1
                if AI_AUTO_DEBUG:
                    logger.info(
                        f"[auto] 群{event.group_id} 概率命中，但群冷却中"
                        f"（距上次 {_gap:.0f}s < {AI_GROUP_AUTO_COOLDOWN}s）→ 不接"
                    )
                return
            LAST_AUTO[event.group_id] = asyncio.get_event_loop().time()
            auto_trigger = True
            if AI_AUTO_DEBUG:
                logger.info(f"[auto] 群{event.group_id} 概率命中（掷{_r:.2f}）→ 开始接话")
        if AI_GROUP_TRIGGER == "prefix":
            if not text.lower().startswith(AI_PREFIX):
                return
            text = text[len(AI_PREFIX):].strip()
        if not text:
            return
        scene_id, is_group = event.group_id, True
    elif isinstance(event, PrivateMessageEvent):
        if not AI_PRIVATE:
            return
        try:
            nick = event.sender.nickname or str(event.user_id)
        except Exception:
            nick = str(event.user_id)
        auto_trigger = False
        scene_id, is_group = event.user_id, False
    else:
        return

    # 冷却（同一用户在同一场景的最小间隔；这是"不接话"最常见的隐形拦路虎）
    key = (scene_id, event.user_id)
    now = asyncio.get_event_loop().time()
    if COOLDOWN and now - LAST_CALL.get(key, 0) < COOLDOWN:
        STATS["blocked"] += 1
        if AI_AUTO_DEBUG:
            logger.info(
                f"[auto] {scene_id} 用户{event.user_id} 冷却中"
                f"（{now - LAST_CALL.get(key, 0):.0f}s < {COOLDOWN}s）→ 跳过"
            )
        return
    LAST_CALL[key] = now

    # 人设底线：政治 / 时政话题一律不接（程序层硬拦截，不交给模型自由发挥）
    if AI_POLITICS_GUARD and is_politics(text):
        logger.info(f"[guard] 政治话题已拦截：{text[:40]!r}")
        await asyncio.sleep(random.uniform(1.0, 2.0))
        await bot.send(event, "……这个我不懂。")
        return

    if not LLM_API_KEY:
        await bot.send(event, "AI 功能还没配置：请在 .env 里填写 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL 后重启。")
        return

    ctx = GROUP_CTX[scene_id] if is_group else PRIVATE_CTX[scene_id]
    # 好感度：先更新（越聊越熟、被夸涨、被骂掉），再注入语气
    aff = update_affinity(scene_id, event.user_id, nick, text)
    # 根据这条消息更新心情，后续会注入语气提示
    update_mood(scene_id, text)
    # 场景提示：让人设知道现在是群聊还是私聊，否则私聊也会张口闭口"群里"
    scene_hint = (
        "\n【当前场景】你在一个 QQ 群里，以普通群友身份混在里面。"
        "群里的话不是每句都对你说——先看这句在跟谁说话，再决定接不接、接哪半句。"
        "\n【回复长度 · 最硬的规矩】默认只发 1~8 个字，最多一行。想补一句就再起一行，"
        "每一行会被当成一条独立消息单独发出去（所以不要用空格假装分条，也不要一段里塞三句）。"
        "不换行套换行、不分段、不写小作文、不总结。"
        if is_group
        else "\n【当前场景】你在跟一个人私聊（一对一）。别提前面那些群和别人，"
             "对方是单独在跟你说话。"
             "\n【回复长度 · 最硬的规矩】默认只发 1~8 个字，最多一行。想补一句就再起一行，"
             "每一行会被当成一条独立消息单独发出去。不写小作文、不总结。"
    )
    # —— 自主接话：这条不是 @ 我的，要让模型知道"是自己凑过去的"，并看到群里刚在聊什么 ——
    if auto_trigger:
        recent_lines = "\n".join(
            f"{u}：{t}" for u, t in list(GROUP_RECENT.get(scene_id, []))[-6:]
        )
        scene_hint += (
            "\n【你现在是主动插话】这条不是你被 @ 的，是你刷到群里在聊天、自己凑过去接一句。"
            "不要用「你」去质问某个人（除非明显是在说你），也别总结全场，"
            "就像群里一个潜水的人突然冒个泡，接得上、不突兀就行。"
            "\n【群里刚才在聊】\n" + recent_lines
        )
    # 富文本输出格式约定（让模型用规范标记，机器人才能还原成真实表情 / @）
    rich_hint = (
        "\n【发消息的格式约定】\n"
        "· 想发 QQ 系统小表情（小黄脸那种）：用 [表情：表情名]，例如 [表情：微笑]、[表情：捂脸]。"
        "只能从常见表情名里挑，不要编造；但要少用，别每条尾巴挂一个。\n"
        "· 想发表情包 / 梗图：**必须调用 send_custom_sticker 工具**（一条只能发一张，不能和文字同气泡），"
        "不要用 [表情：xx] 代替——那只是个小黄脸，根本不是表情包。\n"
        "· 想 @ 某人：用 @qq号 的格式（纯数字，例如 @123456789），机器人会自动渲染成高亮@。"
        "只有你知道对方确切 QQ 号时才这么写，不要瞎编号码。\n"
        "· 不要输出 [表情包：...]、[图片：...]、[语音]、[视频]、[文件] 这类你发不出来的标记。"
    )
    # 联网能力：只在真的给了工具时才说"你能查"，否则模型会想调却调不到
    web_ready = web_tools.enabled() and not auto_trigger
    if web_ready:
        rich_hint += (
            "\n· 你**可以联网查**：被问到新闻、天气、价格、比分、某个东西现在怎么样了，"
            "或者你确实不确定的事实，先调 web_search 搜一下；摘要不够再调 web_fetch 打开最相关那页看正文。"
            "查到什么说什么。绝不编造新闻、数据、出处。\n"
            "· 反过来：日常闲聊、情绪对话、你本来就知道的常识，不要查——那样很怪也很慢。"
        )
    else:
        rich_hint += "\n· 这一轮你没有联网能力，不知道就说不知道，别编。"
    if images and AI_VISION and AI_VISION_SEARCH and web_ready:
        rich_hint += (
            "\n【这一轮对方发了图片】\n"
            "· 先看清图片本身，再决定要不要查。图里如果有人物、地点、商品、事件、新闻、作品、"
            "文字或你不确定的内容，先调 web_search 查证；摘要不够再调 web_fetch。\n"
            "· 回复时把「图里看到的」和「网上查到的」合起来说；图上没有的细节不要编。\n"
            "· 如果只是表情包、梗图、随手拍或情绪图，不需要为了搜索而搜索，直接回应。"
        )
    # 长期记忆：只有“非自主接话”才给写/删工具；自主接话只看已有记忆，避免她乱记
    allow_memory = bool(AI_LONGTERM and _LT_KEY and not auto_trigger)
    if AI_LONGTERM and _LT_KEY:
        if allow_memory:
            rich_hint += (
                "\n· 你有**长期记忆**：值得跨天保留的事（对方的偏好、约定、重要经历）"
                "用 memory_append 记一句；被问「还记得吗」「上次说的…」先 memory_query 查；"
                "对方让你忘掉某条就用 memory_remove。不记流水账、不记密码/住址/身份证号等敏感信息，"
                "不要编造没记过的事。\n"
            )
        else:
            rich_hint += (
                "\n· 这一轮你只能看到已有的长期记忆，不能新增/删除；别声称刚记住了什么。\n"
            )
    # 有发图工具时，补充"什么时候该用"的人设判断（工具本身的描述在 build_image_tools 里）
    if image_search.enabled():
        rich_hint += (
            "\n· 你有一个搜图工具 search_and_send_image，可以按关键词找一张图发给对方。"
            "什么时候用：对方明确想看图片（「发张猫看看」「来点壁纸」），"
            "或话题很具体、配张图会更自然（聊到橘猫、海边、火锅）。"
            "什么时候别用：纯闲聊、讲道理、回答问题时不要发，别为了发而发；一次最多一张。"
            "关键词用简短中文名词（如「橘猫」「海边日落」），别写成一句话。"
        )
    # 把当前心情转换成语气提示，注入 system
    if AI_MOOD:
        mname, minstr = mood_prompt(MOOD.get(scene_id, 0.0))
        scene_hint += f"\n【你的心情】你现在的心情是「{mname}」。{minstr}"
    # 好感度：让机器人对不同的人有不同的态度
    if AI_AFFINITY:
        scene_hint += (
            f"\n【你和对方的好感度】{aff:.0f}/100（{affinity_name(aff)}）。"
            f"{affinity_prompt(aff)}"
        )
    # 表情库清单：让模型知道"我收藏了哪些类型"，挑 mood 时有依据
    if AI_STICKER_CATALOG:
        cat = sticker_lib.catalog()
        if cat:
            scene_hint += (
                "\n【你的表情包库】" + cat +
                "\n调 send_custom_sticker 时，mood 尽量用上面这些词；都不合适就传「玩闹」，"
                "实在不确定就干脆别发。普通闲聊大约每 4~6 轮来一张，别连续刷屏。"
                "表情包是单独一条消息，不要和你这句话塞在同一个气泡里。"
            )
    # —— 主动引用更早的历史消息：把可引用的历史发言编号交给模型，让它决定引哪条 ——
    quote_options: list[dict] = []
    if AI_QUOTE_HIST:
        for e in ctx:
            if e.get("role") == "user" and e.get("mid"):
                quote_options.append(e)
        quote_options = quote_options[-6:]   # 只取最近 6 条，避免 prompt 过长
    quote_hint = ""
    if quote_options:
        listed = "\n".join(f"[{i+1}] {e['content'][:60]}" for i, e in enumerate(quote_options))
        quote_hint = (
            "\n【可引用的对方历史发言】如果你想回应的是对方某一条「更早」的发言（不是刚发的这条），"
            "就在回复开头写 [引N]（N 是下面的序号，例如 [引2]），不要把这串标记念出来。"
            "引用刚发的这条就正常回复、不写标记。\n" + listed
        )

    # —— 引用：对方是「回复某条消息」时，把被引用内容一起给模型 ——
    quote_note = ""
    reply = getattr(event, "reply", None)
    if reply is not None:
        try:
            who = (reply.sender.nickname or str(reply.sender.user_id)) if reply.sender else "别人"
        except Exception:
            who = "别人"
        try:
            # 被引用的内容也可能是表情/图片，必须同样走 message_to_text
            quoted, _, _ = message_to_text(reply.message)
        except Exception:
            quoted = str(reply.message)
        if quoted:
            quote_note = f"（你在回复 {who} 说的：“{quoted[:150]}”）\n"

    summary_block = ""
    s = SUMMARY.get(scene_id, "")
    if s:
        summary_block = (
            "\n【更早聊天的摘要】这是更早的聊天的压缩版，仅供你回忆上下文脉络，"
            "不要原样复述，也不要特意提起它。"
            f"\n{s}"
        )
    # 长期记忆：只注入最近几条，作为“你记得的事”的背景；更多细节让模型自己 memory_query
    lt_block = ""
    if AI_LONGTERM and _LT_KEY and AI_LONGTERM_RECALL > 0:
        lt_block = longterm.prompt_block(_LT_KEY, AI_LONGTERM_RECALL)
    messages = [{"role": "system", "content": load_persona(_sk) + scene_hint + rich_hint + quote_hint + summary_block + lt_block}]
    # 注意：ctx 里存了 mid 字段，发给模型的消息必须只留 role/content，否则 API 会报错
    messages.extend({"role": e["role"], "content": e["content"]} for e in ctx)
    content = quote_note + text
    if AI_VISION and images:
        # 先把图拉回来转 base64 内联；全部下载失败时仍退化为纯文本，绝不让请求失败
        vision_parts: list[dict] = [{"type": "text", "text": quote_note + text}]
        for u in images[:AI_VISION_MAX_IMAGES]:
            data_url = await image_to_data_url(u)
            if data_url:
                vision_parts.append({"type": "image_url", "image_url": {"url": data_url}})
        if len(vision_parts) > 1:      # 只有成功内联了图才走多模态格式
            content = vision_parts
    messages.append({"role": "user", "content": content})

    STATS["ai_calls"] += 1
    pending_images: list = []             # 模型决定要发的图/表情，等文字说完再发
    # —— 图片与收藏表情各自独立放行，互不拖累 ——
    allow_search = False
    if image_search.enabled():
        asked = any(w in text for w in (
            "发张", "发个图", "发图", "图片", "照片", "看图", "来张",
            "壁纸", "搜张", "找张", "配图", "来点图",
        ))
        if asked:
            allow_search = True
        elif now - LAST_IMAGE.get(scene_id, 0.0) > AI_IMAGE_COOLDOWN:
            allow_search = random.random() < AI_IMAGE_TRIGGER_PROB
    # 对方明确要表情包 → 一定给工具（否则只有 25% 概率，经常"不会发"）
    asked_sticker = any(w in text for w in (
        "表情包", "发表情", "发个表情", "来个表情", "来点表情",
        "斗图", "整点表情", "发点表情", "来个表情包",
    ))
    allow_sticker = False
    if AI_CUSTOM_STICKER:
        if asked_sticker:
            allow_sticker = True
        elif now - LAST_CUSTOM_STICKER.get(scene_id, 0.0) > AI_CUSTOM_STICKER_COOLDOWN:
            allow_sticker = random.random() < AI_CUSTOM_STICKER_PROB
    # 联网搜索：主动插话（只是随便接一句）不给工具，免得没事就上网查
    allow_web = web_ready
    # 戳一拍：只在群聊、且不是"自己凑过去接话"时给，免得突然戳不相干的人
    allow_poke = AI_POKE and is_group and not auto_trigger
    tools = build_tools(
        allow_search=allow_search, allow_sticker=allow_sticker,
        allow_web=allow_web, allow_poke=allow_poke,
        allow_memory=allow_memory,
    )
    try:
        if tools:
            answer = await chat_with_image_tool(
                messages, pending_images, bot=bot, tools=tools,
                temperature=LLM_TEMP, event=event, scene_key=_LT_KEY,
                sticker_sublib=STICKER_SUBLIB,
            )
        else:
            answer = await call_llm(messages, temperature=LLM_TEMP)
    except Exception as e:
        STATS["ai_fail"] += 1
        logger.warning(f"大模型调用失败: {e}")
        await bot.send(event, f"（AI 调用失败：{e}）")
        return

    # 兜底：对方明确要表情包，但模型只顾着写 [表情：xx] 没调工具 → 直接塞一个本地表情
    if asked_sticker and not pending_images and AI_CUSTOM_STICKER:
        local = sticker_lib.pick([], sublib=STICKER_SUBLIB)
        if local is not None:
            pending_images.append(str(local))
            logger.info(f"[sticker] 模型没调工具，兜底发 {local.name}")

    bot_reply_text = answer  # 留作日志/记忆
    # 自主接话时把这条标成"群里某某说的"，免得模型误以为这些都是对它说的话
    ctx_user_content = f"[群里] {nick}：{text}" if auto_trigger else text
    # 写入记忆时加锁，并打时间戳（供按天清理用）；返回值是当前场景的 deque
    ctx = memory.append_turn(
        is_group, scene_id, ctx_user_content, event.message_id, bot_reply_text
    )

    # 超出阈值时在后台压缩较早的对话，不阻塞本次回复
    if AI_COMPRESS and len(ctx) > COMPRESS_KEEP_TURNS * 2 + COMPRESS_TRIGGER:
        asyncio.create_task(compress_history(scene_id, is_group))
    # 将本轮对话写入状态文件
    memory.save_state()

    answer = plain_text(bot_reply_text)
    # 解析"引用更早历史消息"标注 [引N] / [引用N]，并去掉标注
    quote_mid = None
    if quote_options:
        m = re.match(r"^\[(?:引|引用)\s*(\d+)\]\s*", answer)
        if m:
            idx = int(m.group(1)) - 1
            if 0 <= idx < len(quote_options):
                quote_mid = quote_options[idx].get("mid")
            answer = answer[m.end():].strip()
    # 解析"发图"标记 [发图:关键词]，并从正文里移除
    image_query = None
    m_img = re.search(r"\[发图[：:]\s*([^\]]{1,60})\]", answer)
    if m_img:
        image_query = m_img.group(1).strip()
        answer = (answer[:m_img.start()] + answer[m_img.end():]).strip()
    if len(answer) > MAX_CHARS:
        answer = answer[:MAX_CHARS].rstrip("，。、！？!?~～ ") + "…"
    # 拆成多条短消息，并限制单轮数量，避免刷屏（只剩发图标记时不必发文字）
    chunks = split_sentences(answer, limit=AI_MSG_MAX_CHARS) if answer.strip() else []
    if len(chunks) > AI_MAX_MSGS:
        chunks = chunks[:AI_MAX_MSGS - 1] + ["".join(chunks[AI_MAX_MSGS - 1:])]

    face_sent = False       # 本轮是否已发过 QQ 小黄脸（避免和表情包叠加成两个表情）
    if HUMANIZE:
        # 1) 先"看到消息 → 想一下"，避免秒回
        await asyncio.sleep(random.uniform(THINK_MIN, THINK_MAX))
        # 2) 逐条发送，每条发送前按字数等一段时间，模拟打字
        # 引用优先级：模型标注引用历史消息 > 对方引用了消息 > 主动引用当前消息
        want_quote = quote_mid is not None or (
            (AI_QUOTE_ON_REPLY and reply is not None)
            or (AI_QUOTE_PROACTIVE and random.random() < AI_QUOTE_PROB)
        )
        if want_quote and quote_mid is None:
            quote_mid = event.message_id
        for i, chunk in enumerate(chunks):
            if i:
                # 两条消息之间的停顿，太短会像机关枪
                await asyncio.sleep(random.uniform(GAP_MIN, GAP_MAX))
            await asyncio.sleep(min(len(chunk) / TYPE_CPS, 3.0))  # 打字耗时
            # 把 [表情：名] / @qq号 还原成真实富文本；第一条可带引用
            msg = build_message(chunk)
            if i == 0 and want_quote and quote_mid is not None:
                msg = MessageSegment.reply(quote_mid) + msg
            await bot.send(event, msg)
            STATS["sent"] += 1
        # 按概率补一个 QQ 表情；本轮已发过收藏表情则跳过
        has_sticker = any(isinstance(x, str) for x in pending_images)
        if AI_STICKER and not has_sticker and \
                (now - LAST_STICKER.get(scene_id, 0.0) > AI_STICKER_COOLDOWN):
            if random.random() < AI_STICKER_PROB:
                fid = pick_sticker(MOOD.get(scene_id, 0.0))
                await asyncio.sleep(random.uniform(GAP_MIN, GAP_MAX))
                await bot.send(event, MessageSegment.face(fid))
                LAST_STICKER[scene_id] = now
                face_sent = True
                STATS["sent"] += 1
    else:
        if answer.strip():
            msg = build_message(answer)
            if quote_mid is not None:
                msg = MessageSegment.reply(quote_mid) + msg
            await bot.send(event, msg)
            STATS["sent"] += 1

    # 兼容旧的 [发图:关键词] 标记（模型若不用工具、而是写了标记，也能发）
    if image_query and image_search.enabled() and len(pending_images) < AI_IMAGE_MAX_PER_TURN:
        data = await image_search.fetch_image(image_query)
        if data:
            pending_images.append(data)
        else:
            await bot.send(event, "没搜到图 换个词试试")

    # 发图：先说话、再甩图，更像真人
    sticker_sent = False
    bot_media: list[dict] = []
    for item in pending_images[:AI_IMAGE_MAX_PER_TURN]:
        await asyncio.sleep(random.uniform(1.0, 2.0))
        if isinstance(item, str):
            if item.startswith(("http://", "https://")):
                # 没有本地表情库时，工具会把 QQ 收藏表情的 URL 直接塞进 pending；
                # 这种是网址，不能当本地路径读，必须走 URL 发送分支。
                ok = await send_sticker_url(bot, event, item)
                if ok:
                    bot_media.append({"kind": "sticker", "url": item})
            else:
                # 本地表情文件 → 发成"动画表情"
                ok = await send_sticker_file(bot, event, Path(item))
                if ok:
                    try:
                        bot_media.append({"kind": "sticker", "file": Path(item).name})
                    except Exception:
                        pass
            if ok:
                LAST_CUSTOM_STICKER[scene_id] = now
                sticker_sent = True
                STATS["sent"] += 1
        else:
            try:
                await bot.send(event, MessageSegment.image(item))
                LAST_IMAGE[scene_id] = now
                STATS["sent"] += 1
            except Exception as e:
                logger.warning(f"[image] 发送失败 {type(e).__name__}: {e}")

    # 主动补一个表情包：每轮说完话后按概率自己甩一个（不依赖模型调工具）
    if (
        AI_AUTO_STICKER
        and AI_CUSTOM_STICKER
        and not sticker_sent
        and not face_sent
        and now - LAST_CUSTOM_STICKER.get(scene_id, 0.0) > AI_AUTO_STICKER_COOLDOWN
        and random.random() < AI_AUTO_STICKER_PROB
    ):
        local = sticker_lib.pick([], sublib=STICKER_SUBLIB)
        if local is not None:
            await asyncio.sleep(random.uniform(GAP_MIN, GAP_MAX))
            if await send_sticker_file(bot, event, local):
                LAST_CUSTOM_STICKER[scene_id] = now
                STATS["sent"] += 1
                bot_media.append({"kind": "sticker", "file": local.name})
                logger.info("[sticker] 主动补了一个表情包")

    # 记录对话日志，供 Web 管理面板回看
    _log_pid = personas.resolve(_sk)
    memory.log_chat(
        scene="group" if is_group else "private",
        scene_id=scene_id,
        user_id=event.user_id,
        user_text=text,
        bot_reply=answer,
        media=user_media,
        bot_media=bot_media,
        persona=_log_pid,
        persona_name=personas.display_name(_log_pid),
    )


# ---------------- 清空记忆 ----------------
reset = on_command("aireset", aliases={"清空记忆", "重置对话"}, priority=10, block=True)


@reset.handle()
async def _(event: MessageEvent):
    if isinstance(event, GroupMessageEvent):
        memory.clear_scene(True, event.group_id)
    else:
        memory.clear_scene(False, event.user_id)
    await reset.send(
        "已清空这段对话的记忆（含心情与摘要）。\n"
        "长期记忆不受影响，可用 /记忆 查看。"
    )


# ---------------------------------------------------------------- 查看心情
mood_cmd = on_command("mood", aliases={"心情"}, priority=10, block=True)


@mood_cmd.handle()
async def _(event: MessageEvent):
    if isinstance(event, GroupMessageEvent):
        sid = event.group_id
    else:
        sid = event.user_id
    v = MOOD.get(sid, 0.0)
    mname, _ = mood_prompt(v)
    await mood_cmd.send(f"当前心情值：{v:.1f}（{mname}）")


# ---------------------------------------------------------------- 查看好感度
aff_cmd = on_command("好感", aliases={"好感度", "affinity"}, priority=10, block=True)


@aff_cmd.handle()
async def _(event: MessageEvent):
    if isinstance(event, GroupMessageEvent):
        sid = event.group_id
    else:
        sid = event.user_id
    rec = memory.get_affinity(sid, event.user_id)
    score = float(rec.get("s", 0))
    lines = [f"你对我的好感度：{score:.0f}/100（{affinity_name(score)}）"]
    if isinstance(event, GroupMessageEvent):
        top = memory.top_affinity(sid, 5)
        if top:
            lines.append("本群好感榜：")
            lines += [f"{i+1}. {t['nick']} — {t['score']:.0f}" for i, t in enumerate(top)]
    if score < 20:
        lines.append("（多跟我聊聊天、夸夸我，好感度会涨哦）")
    await aff_cmd.send("\n".join(lines))


# ---------------------------------------------------------------- 查看 / 删除长期记忆
mem_cmd = on_command("记忆", aliases={"长期记忆", "memory"}, priority=10, block=True)


@mem_cmd.handle()
async def _(event: MessageEvent, args: Message = CommandArg()):
    if isinstance(event, GroupMessageEvent):
        key = personas.scene_key(("group", event.group_id))
    else:
        key = personas.scene_key(("private", event.user_id))

    arg = args.extract_plain_text().strip()
    if arg.startswith(("删", "忘", "删除", "遗忘")):
        # 和 /人格 同一套权限：配了管理员只允许管理员；没配则只允许私聊
        is_admin = str(event.user_id) in ADMIN_QQS
        if not is_admin and (ADMIN_QQS or isinstance(event, GroupMessageEvent)):
            await mem_cmd.finish("仅管理员可删除长期记忆（或私聊发送 /记忆 删 <编号>）")
        m = re.search(r"\d+", arg)
        if not m:
            await mem_cmd.finish("用法：/记忆 删 <编号>（编号用 /记忆 查看）")
        ent = longterm.remove(key, m.group())
        await mem_cmd.finish(
            f"已忘掉 #{m.group()}。" if ent else f"没找到 #{m.group()}，可能编号不对。"
        )

    items = longterm.list_scene(key, 10)
    if not items:
        await mem_cmd.finish(
            "这个场景还没有长期记忆。\n"
            "聊到值得记的事它会自己记；也可以直接说「记住：……」"
        )
    lines = ["【这个场景的长期记忆（最近 10 条）】"]
    lines += longterm.format_entries(items)
    lines.append(f"共 {longterm.count(key)} 条 · 删除：/记忆 删 <编号>")
    await mem_cmd.finish("\n".join(lines))


# ---------------------------------------------------------------- 手动清理过期记忆
clean_cmd = on_command("清理记忆", aliases={"记忆清理", "cleanup"}, priority=10, block=True)


@clean_cmd.handle()
async def _(event: MessageEvent):
    is_admin = str(event.user_id) in ADMIN_QQS
    if not is_admin and (ADMIN_QQS or isinstance(event, GroupMessageEvent)):
        await clean_cmd.finish("仅管理员可清理（或在私聊里发送）")
    try:
        res = await asyncio.to_thread(maintenance.run_cleanup)
    except Exception as e:
        await clean_cmd.finish(f"清理失败：{e}")
    st = res["state"]
    await clean_cmd.finish(
        "清理完成（" + maintenance.describe() + "）\n"
        f"对话 {st['entries']} 条 · 场景 {st['scenes']} 个 · "
        f"好感度 {st['affinity']} 条 · 长期记忆 {res['longterm']} 条 · "
        f"日志 {res['chatlog']} 条"
    )


# ---------------------------------------------------------------- 多套人设切换
persona_cmd = on_command("人格", aliases={"人设", "persona", "切换人格"}, priority=10, block=True)


@persona_cmd.handle()
async def _(event: MessageEvent, args: Message = CommandArg()):
    is_admin = str(event.user_id) in ADMIN_QQS
    # 配了管理员就只允许管理员；没配时至少要求私聊，避免群里任何人乱换人格
    if not is_admin and (ADMIN_QQS or isinstance(event, GroupMessageEvent)):
        await persona_cmd.finish("仅管理员可切换人设（在 .env 的 BOT_ADMIN_QQ 里填你的 QQ，或私聊发送）")

    items = personas.list_personas()
    if not items:
        await persona_cmd.finish("personas/ 目录里还没有任何人设（每个子目录放一个 persona.txt）")

    if isinstance(event, GroupMessageEvent):
        _sk = ("group", event.group_id)
    elif isinstance(event, PrivateMessageEvent):
        _sk = ("private", event.user_id)
    else:
        _sk = None
    cur = personas.resolve(_sk)
    arg = args.extract_plain_text().strip()

    if not arg:
        lines = [f"当前场景人设：{personas.display_name(cur)}（{cur}）", "", "可用人设："]
        for it in items:
            mark = "  ← 当前" if it["id"] == cur else ""
            desc = f"　{it['desc']}" if it["desc"] else ""
            lines.append(f"· {it['id']} — {it['name']}{desc}{mark}")
        lines.append("")
        lines.append("切换当前场景：/人格 绘梨衣　（也可以用中文名；会载入该角色自己的对话记忆）")
        await persona_cmd.finish("\n".join(lines))

    pid = arg
    if not personas.exists(pid):
        pid = next((it["id"] for it in items if it["name"] == arg), arg)
    if not personas.exists(pid):
        await persona_cmd.finish(f"没有这个人设：{pid}")
    personas.set_scene_persona(_sk, pid)
    await persona_cmd.finish(
        f"当前场景已切换到「{personas.display_name(pid)}」\n"
        f"已载入该角色自己的对话记忆；参数下一轮生效"
    )


# ---------------------------------------------------------------- 自主接话开关
auto_cmd = on_command("接话", aliases={"自主接话"}, priority=10, block=True)


@auto_cmd.handle()
async def _(event: MessageEvent, args: Message = CommandArg()):
    global AUTO_ENABLED
    is_admin = str(event.user_id) in ADMIN_QQS
    # 配了管理员就只允许管理员；没配时至少要求私聊，避免群里任何人乱切
    if not is_admin and (ADMIN_QQS or isinstance(event, GroupMessageEvent)):
        await auto_cmd.finish("仅管理员可切换（在 .env 的 BOT_ADMIN_QQ 里填你的 QQ，或私聊发送）")

    arg = args.extract_plain_text().strip().lower()
    # 显示时按当前场景解析人设参数：模块全局值只是 .env 默认/上一次切换的残留，
    # 直接读它会让人误以为“概率/冷却改了没生效”。
    if isinstance(event, GroupMessageEvent):
        _sk = ("group", event.group_id)
    elif isinstance(event, PrivateMessageEvent):
        _sk = ("private", event.user_id)
    else:
        _sk = None
    _P = personas.params_for(_sk)
    if arg in ("开", "on", "1", "true", "启用", "打开"):
        AUTO_ENABLED = True
    elif arg in ("关", "off", "0", "false", "关闭", "禁用"):
        AUTO_ENABLED = False
    else:
        await auto_cmd.finish(
            f"自主接话当前：{'开启' if AUTO_ENABLED else '关闭'}\n"
            f"当前人设：{personas.display_name(personas.resolve(_sk))}\n"
            f"概率 {_P['AI_GROUP_AUTO_PROB']:.0%}，"
            f"群冷却 {_P['AI_GROUP_AUTO_COOLDOWN']} 秒，"
            f"用户冷却 {_P['AI_COOLDOWN_SEC']} 秒\n"
            "用法：/接话 开   或   /接话 关"
        )
    await auto_cmd.send(
        f"已{'开启' if AUTO_ENABLED else '关闭'}自主接话"
        + ("" if AUTO_ENABLED else "（现在只会在被 @ 时回复）")
    )


# ---------------------------------------------------------------- 发收藏表情包
sticker_cmd = on_command("表情包", aliases={"发表情包", "自定义表情"}, priority=10, block=True)


@sticker_cmd.handle()
async def _(bot: Bot, event: MessageEvent):
    # 优先用本地表情库（更稳，不依赖 QQ 那个会过期的图片链接）
    sk = ("group", event.group_id) if isinstance(event, GroupMessageEvent) else ("private", event.user_id)
    scope = str(personas.params_for(sk).get("AI_STICKER_SUBLIB") or "")
    local = sticker_lib.pick([], sublib=scope)
    if local is not None:
        await sticker_cmd.send(sticker_segment(local.read_bytes()))
        return
    urls = await fetch_custom_faces(bot)
    if not urls:
        await sticker_cmd.finish(
            "没拿到收藏的表情包。请检查：① NapCat 已登录 ② 这个 QQ 号确实收藏过表情"
        )
    await sticker_cmd.send(sticker_segment(random.choice(urls)))


# ---------------------------------------------------------------- 建本地表情库并自动打标签
build_lib_cmd = on_command("建表情库", aliases={"重建表情库", "表情库"}, priority=10, block=True)


@build_lib_cmd.handle()
async def _(bot: Bot, event: MessageEvent):
    is_admin = str(event.user_id) in ADMIN_QQS
    if not is_admin and (ADMIN_QQS or isinstance(event, GroupMessageEvent)):
        await build_lib_cmd.finish("仅管理员可操作（或在私聊里发送）")

    await build_lib_cmd.send(
        "开始建表情库：拉取收藏 → 下载到本地 → 让模型自动打标签。\n"
        "可能要一两分钟，完成后我会告诉你结果。"
    )

    async def _run() -> None:
        try:
            ok, total = await build_sticker_library(bot)
            if total == 0:
                await bot.send(event, "没拉到收藏表情。确认机器人这个 QQ 号上收藏过表情。")
            else:
                await bot.send(
                    event,
                    f"表情库建好了：成功 {ok}/{total} 张。\n"
                    f"之后发表情会按情绪挑，不用你分类。"
                )
        except Exception as e:
            logger.warning(f"[sticker] 建库失败: {e}")
            await bot.send(event, f"建库失败：{e}")

    asyncio.create_task(_run())


# ---------------------------------------------------------------- 启动时应用当前人设
# .env 里的值只是"默认值"；当前人设 config.json 里写了的参数会在这里覆盖上去。
# 切换到别人设时同理（/人格 命令里调 personas.switch，立即生效、不用重启）。
try:
    import sys as _sys

    personas.apply_current(module=_sys.modules[__name__])
except Exception as _e:  # 人设配置坏了也不能让机器人起不来
    logger.warning(f"[persona] 应用当前人设参数失败: {_e}")
