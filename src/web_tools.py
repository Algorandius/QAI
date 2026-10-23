"""DeepSeek Search / Fetch 接口封装。

    POST {root}/v1/tools/search   {"query": "...", "count": 5, "need_content": false}
    POST {root}/v1/tools/fetch    {"url": "https://..."}

Search 返回 results[]：title / url / snippet / summary / content / site_name / date /
rerank_score / authority。
Fetch 返回 {title, content}，content 为 Markdown 正文。

这两个接口独立于 chat/completions，需要在对话层注册为 function 工具，
模型发起 tool_call 后再请求。
"""
from __future__ import annotations

from typing import Any

import httpx
from nonebot import logger

from src.config import env, env_bool, env_int

# 开关：关掉后模型连工具都看不到，也不会产生搜索费用
WEB_TOOL = env_bool("AI_WEB_TOOL", True)
# 每轮对话最多查几次（防止模型反复搜个不停，也防止烧钱）
WEB_MAX_SEARCH = max(0, env_int("AI_WEB_MAX_SEARCH", 3))
# 单次搜索结果拼给模型的字符上限
WEB_RESULT_CHARS = max(200, env_int("AI_WEB_RESULT_CHARS", 1200))
# fetch 正文塞给模型的字符上限（网页常有几万字，不截断会炸上下文）
WEB_FETCH_CHARS = max(500, env_int("AI_WEB_FETCH_CHARS", 3000))
WEB_TIMEOUT = max(5.0, float(env("AI_WEB_TIMEOUT", "30") or 30))

# tools 接口挂在域名根上（https://api.deepseek.com/v1/tools/...），
# 而 LLM_BASE_URL 一般是 https://api.deepseek.com/v1，所以要退一级。
_BASE = (env("LLM_BASE_URL", "https://api.deepseek.com/v1") or "").rstrip("/")
_ROOT = _BASE[:-3] if _BASE.endswith("/v1") else _BASE
_KEY = env("LLM_API_KEY", "")


def enabled() -> bool:
    """只有 DeepSeek 官方 API 才有这两个接口；换别家（智谱/硅基流动）自动关掉。"""
    return bool(WEB_TOOL and _KEY and "deepseek.com" in _ROOT)


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_KEY}", "Content-Type": "application/json"}


async def search(query: str, count: int = 5) -> list[dict[str, Any]]:
    """按关键词搜网页，返回结果列表（失败返回空列表，不抛异常）。"""
    if not enabled() or not query.strip():
        return []
    try:
        async with httpx.AsyncClient(timeout=WEB_TIMEOUT, trust_env=False) as c:
            r = await c.post(f"{_ROOT}/v1/tools/search", headers=_headers(), json={
                "query": query[:128],
                "count": max(1, min(50, count)),
                "need_content": False,
            })
        if r.status_code != 200:
            logger.warning(f"[web] search HTTP {r.status_code}: {r.text[:150]}")
            return []
        return r.json().get("results") or []
    except Exception as e:
        logger.warning(f"[web] search 失败 {type(e).__name__}: {e}")
        return []


async def fetch(url: str) -> dict[str, Any] | None:
    """抓一个网页的正文（Markdown）。失败返回 None。"""
    if not enabled() or not url.startswith("http"):
        return None
    try:
        async with httpx.AsyncClient(timeout=WEB_TIMEOUT, trust_env=False) as c:
            r = await c.post(f"{_ROOT}/v1/tools/fetch", headers=_headers(), json={"url": url})
        if r.status_code != 200:
            logger.warning(f"[web] fetch HTTP {r.status_code}: {r.text[:150]}")
            return None
        return r.json()
    except Exception as e:
        logger.warning(f"[web] fetch 失败 {type(e).__name__}: {e}")
        return None


def _clip(s: str, n: int) -> str:
    s = (s or "").strip().replace("\n\n", "\n")
    return s if len(s) <= n else s[:n].rstrip() + "…"


def format_results(results: list[dict], limit_chars: int | None = None) -> str:
    """把搜索结果拼成模型能读的紧凑文本（带序号和链接，方便它再 fetch）。"""
    if not results:
        return "没有搜到结果。"
    budget = limit_chars or WEB_RESULT_CHARS
    lines: list[str] = []
    for i, it in enumerate(results, 1):
        title = (it.get("title") or "").strip()
        site = (it.get("site_name") or "").strip()
        date = str(it.get("date") or "")[:10]
        url = (it.get("url") or "").strip()
        body = (it.get("summary") or it.get("snippet") or it.get("content") or "").strip()
        head = f"[{i}] {title}"
        meta = " · ".join(x for x in (site, date) if x)
        if meta:
            head += f"（{meta}）"
        lines.append(head)
        if url:
            lines.append(f"    {url}")
        if body:
            lines.append("    " + _clip(body, 260))
        if sum(len(x) for x in lines) > budget:
            break
    text = "\n".join(lines)
    return _clip(text, budget)


def format_page(page: dict[str, Any]) -> str:
    """把 fetch 的正文拼成模型能读的文本。"""
    title = (page.get("title") or "").strip()
    content = (page.get("content") or "").strip()
    if not content:
        return "这个网页打不开或没有正文。"
    return (f"《{title}》\n" if title else "") + _clip(content, WEB_FETCH_CHARS)
