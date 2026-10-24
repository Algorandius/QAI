"""QAI 版本号唯一来源。

launcher.py、build_portable.py、updater.py 都从这里读，避免版本号写散。
发版时只改这一行。
"""
from __future__ import annotations

VERSION = "1.2.1"

# 每次新增 /api 接口就 +1，界面用它对不上就会提示「启动器是旧版」
API_LEVEL = 2
