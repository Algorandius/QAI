"""内置指令目录：从插件源码里解析 `on_command(...)`，供管理台展示。

为什么解析源码而不是手写一份清单：
手写清单一定会忘。加个命令忘了同步，面板就骗人。
这里直接读 src/plugins/*.py，有什么就显示什么。

只做只读解析，不 import 插件（import 会触发 NoneBot 初始化和副作用）。
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

from src.config import ROOT

PLUGIN_DIR = ROOT / "src" / "plugins"

# 分组规则：命令关键字 → (分组名, 分组说明)。
# 顺序有意义，先匹配到的优先（例如「建表情库」要落在表情组，不是记忆组）。
_GROUPS: list[tuple[str, tuple[str, ...], str]] = [
    ("斗图", ("斗图", "收图", "收藏表情", "收表情包"), "跟人斗图、收藏别人发的表情包"),
    ("消息增强", ("history", "赞", "已读", "消息增强", "react", "read"),
     "已读标记、表情回应、历史消息"),
    ("表情", ("表情包", "建表情库", "发表情包", "自定义表情"),
     "发收藏表情、重建表情库"),
    ("记忆与人设", ("人格", "人设", "persona", "接话", "记忆", "记忆清理", "清理记忆",
                    "aireset", "mood", "心情", "好感", "affinity"),
     "切换人设、记忆管理、好感度与心情"),
    ("群管理", ("ban", "禁言", "unban", "解禁", "members", "成员", "groups", "群列表"),
     "禁言、成员与群列表（需要管理权限）"),
    ("消息", ("echo", "回显", "at", "face", "表情", "img", "图片", "发图",
              "reply", "引用回复", "recall", "撤回"),
     "回显、@、发表情、发图、引用回复、撤回"),
    ("基础", ("help", "帮助", "菜单", "ping", "pong", "存活", "me", "我是谁", "status", "状态"),
     "帮助、存活检测、身份与运行状态"),
]

_HELP_TEXT = PLUGIN_DIR / "basic.py"

# 从 help 文案里抽「/cmd  说明」，补充说明文本
_HELP_LINE = re.compile(r'"/([^\s/]+)\s{2,}([^"\\]+)\\n"')

# help 文案里没写全的（有参数的命令常在别处说明），这里补一份。
# 只补 help 抽不到的，抽到的不覆盖。
_EXTRA_DESC = {
    "help": "列出全部指令",
    "at": "@某人，可带一句正文：/at 123456 你好",
    "ban": "禁言某人 N 分钟（需要管理权限）：/ban @某人 10",
    "unban": "解除禁言：/unban @某人",
    "img": "发一张图：/img https://… 或 /img 文件名.png",
    "echo": "原样回显一段文字：/echo 你好",
    "reply": "引用你这条消息回复：/reply 正文",
    "ping": "看机器人在不在、响应多快",
    "status": "运行状态：在线时长、收发条数",
    "history": "拉最近聊天记录，合并转发给你：/history 20",
    "收图": "引用一条表情包消息再发 /收图；不引用则从 QQ 收藏拉一批",
    "开启斗图模式": "本群开启斗图，有人发表情包它就回",
    "关闭斗图模式": "关掉本群的斗图模式",
    "消息增强": "查看已读/输入状态/表情回应/历史补全的开关",
    "赞": "给你引用的消息贴一个表情回应",
    "已读": "把引用的消息标记为已读",
    "接话": "查看或切换自主接话：/接话 开  /接话 关",
    "记忆": "查看长期记忆；/记忆 删 <编号> 忘掉某条",
    "清理记忆": "立即清理过期的对话与日志（管理员或私聊）",
    "表情包": "从收藏里随机发一个表情包",
    "建表情库": "拉取 QQ 收藏 → 视觉打标签 → 重建本地库（管理员或私聊）",
    "aireset": "清空本群/本人的对话记忆",
    "mood": "查看当前心情",
    "好感": "查看自己 + 本群的好感度排行",
    "人格": "列出人设；/人格 名字 切换（会清空全部记忆）",
}


def _group_of(cmd: str, aliases: list[str]) -> tuple[str, str]:
    """把一条命令归到某个分组。"""
    pool = [cmd.lower()] + [a.lower() for a in aliases]
    for name, keys, desc in _GROUPS:
        for key in keys:
            k = key.lower()
            if any(k == p or k in p for p in pool):
                return name, desc
    return "其他", "未归类的命令"


def _help_texts() -> dict[str, str]:
    """从 basic.py 的 /help 文案里抽出每条命令的说明。"""
    out: dict[str, str] = {}
    try:
        raw = _HELP_TEXT.read_text(encoding="utf-8")
    except Exception:
        return out
    for m in _HELP_LINE.finditer(raw):
        cmd, desc = m.group(1).strip(), m.group(2).strip()
        if cmd and desc and cmd not in out:
            out[cmd] = desc
    return out


def _scan_file(path: Path, helps: dict[str, str]) -> list[dict]:
    """解析一个插件文件里的 on_command 调用。"""
    try:
        src = path.read_text(encoding="utf-8")
    except Exception:
        return []
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []

    found: list[dict] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = getattr(fn, "id", None) or getattr(fn, "attr", None)
        if name != "on_command":
            continue
        cmd = ""
        if node.args:
            a0 = node.args[0]
            if isinstance(a0, ast.Constant) and isinstance(a0.value, str):
                cmd = a0.value
        if not cmd:
            continue

        aliases: list[str] = []
        for kw in node.keywords:
            if kw.arg != "aliases":
                continue
            if isinstance(kw.value, (ast.Set, ast.List, ast.Tuple)):
                for el in kw.value.elts:
                    if isinstance(el, ast.Constant) and isinstance(el.value, str):
                        aliases.append(el.value)

        group, group_desc = _group_of(cmd, aliases)
        found.append({
            "command": cmd,
            "aliases": aliases,
            # 优先用 help 文案里的说明，抽不到再用补充表
            "desc": helps.get(cmd) or _EXTRA_DESC.get(cmd, ""),
            "group": group,
            "group_desc": group_desc,
            "file": path.name,
        })
    return found


def list_commands() -> dict:
    """返回全部指令，按分组整理好，供面板直接渲染。"""
    helps = _help_texts()
    items: list[dict] = []
    seen: set[str] = set()
    for path in sorted(PLUGIN_DIR.glob("*.py")):
        if path.name.startswith("_"):
            continue
        for item in _scan_file(path, helps):
            key = item["command"]
            if key in seen:          # 同一命令可能被多个文件注册，只留一条
                continue
            seen.add(key)
            items.append(item)

    # 按分组聚合，保持 _GROUPS 里的顺序
    order = [g[0] for g in _GROUPS] + ["其他"]
    groups: list[dict] = []
    for gname in order:
        members = [x for x in items if x["group"] == gname]
        if not members:
            continue
        members.sort(key=lambda x: x["command"])
        groups.append({
            "name": gname,
            "desc": members[0]["group_desc"],
            "items": [{k: v for k, v in m.items() if k not in ("group", "group_desc")}
                      for m in members],
        })

    return {
        "ok": True,
        "count": len(items),
        "groups": groups,
        # 通用调用方式：这些都写在 basic.py 的说明里
        "prefixes": ["/", "!", "！"],
        "note": "指令前面的斜杠可以用 ! 或 ！ 代替。带参数的指令按示例填写即可。",
    }
