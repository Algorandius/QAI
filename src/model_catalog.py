"""模型目录与厂商 Key 的对应关系。

人设可以选择一个模型；未选择时使用 .env 里的默认模型。
"""
from __future__ import annotations

from typing import Any

PROVIDERS: list[dict[str, Any]] = [
    {
        "id": "zhipu",
        "name": "智谱 GLM",
        "base": "https://open.bigmodel.cn/api/paas/v4/",
        "models": [
            {"id": "glm-4.7-flash", "name": "GLM-4.7-Flash（免费）", "base": "https://open.bigmodel.cn/api/paas/v4/"},
            {"id": "glm-5.3-flash", "name": "GLM-5.3-Flash", "base": "https://open.bigmodel.cn/api/paas/v4/"},
            {"id": "glm-5.3", "name": "GLM-5.3", "base": "https://open.bigmodel.cn/api/paas/v4/"},
        ],
    },
    {
        "id": "deepseek",
        "name": "DeepSeek",
        "base": "https://api.deepseek.com/v1",
        "models": [
            {"id": "deepseek-flash", "name": "DeepSeek-V4.1-Flash", "base": "https://api.deepseek.com/v1"},
            {"id": "deepseek-v4-pro", "name": "DeepSeek-V4-Pro", "base": "https://api.deepseek.com/v1"},
        ],
    },
    {
        "id": "qwen",
        "name": "阿里通义千问",
        "base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "models": [
            {"id": "qwen3.8-flash", "name": "qwen3.8-flash", "base": "https://dashscope.aliyuncs.com/compatible-mode/v1"},
        ],
    },
    {
        "id": "kimi",
        "name": "Kimi",
        "base": "https://api.moonshot.cn/v1",
        "models": [
            {"id": "kimi-k3", "name": "kimi-k3", "base": "https://api.moonshot.cn/v1"},
        ],
    },
    {
        "id": "hunyuan",
        "name": "混元 HY",
        "base": "https://tokenhub.tencentmaas.com/v1",
        "models": [
            {"id": "hy3-preview", "name": "Hy3", "base": "https://tokenhub.tencentmaas.com/v1"},
            {"id": "hy4-preview", "name": "Hy4 preview", "base": "https://tokenhub-intl.tencentcloudmaas.com/v1"},
        ],
    },
]

PROVIDER_KEY_ENV = {
    "zhipu": "LLM_KEY_ZHIPU",
    "deepseek": "LLM_KEY_DEEPSEEK",
    "qwen": "LLM_KEY_QWEN",
    "kimi": "LLM_KEY_KIMI",
    "hunyuan": "LLM_KEY_HUNYUAN",
    "tencent": "LLM_KEY_TENCENT",
    "tencent_intl": "LLM_KEY_TENCENT_INTL",
}

# 一个厂商可能对应多个历史键名（混元早期写成 tencent / tencent_intl）。
# 按优先级排列，读的时候取第一个非空的。
PROVIDER_KEY_ALIASES: dict[str, tuple[str, ...]] = {
    "zhipu": ("LLM_KEY_ZHIPU",),
    "deepseek": ("LLM_KEY_DEEPSEEK",),
    "qwen": ("LLM_KEY_QWEN",),
    "kimi": ("LLM_KEY_KIMI",),
    "hunyuan": ("LLM_KEY_HUNYUAN", "LLM_KEY_TENCENT", "LLM_KEY_TENCENT_INTL"),
    "tencent": ("LLM_KEY_HUNYUAN", "LLM_KEY_TENCENT", "LLM_KEY_TENCENT_INTL"),
    "tencent_intl": ("LLM_KEY_HUNYUAN", "LLM_KEY_TENCENT_INTL", "LLM_KEY_TENCENT"),
}


def key_envs_for(provider_id: str) -> tuple[str, ...]:
    """该厂商可能存放 Key 的 .env 键名，按优先级排列。

    厂商别名（tencent → 混元）也在这里处理，调用方不要再自己判断。
    """
    return PROVIDER_KEY_ALIASES.get(str(provider_id or "").strip().lower(), ())


def key_env_for(provider_id: str) -> str:
    """该厂商**写入** Key 时用的键名（永远写主键，避免别名分叉）。"""
    pid = str(provider_id or "").strip().lower()
    if pid in ("tencent", "tencent_intl"):
        pid = "hunyuan"
    return PROVIDER_KEY_ENV.get(pid, "")


def provider(provider_id: str) -> dict[str, Any] | None:
    return next((p for p in PROVIDERS if p["id"] == provider_id), None)


def model(provider_id: str, model_id: str) -> dict[str, Any] | None:
    p = provider(provider_id)
    if not p:
        return None
    return next((m for m in p["models"] if m["id"] == model_id), None)


def base_for(provider_id: str, model_id: str = "") -> str:
    m = model(provider_id, model_id)
    if m and m.get("base"):
        return str(m["base"])
    p = provider(provider_id)
    return str(p.get("base") or "") if p else ""
