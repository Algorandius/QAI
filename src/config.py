"""读取 .env 的辅助函数。

NoneBot 只加载自身认识的配置项，这里显式调用 load_dotenv，
让 LLM_API_KEY、ALLOWED_GROUPS 等自定义变量也能被读取。
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent

# override=False：真正的环境变量（命令行传入的）优先级更高，方便临时调试
load_dotenv(ROOT / ".env", override=False)


def env(key: str, default: str = "") -> str:
    v = os.getenv(key)
    if v is None or v.strip() == "":
        return default
    return v.strip()


def env_int(key: str, default: int = 0) -> int:
    try:
        return int(env(key, str(default)))
    except ValueError:
        return default


def env_float(key: str, default: float = 0.0) -> float:
    try:
        return float(env(key, str(default)))
    except ValueError:
        return default


def env_bool(key: str, default: bool = False) -> bool:
    return env(key, "false").strip().lower() in ("1", "true", "yes", "on")


def env_set(key: str) -> set[str]:
    return {x.strip() for x in env(key).split(",") if x.strip()}
