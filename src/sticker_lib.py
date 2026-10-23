"""本地表情库：文件缓存、标签索引和按情绪挑选。

目录结构：
    assets/stickers/
        <md5>.gif|png|jpg|webp
        index.json   [{"file": "xxx.gif", "tags": ["笑","开心"], "sublibs": ["子库"]}]
        sublibs.json ["子库名称"]
"""
from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path

from nonebot import logger

from src.config import ROOT

LIB_DIR = ROOT / "assets" / "stickers"
INDEX_FILE = LIB_DIR / "index.json"
SUBLIB_FILE = LIB_DIR / "sublibs.json"
LIB_DIR.mkdir(parents=True, exist_ok=True)


def load_sublibs() -> list[str]:
    if not SUBLIB_FILE.exists():
        return []
    try:
        data = json.loads(SUBLIB_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"[sticker] 读取子库失败: {e}")
        return []
    if not isinstance(data, list):
        return []
    out: list[str] = []
    for x in data:
        name = str(x or "").strip()
        if name and name not in out:
            out.append(name[:30])
    return out


def save_sublibs(names: list[str]) -> None:
    try:
        tmp = SUBLIB_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(names, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(SUBLIB_FILE)
    except Exception as e:
        logger.warning(f"[sticker] 保存子库失败: {e}")


def create_sublib(name: str) -> str:
    name = str(name or "").strip()[:30]
    if not name:
        raise ValueError("子库名称不能为空")
    names = load_sublibs()
    if name in names:
        raise ValueError("这个子库已经存在")
    names.append(name)
    save_sublibs(names)
    return name


def delete_sublib(name: str) -> int:
    name = str(name or "").strip()
    names = load_sublibs()
    if name not in names:
        raise ValueError("没有这个子库")
    save_sublibs([x for x in names if x != name])
    items = load_index()
    n = 0
    for it in items:
        tags = item_sublibs(it)
        if name in tags:
            tags = [x for x in tags if x != name]
            it["sublibs"] = tags
            it.pop("sublib", None)
            n += 1
    if n:
        save_index(items)
    return n


def item_sublibs(item: dict) -> list[str]:
    raw = item.get("sublibs")
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    old = str(item.get("sublib") or "").strip()
    return [old] if old else []


def set_item_sublibs(filename: str, sublibs: list[str]) -> dict:
    filename = str(filename or "").strip()
    valid = set(load_sublibs())
    names = [str(x).strip() for x in (sublibs or [])]
    names = list(dict.fromkeys(x for x in names if x in valid))
    items = load_index()
    for it in items:
        if it.get("file") == filename:
            it["sublibs"] = names
            it.pop("sublib", None)
            save_index(items)
            return it
    raise ValueError("没有这个表情")


def set_item_sublib(filename: str, sublib: str) -> dict:
    """兼容旧的单子库接口。"""
    return set_item_sublibs(filename, [sublib] if sublib else [])


def sublib_counts() -> list[dict]:
    counts: dict[str, int] = {}
    for it in load_index():
        for name in item_sublibs(it):
            counts[name] = counts.get(name, 0) + 1
    return [{"name": n, "count": counts.get(n, 0)} for n in load_sublibs()]


def load_index() -> list[dict]:
    if not INDEX_FILE.exists():
        return []
    try:
        data = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"[sticker] 读取索引失败: {e}")
        return []
    if not isinstance(data, list):
        return []
    out = []
    for d in data:
        if not isinstance(d, dict) or not d.get("file"):
            continue
        d["sublibs"] = item_sublibs(d)
        d.pop("sublib", None)
        out.append(d)
    return out


def load_index() -> list[dict]:
    if not INDEX_FILE.exists():
        return []
    try:
        data = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"[sticker] 读取索引失败: {e}")
        return []
    if not isinstance(data, list):
        return []
    return [d for d in data if isinstance(d, dict) and d.get("file")]


def save_index(items: list[dict]) -> None:
    try:
        tmp = INDEX_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(INDEX_FILE)
    except Exception as e:
        logger.warning(f"[sticker] 保存索引失败: {e}")


def file_for(url: str, ext: str = "gif") -> Path:
    """按 URL 生成稳定的本地文件名（同一条收藏不会重复下载）。"""
    h = hashlib.md5(url.encode("utf-8")).hexdigest()[:16]
    return LIB_DIR / f"{h}.{ext}"


def count() -> int:
    return len(load_index())


# ---------------------------------------------------------------- 库清单（让模型知道"我有哪些图"）
_CATALOG_CACHE: tuple[float, str] | None = None


def catalog(max_tags: int = 10) -> str:
    """返回一句"我的表情库大概有哪些"的摘要，供注入 system prompt。

    形如：共 23 张，常见标签：疑惑(9)、整活(7)、开心(6)…
    按索引文件 mtime 缓存，索引没变就不重算。
    """
    global _CATALOG_CACHE
    if not INDEX_FILE.exists():
        return ""
    try:
        mtime = INDEX_FILE.stat().st_mtime
    except OSError:
        return ""
    if _CATALOG_CACHE and _CATALOG_CACHE[0] == mtime:
        return _CATALOG_CACHE[1]

    items = load_index()
    if not items:
        return ""

    counter: dict[str, int] = {}
    for it in items:
        # 先归并到主类（疑惑/问号/啊？ → 疑惑），同一张图内去重，按"张数"统计
        mains = set()
        for t in (it.get("tags") or []):
            t = str(t).strip()
            if t:
                mains.add(_ALIAS.get(t.lower(), t))
        for m in mains:
            counter[m] = counter.get(m, 0) + 1

    if not counter:
        text = f"共 {len(items)} 张（还没打标签）"
    else:
        top = sorted(counter.items(), key=lambda kv: -kv[1])[:max_tags]
        text = f"共 {len(items)} 张，常见标签：" + "、".join(f"{t}({c})" for t, c in top)

    _CATALOG_CACHE = (mtime, text)
    return text


# 同义词表：主类 -> 同类说法（标签是模型写的，每次用词未必一致）
_SYNONYMS = {
    "疑惑": ["疑惑", "问号", "啊？", "啊?", "懵逼", "不解", "挠头", "what", "懵"],
    "玩闹": ["整活", "玩闹", "梗", "梗图", "抽象", "沙雕", "搞怪", "气氛", "搞笑", "离谱"],
    "开心": ["笑", "开心", "哈哈", "笑死", "乐", "欢乐", "高兴", "嬉皮", "笑哭"],
    "无语": ["无语", "服了", "汗", "沉默", "无奈", "绷不住", "尴尬", "流汗"],
    "赞": ["赞", "牛", "强", "厉害", "大拇指", "棒", "优秀", "666"],
    "哭": ["哭", "流泪", "难过", "委屈", "呜呜", "泪", "悲伤"],
    "生气": ["生气", "怒", "气", "暴躁", "火大", "抓狂", "愤怒"],
    "卖萌": ["萌", "可爱", "撒娇", "害羞", "乖", "亲亲", "可怜"],
    "打招呼": ["hi", "你好", "招手", "打招呼", "hello", "早", "挥手"],
    "谢谢": ["谢谢", "感谢", "鞠躬", "抱拳", "多谢"],
}

# 反向索引：任意说法 -> 主类（用于归并统计 / 扩展匹配）
_ALIAS: dict[str, str] = {}
for _main, _syns in _SYNONYMS.items():
    _ALIAS[_main.lower()] = _main
    for _s in _syns:
        _ALIAS[_s.lower()] = _main

# "没有明确情绪"时优先用的玩闹类标签
_FUN_TAGS = ("整活", "玩闹", "梗", "梗图", "抽象", "沙雕", "搞怪", "气氛")


# QQ/NapCat 对大 GIF 支持不好，挑图时优先避开过大的
_MAX_SEND_BYTES = 5 * 1024 * 1024


def pick(mood_tags: list[str], sublib: str = "") -> Path | None:
    """按情绪标签挑一个本地表情；sublib 非空时只从该子库挑。"""
    items = load_index()
    if sublib:
        sublib = str(sublib).strip()
        if sublib in load_sublibs():
            items = [it for it in items if sublib in item_sublibs(it)]
    if not items:
        return None

    want: list[str] = []
    for raw in mood_tags or []:
        t = str(raw).strip().lower()
        if not t:
            continue
        main = _ALIAS.get(t, t)
        want.append(t)
        want.append(main.lower())
        want.extend(s.lower() for s in _SYNONYMS.get(main, []))
    want = list(dict.fromkeys(want))     # 去重，避免重复词把分数抬高

    def _tags_of(it: dict) -> list[str]:
        return [str(x).lower() for x in (it.get("tags") or [])]

    pool: list[dict] = []
    if want:
        best: list[dict] = []
        best_score = 0
        for it in items:
            tags = _tags_of(it)
            score = sum(1 for w in want if any(w in tg or tg in w for tg in tags))
            if score > best_score:
                best, best_score = [it], score
            elif score == best_score and score > 0:
                best.append(it)
        pool = best

    if not pool:
        # 没匹配上（或模型没给情绪）：优先挑"玩闹/整活"类，这类图放到任何场合都不违和
        pool = [it for it in items
                if any(any(f in tg for f in _FUN_TAGS) for tg in _tags_of(it))]
    if not pool:
        pool = items

    random.shuffle(pool)
    fallback: Path | None = None
    for it in pool:
        p = LIB_DIR / it["file"]
        try:
            if not p.exists():
                continue
            if fallback is None:
                fallback = p
            if p.stat().st_size <= _MAX_SEND_BYTES:   # 优先能顺利发出去的小图
                return p
        except OSError:
            continue
    return fallback
