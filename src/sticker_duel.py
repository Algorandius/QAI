"""斗图模式 + 自动收藏表情包。

两件事：
1. 自动收藏——群里/私聊里看到别人发的表情包，下载、打标签、进本地库。
   有频率上限，不会把整个群的图都扫一遍。
2. 斗图模式——`/开启斗图模式` 后，这个群里有人发表情包/图片，
   机器人也回一张，不调模型、不花钱，纯图对战。

设计原则：
  · 所有网络请求都有超时和失败降级，不影响正常聊天
  · 斗图有冷却和条数上限，避免刷屏被群管踢
  · 只收自己看得懂的格式，非图片一律丢弃
"""
from __future__ import annotations

import asyncio
import random
import time
from collections import deque
from pathlib import Path

import httpx
from nonebot import logger

from src import sticker_lib
from src.config import env_bool, env_int

# 自动收藏
STICKER_AUTO_COLLECT = env_bool("STICKER_AUTO_COLLECT", True)
STICKER_COLLECT_MIN_BYTES = 1024              # 太小的多半是占位图/失败响应
STICKER_COLLECT_MAX_BYTES = 6 * 1024 * 1024   # 太大的发不出去，也占空间
STICKER_COLLECT_COOLDOWN = 3.0                # 两次收藏之间的最小间隔（秒）
STICKER_COLLECT_MAX_PER_HOUR = max(1, env_int("STICKER_COLLECT_MAX_PER_HOUR", 60))

# 斗图
DUEL_COOLDOWN = 1.2            # 每次回图的最小间隔，别变成机关枪
DUEL_GAP = (0.6, 1.4)          # 一轮里两张图之间的停顿
DUEL_MAX_PER_ROUND = max(1, env_int("STICKER_DUEL_MAX", 3))
DUEL_SESSION_TIMEOUT = 30 * 60  # 半小时没动静自动结束

# 运行状态（进程内，不落盘：重启后斗图模式自动关闭，避免意外刷屏）
_duel_on: set[int] = set()             # 开启了斗图的场景 id
_duel_last: dict[int, float] = {}      # 各场景最后一次斗图时间
# 各场景最近发过的表情（文件名队列），用于"别连着发同一张"
_duel_recent: dict[int, deque[str]] = {}
_duel_seen: dict[int, set[str]] = {}   # 与 recent 同步的集合，方便 O(1) 判断
DUEL_RECENT_KEEP = 12                  # 记住最近 12 张，够多了
_collect_last = [0.0]                  # 上次收藏时间
_collect_count = [0, 0.0]              # [次数, 本小时起始时间]

_IMG_MAGIC = (
    (b"\xff\xd8\xff", "jpg"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
)


def sniff_ext(data: bytes) -> str:
    """用文件头判断扩展名；不是图片返回空串。"""
    if not data:
        return ""
    for magic, ext in _IMG_MAGIC:
        if data.startswith(magic):
            return ext
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return ""


# ---------------------------------------------------------------- 斗图开关
def duel_enabled(scene_id: int) -> bool:
    """这个场景是否开着斗图。超过闲置时间的自动关掉。"""
    if scene_id not in _duel_on:
        return False
    # 注意不能写 `if last and ...`：last 可能是 0.0（测试或时钟异常），
    # 那样会跳过过期判断，模式永远关不掉。
    last = _duel_last.get(scene_id)
    if last is not None and time.time() - last > DUEL_SESSION_TIMEOUT:
        _duel_on.discard(scene_id)
        _duel_last.pop(scene_id, None)
        logger.info(f"[duel] 场景 {scene_id} 长时间无互动，斗图模式已自动关闭")
        return False
    return True


def duel_start(scene_id: int) -> None:
    _duel_on.add(scene_id)
    _duel_last[scene_id] = time.time()


def duel_stop(scene_id: int) -> None:
    _duel_on.discard(scene_id)
    _duel_last.pop(scene_id, None)


def duel_ready(scene_id: int) -> bool:
    """冷却是否已过，可以回一张了。"""
    return time.time() - _duel_last.get(scene_id, 0.0) >= DUEL_COOLDOWN


def duel_touch(scene_id: int) -> None:
    _duel_last[scene_id] = time.time()


# ---------------------------------------------------------------- 自动收藏
def _collect_allowed(now: float) -> bool:
    """频率闸门：太频繁、或一小时配额用完，就别收了。"""
    if not STICKER_AUTO_COLLECT:
        return False
    if now - _collect_last[0] < STICKER_COLLECT_COOLDOWN:
        return False
    if now - _collect_count[1] > 3600:
        _collect_count[0] = 0
        _collect_count[1] = now
    if _collect_count[0] >= STICKER_COLLECT_MAX_PER_HOUR:
        return False
    return True


async def collect_from_urls(urls: list[str]) -> int:
    """把一组图片 URL 收进本地表情库，返回新增数量。

    自动收藏和手动 /收图 共用；已收过的 URL 直接跳过。
    """
    if not urls:
        return 0
    added = 0
    for url in urls:
        now = time.time()
        if not _collect_allowed(now):
            break
        if not str(url or "").startswith("http"):
            continue
        if sticker_lib.has_url(url):
            continue
        try:
            async with httpx.AsyncClient(
                trust_env=False, timeout=20, follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) QQBot/1.0"},
            ) as c:
                r = await c.get(url)
            if r.status_code != 200:
                continue
            data = r.content
        except Exception as e:
            logger.debug(f"[collect] 下载失败 {type(e).__name__}: {str(e)[:80]}")
            continue

        if not (STICKER_COLLECT_MIN_BYTES <= len(data) <= STICKER_COLLECT_MAX_BYTES):
            continue
        ext = sniff_ext(data)
        if not ext:
            logger.debug(f"[collect] 不是图片，跳过：{data[:60]!r}")
            continue

        path = sticker_lib.file_for(url, ext)
        try:
            path.write_bytes(data)
        except Exception as e:
            logger.warning(f"[collect] 写文件失败: {e}")
            continue
        sticker_lib.add_item(path.name, [], url=url)
        added += 1
        _collect_last[0] = now
        _collect_count[0] += 1
    if added:
        logger.info(f"[collect] 自动收藏 {added} 张表情（本次共 {len(urls)} 个候选）")
    return added


async def collect_background(urls: list[str]) -> None:
    """后台收图：不阻塞消息处理。"""
    try:
        await collect_from_urls(urls)
    except Exception as e:
        logger.debug(f"[collect] 后台收藏异常: {type(e).__name__}: {e}")


def note_sent(scene_id: int, filename: str) -> None:
    """记下这一轮发了哪张，供下一轮避开。"""
    if not filename:
        return
    dq = _duel_recent.get(scene_id)
    if dq is None:
        dq = deque(maxlen=_recent_keep())
        _duel_recent[scene_id] = dq
        _duel_seen[scene_id] = set()
    dq.append(filename)
    _duel_seen[scene_id] = set(dq)


def _recent_keep() -> int:
    """「最近发过」要记几条。

    不能固定成 12：库只有 8 张时，12 条记录会把整个库排空，
    下一轮就只能退化成"随便发一张"，于是又重复。
    取「库容量的三分之一」，且至少留 3 张、最多 12 条，
    保证总有几张是"最近没发过的"。
    """
    total = sticker_lib.count()
    if total <= 1:
        return 0
    keep = total // 3
    return max(3, min(keep, DUEL_RECENT_KEEP))


def recent_sent(scene_id: int) -> set[str]:
    return set(_duel_recent.get(scene_id) or ())


def reset_recent(scene_id: int) -> None:
    _duel_recent.pop(scene_id, None)
    _duel_seen.pop(scene_id, None)


# ---------------------------------------------------------------- 斗图出图
def pick_duel_sticker(scene_id: int, sent: set[str] | None = None) -> Path | None:
    """斗图挑图：这一轮绝不重复，跨轮尽量不重复。

    优先级：
      1. 不在「本轮已发 + 最近 N 张」里 —— 最佳
      2. 不在「本轮已发」里 —— 库小时退一步，但本轮仍然不重复
      3. 返回 None —— 库比本轮要的还少，宁可少发也不重样
    """
    sent = sent or set()
    path = sticker_lib.pick_excluding(sent | recent_sent(scene_id))
    if path:
        return path
    return sticker_lib.pick_excluding(sent)


def duel_round_size() -> int:
    """这一轮回几张。

    上限受库容量约束：库很小的时候一次只回一张，
    否则必然要在同一轮里重复（或者被逼着跨轮重复）。
    """
    total = sticker_lib.count()
    if total <= 0:
        return 1
    # 最多回库里的 1/4，但不超过配置上限，至少 1 张
    cap = max(1, min(DUEL_MAX_PER_ROUND, total // 4 or 1))
    return random.randint(1, cap)
