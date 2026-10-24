"""扫描仓库里有没有明文密钥，防止再次把作者本机配置带进发布。

这是 2026-10 那次的教训：`.env` 被打进 zip 前只按黑名单清了几个键，
智谱那种 `id.secret` 格式的 Key 不在 `sk-` 前缀里，正则抓不到。

用法：
    python tools/check_secrets.py
发现可疑内容时退出码 1。

注意：这里只做「明显的密钥形状」判断，不追求零误报；误报就改进规则，
不要靠加白名单糊过去。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SKIP_DIRS = {".git", "__pycache__", ".venv", "node_modules", "dist", "build", "runtime"}
SCAN_SUFFIXES = {".py", ".js", ".html", ".css", ".md", ".json", ".txt", ".bat", ".vbs", ".yml", ".yaml"}

# 形如 sk- 加一长串字符的常见厂商 Key（这里刻意不写出可匹配的样例）
SK_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{16,}")
# 形如 32位hex.16位 的智谱 Key
ZHIPU_KEY_RE = re.compile(r"\b[0-9a-f]{32}\.[A-Za-z0-9]{10,}\b")
# .env 风格赋值里出现非空值的高危变量
ENV_LINE_RE = re.compile(
    r"^\s*(LLM_API_KEY|LLM_KEY_[A-Z_]+|ONEBOT_ACCESS_TOKEN|PIXABAY_API_KEY|"
    r"PEXELS_API_KEY|UNSPLASH_ACCESS_KEY)\s*=\s*(\S{12,})\s*$",
    re.M,
)
# 允许出现的占位值
PLACEHOLDERS = {"change-me", "your-key", "xxx", "todo", "sk-***"}


def iter_files():
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in SCAN_SUFFIXES:
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts):
            continue
        yield path


def scan() -> list[str]:
    problems: list[str] = []
    for path in iter_files():
        if path.name == ".env":
            # 本机真实配置，不参与扫描（它也进不了版本库）
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        rel = path.relative_to(ROOT)
        for match in SK_KEY_RE.finditer(text):
            problems.append(f"{rel}: 疑似 API Key {match.group(0)[:10]}…")
        for match in ZHIPU_KEY_RE.finditer(text):
            problems.append(f"{rel}: 疑似智谱 Key {match.group(0)[:10]}…")
        # 「KEY=值」只可能出现在配置文件/文档里；代码里的赋值是正常的
        if path.suffix.lower() in {".py", ".js", ".bat", ".vbs"}:
            continue
        for name, value in ENV_LINE_RE.findall(text):
            if value.strip().strip("'\"").lower() in PLACEHOLDERS:
                continue
            problems.append(f"{rel}: {name} 带着值（{value[:6]}…）")
    return problems


def main() -> int:
    problems = scan()
    if problems:
        print("✗ 发现疑似明文密钥：")
        for item in problems:
            print(f"  {item}")
        print("\n确认是占位示例的话，请改成 change-me 之类的形式。")
        return 1
    print("✓ 未发现明文密钥")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
