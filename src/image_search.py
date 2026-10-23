"""
图片搜索。由 AI_IMAGE_SOURCE 选择图源：

- 360（默认）：免 Key，中文关键词可用，适合自用/娱乐；图片版权需自行确认
- key：仅用 Pexels / Pixabay / Unsplash，需要注册 Key
- auto：优先 360，失败后尝试 key 源
"""
from __future__ import annotations

import random
import time

import httpx
from nonebot import logger

from src.config import env, env_bool, env_int

# 图库 Key。Pexels 已不再发放新 Key，Pixabay 仍可正常申请。
PEXELS_KEY = env("PEXELS_API_KEY", "").strip()
PIXABAY_KEY = env("PIXABAY_API_KEY", "").strip()
UNSPLASH_KEY = env("UNSPLASH_ACCESS_KEY", "").strip()
IMAGE_SEND = env_bool("AI_IMAGE_SEND", True)
IMAGE_SOURCE = env("AI_IMAGE_SOURCE", "360").strip().lower()   # 360 / key / auto
IMG_MAX_BYTES = max(1, env_int("AI_IMAGE_MAX_MB", 10)) * 1024 * 1024
IMG_TIMEOUT = 25

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) QQBot/1.0"}

# 简易敏感词过滤：避免被拿来搜不当内容（图库自身也有 safesearch，双保险）
_BLOCK_WORDS = (
    "nude", "naked", "sex", "porn", "xxx", "hentai", "nsfw",
    "gore", "blood", "violence", "色情", "裸", "血腥", "暴力",
)

# 常见中文关键词 → 英文（图库对英文支持最好；模型偶尔仍会给中文，这里兜底）
_CN2EN = {
    "猫": "cat", "猫猫": "cat", "小猫": "kitten", "狗": "dog", "狗狗": "dog", "小狗": "puppy",
    "兔子": "rabbit", "鸟": "bird", "熊猫": "panda", "老虎": "tiger", "狮子": "lion",
    "大象": "elephant", "猴子": "monkey", "企鹅": "penguin", "海豚": "dolphin", "鲸鱼": "whale",
    "风景": "landscape", "山": "mountain", "海": "ocean", "大海": "ocean", "花": "flower",
    "天空": "sky", "森林": "forest", "草原": "grassland", "沙漠": "desert", "瀑布": "waterfall",
    "城市": "city", "夜晚": "night", "星空": "starry sky", "银河": "galaxy", "极光": "aurora",
    "月亮": "moon", "太阳": "sun", "彩虹": "rainbow", "雪": "snow", "雨": "rain",
    "食物": "food", "美食": "food", "咖啡": "coffee", "蛋糕": "cake", "披萨": "pizza",
    "汉堡": "burger", "奶茶": "bubble tea", "水果": "fruit", "草莓": "strawberry",
    "西瓜": "watermelon", "冰淇淋": "ice cream", "火锅": "hotpot", "面条": "noodles",
    "汽车": "car", "跑车": "sports car", "飞机": "airplane", "火车": "train", "摩托车": "motorcycle",
    "动漫": "anime", "女孩": "girl", "男孩": "boy", "可爱": "cute", "搞笑": "funny",
    "城堡": "castle", "桥": "bridge", "书": "book", "篮球": "basketball", "足球": "football",
    "游戏": "game", "电脑": "computer", "手机": "phone", "机器人": "robot",
    "宇宙": "space", "地球": "earth", "山丘": "hills", "湖": "lake", "沙滩": "beach",
}


def enabled() -> bool:
    """开关打开、且所选图源可用时启用。"""
    if not IMAGE_SEND:
        return False
    if IMAGE_SOURCE in ("360", "auto"):
        return True          # 360 免 Key
    return bool(PEXELS_KEY or PIXABAY_KEY or UNSPLASH_KEY)


def _clean_query(query: str) -> str:
    q = (query or "").strip().strip("，,。.！!？?、\"'“”")
    q = q[:60]
    if not q:
        return ""
    low = q.lower()
    if any(w in low for w in _BLOCK_WORDS):
        logger.warning(f"[image] 关键词被安全过滤: {q!r}")
        return ""
    # 360 本身就是中文搜索引擎，关键词原样保留；只有英文图库（key 源）才需要中译英
    if IMAGE_SOURCE in ("360", "auto"):
        return q
    # 中文关键词兜底翻译成英文
    if any("\u4e00" <= ch <= "\u9fff" for ch in q):
        mapped = _CN2EN.get(q)
        if mapped:
            return mapped
        # 尝试逐词替换（如"可爱的小猫"）
        out = q
        for cn, en in sorted(_CN2EN.items(), key=lambda x: -len(x[0])):
            if cn in out:
                out = out.replace(cn, f" {en} ")
        # 清掉常见虚词/量词，避免"可爱的小猫"因为一个"的"就整句放弃
        for stop in ("的", "了", "吗", "呀", "啊", "吧", "呢", "和", "与",
                     "一只", "一个", "一张", "一些", "一点"):
            out = out.replace(stop, " ")
        if any("\u4e00" <= ch <= "\u9fff" for ch in out):
            # 还有中文没翻出来，图库基本搜不到，直接放弃（让上层回一句自然话）
            logger.info(f"[image] 中文关键词无法自动转英文，跳过: {q!r}")
            return ""
        return " ".join(out.split())
    return q


async def _download(client: httpx.AsyncClient, url: str) -> bytes | None:
    try:
        r = await client.get(url, headers=UA)
        r.raise_for_status()
        ct = (r.headers.get("content-type") or "").lower()
        if not ct.startswith("image/"):
            logger.warning(f"[image] 非图片响应 {ct}: {url[:70]}")
            return None
        if len(r.content) > IMG_MAX_BYTES:
            logger.warning(f"[image] 图片过大 {len(r.content) // 1024}KB，跳过: {url[:70]}")
            return None
        return r.content
    except Exception as e:
        logger.warning(f"[image] 下载失败 {type(e).__name__}: {url[:70]}")
        return None


async def _from_360(client: httpx.AsyncClient, q: str) -> bytes | None:
    """360 图片搜索（免 Key）。优先原图 img，退到缩略图。

    注意：360 是通用搜索引擎，返回的图片来自各个网站，版权不干净，
    仅供个人娱乐使用；已加敏感词过滤做最低限度兜底。
    """
    try:
        r = await client.get(
            "https://image.so.com/j",
            params={"q": q, "pn": 0, "rn": 12},
            headers={**UA, "Referer": "https://image.so.com/"},
        )
        r.raise_for_status()
        items = r.json().get("list") or []
        random.shuffle(items)
        for it in items:
            for key in ("img", "thumb_bak", "thumb"):
                url = (it.get(key) or "").strip()
                if not url.startswith("http"):
                    continue
                if url.startswith("http://"):        # 统一走 https，避免明文被中间设备掐断
                    url = "https://" + url[7:]
                data = await _download(client, url)
                if data:
                    return data
    except Exception as e:
        logger.warning(f"[image] 360 搜索失败: {e}")
    return None


async def _from_pexels(client: httpx.AsyncClient, q: str) -> bytes | None:
    if not PEXELS_KEY:
        return None
    try:
        r = await client.get(
            "https://api.pexels.com/v1/search",
            params={"query": q, "per_page": 8, "orientation": "landscape", "safesearch": "true"},
            headers={**UA, "Authorization": PEXELS_KEY},
        )
        r.raise_for_status()
        photos = r.json().get("photos") or []
        random.shuffle(photos)
        for p in photos:
            src = p.get("src") or {}
            url = src.get("large") or src.get("large2x") or src.get("medium") or src.get("original")
            if url:
                data = await _download(client, url)
                if data:
                    return data
    except Exception as e:
        logger.warning(f"[image] Pexels 搜索失败: {e}")
    return None


async def _from_pixabay(client: httpx.AsyncClient, q: str) -> bytes | None:
    if not PIXABAY_KEY:
        return None
    try:
        r = await client.get(
            "https://pixabay.com/api/",
            params={
                "key": PIXABAY_KEY, "q": q, "image_type": "photo",
                "per_page": 8, "safesearch": "true", "lang": "en",
            },
            headers=UA,
        )
        r.raise_for_status()
        hits = r.json().get("hits") or []
        random.shuffle(hits)
        for h in hits:
            url = h.get("largeImageURL") or h.get("webformatURL")
            if url:
                data = await _download(client, url)
                if data:
                    return data
    except Exception as e:
        logger.warning(f"[image] Pixabay 搜索失败: {e}")
    return None


async def _from_unsplash(client: httpx.AsyncClient, q: str) -> bytes | None:
    if not UNSPLASH_KEY:
        return None
    try:
        r = await client.get(
            "https://api.unsplash.com/search/photos",
            params={"query": q, "per_page": 8, "orientation": "landscape", "content_filter": "high"},
            headers={**UA, "Authorization": f"Client-ID {UNSPLASH_KEY}"},
        )
        r.raise_for_status()
        results = r.json().get("results") or []
        random.shuffle(results)
        for item in results:
            urls = item.get("urls") or {}
            url = urls.get("regular") or urls.get("full") or urls.get("small")
            if url:
                data = await _download(client, url)
                if data:
                    return data
    except Exception as e:
        logger.warning(f"[image] Unsplash 搜索失败: {e}")
    return None


# 缓存：Pixabay 条款要求缓存 API 结果 24 小时，顺带省额度
_CACHE: dict[str, tuple[float, bytes]] = {}
_CACHE_TTL = 24 * 3600
_CACHE_MAX = 32


async def fetch_image(query: str) -> bytes | None:
    """按关键词搜一张可自由使用的图片，返回图片字节；失败返回 None。"""
    if not enabled():
        return None
    q = _clean_query(query)
    if not q:
        return None

    # 命中 24h 缓存直接返回
    key = q.lower()
    now = time.time()
    hit = _CACHE.get(key)
    if hit and now - hit[0] < _CACHE_TTL:
        logger.info(f"[image] 命中缓存 {q!r}")
        return hit[1]

    # trust_env=False：本机系统代理曾在访问部分站点时返回 502，直连更稳
    async with httpx.AsyncClient(trust_env=False, timeout=IMG_TIMEOUT, follow_redirects=True) as client:
        if IMAGE_SOURCE == "360":
            sources = (_from_360,)
        elif IMAGE_SOURCE == "auto":
            sources = (_from_360, _from_pexels, _from_pixabay, _from_unsplash)
        else:
            sources = (_from_pexels, _from_pixabay, _from_unsplash)
        for fn in sources:
            data = await fn(client, q)
            if data:
                _CACHE[key] = (now, data)
                if len(_CACHE) > _CACHE_MAX:      # 简单淘汰最旧的一条，避免吃内存
                    oldest = min(_CACHE, key=lambda k: _CACHE[k][0])
                    _CACHE.pop(oldest, None)
                logger.info(f"[image] 搜到图片 {q!r} via {fn.__name__}（{len(data) // 1024}KB）")
                return data
    logger.warning(f"[image] 没搜到图片: {q!r}")
    return None
