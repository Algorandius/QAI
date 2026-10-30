"""QAI 版本号唯一来源。

launcher.py、build_portable.py、updater.py 都从这里读，避免版本号写散。
发版时只改这一行。
"""
from __future__ import annotations

VERSION = "1.3.0"

# 启动器接口版本：launcher.py 的 /api 有增减时 +1，界面用它对不上会提示「启动器是旧版」。
# 1.3.0 新增的 /api/commands、/api/diary 在 webpanel（8081）里，不影响这个值。
API_LEVEL = 2
