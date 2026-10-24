"""检查「代码里读取的配置键」是否都出现在 .env 模板里。

为什么需要它：
`.env.example` 少一个键，用户在界面上点了某个功能、配置却根本没生效，
而代码里不会报错——只会静默回落到默认值。发 1.1.0 时就漏了 LLM_KEY_*。

用法：
    python tools/check_env_keys.py            # 检查，失败时退出码 1
    python tools/check_env_keys.py --list     # 顺便列出代码读取的全部键

CI 在推送上跑这个脚本；本地构建前也可以跑一次。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 允许出现在代码里、但刻意不写进模板的键（写在这里要说明原因）
ALLOW_MISSING: dict[str, str] = {
    "QAI_USE_CHROME": "只给开发者临时切 Chrome 外壳用，不给用户",
    "AI_STICKER_SUBLIB": "只存在于人设 config.json，不读 .env",
    "BUILD_BASE_PYTHON": "构建时用的环境变量，不进运行时配置",
    "BUILD_CHECK_CLR": "构建时自检开关，不进运行时配置",
    "NAPCAT_INSTALL_URL": "NapCat 下载源覆盖项，默认走 GitHub 官方地址",
    "QAI_SKIP_ACL": "权限收紧的逃生开关（不想改文件权限时设 1）",
}

# 操作系统自带的环境变量，不是 QAI 的配置项
OS_VARS = {
    "LOCALAPPDATA", "APPDATA", "USERPROFILE", "USERNAME", "USER",
    "PROGRAMFILES", "TEMP", "TMP", "PATH", "HOME", "COMPUTERNAME",
}

# 只扫运行时真正会读配置的地方；tools/ 下是排错脚本，不算产品配置
SCAN_DIRS = ("src",)
SCAN_FILES = ("launcher.py", "qai_window.py", "bot.py", "stop.py", "build_portable.py")
ENV_TEMPLATE_CANDIDATES = (".env.example", ".env")

# env("KEY") / env_int("KEY", ...) / env_bool(...) / launcher 的 _env_int(...)
READ_RE = re.compile(
    r"(?:\benv|env_int|env_float|env_bool|_env_int|_env_bool|_cfg_text)\s*\(\s*[\"']([A-Z][A-Z0-9_]*)[\"']"
)
# os.getenv("KEY") / os.environ.get("KEY")
OS_ENV_RE = re.compile(r"os\.(?:getenv|environ\.get)\s*\(\s*[\"']([A-Z][A-Z0-9_]*)[\"']")


def iter_sources():
    for name in SCAN_FILES:
        path = ROOT / name
        if path.is_file():
            yield path
    for folder in SCAN_DIRS:
        base = ROOT / folder
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            yield path


def collect_used_keys() -> dict[str, str]:
    """返回 {键: 第一次出现的位置}。"""
    used: dict[str, str] = {}
    for path in iter_sources():
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        for regex in (READ_RE, OS_ENV_RE):
            for match in regex.finditer(text):
                used.setdefault(match.group(1), str(path.relative_to(ROOT)))
    # 厂商 Key 是通过 PROVIDER_KEY_ENV 间接读取的，正则抓不到，按清单补上
    try:
        from src.model_catalog import PROVIDER_KEY_ALIASES

        for names in PROVIDER_KEY_ALIASES.values():
            for name in names:
                used.setdefault(name, "src/model_catalog.py")
    except Exception:
        pass
    return used


def collect_template_keys() -> tuple[set[str], Path | None]:
    for candidate in ENV_TEMPLATE_CANDIDATES:
        path = ROOT / candidate
        if not path.is_file():
            continue
        keys = {
            m.group(1)
            for m in re.finditer(r"^\s*([A-Z][A-Z0-9_]*)\s*=", path.read_text(encoding="utf-8"), re.M)
        }
        return keys, path
    return set(), None


def build_guaranteed_keys() -> set[str]:
    """构建时一定会补进 .env 的键，模板里没有也算覆盖。"""
    names = {"ONEBOT_ACCESS_TOKEN", "LLM_API_KEY"}
    try:
        import build_portable

        names |= set(build_portable.SANITIZE_DEFAULTS)
        names |= set(build_portable.PROVIDER_KEY_SLOTS)
        names |= set(build_portable.UPDATE_KEY_SLOTS)
    except Exception:
        pass
    try:
        from src.model_catalog import PROVIDER_KEY_ALIASES

        # 混元的历史别名（tencent / tencent_intl）只读不写，模板里不必出现
        for aliases in PROVIDER_KEY_ALIASES.values():
            names |= set(aliases)
    except Exception:
        pass
    return names


def main() -> int:
    ap = argparse.ArgumentParser(description="检查 .env 模板是否覆盖代码读取的配置键")
    ap.add_argument("--list", action="store_true", help="列出代码读取的全部键")
    args = ap.parse_args()

    used = collect_used_keys()
    documented, template = collect_template_keys()
    guaranteed = build_guaranteed_keys()

    if args.list:
        for name in sorted(used):
            print(f"{name:<32} {used[name]}")

    if template is None:
        print("✗ 找不到 .env.example 或 .env")
        return 1

    missing = {
        name: where
        for name, where in used.items()
        if name not in documented
        and name not in guaranteed
        and name not in ALLOW_MISSING
        and name not in OS_VARS
    }
    if missing:
        print(f"✗ 有 {len(missing)} 个键代码会读、但 {template.name} 里没有：\n")
        for name in sorted(missing):
            print(f"  {name:<32} 读自 {missing[name]}")
        print("\n用户在 .env 里看不到这些键，只在界面上点对应功能时会静默失效。")
        return 1

    allowed_missing = sorted(set(used) - documented - guaranteed - OS_VARS)
    print(
        f"✓ 配置键一致：代码读取 {len(used)} 个，{template.name} 声明 {len(documented)} 个，"
        f"构建补全 {len(guaranteed)} 个"
    )
    if allowed_missing:
        print(f"  白名单跳过 {len(allowed_missing)} 个：{', '.join(allowed_missing)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
