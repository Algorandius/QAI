"""运行期统计，供 /status 命令查看"""
import time

START_AT = time.time()

STATS = {
    "recv": 0,        # 收到的消息数
    "sent": 0,        # 发出的消息数
    "ai_calls": 0,    # 大模型调用次数
    "ai_fail": 0,     # 大模型失败次数
    "blocked": 0,     # 被冷却/限流拦下的次数
}


def uptime_str() -> str:
    s = int(time.time() - START_AT)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    if h:
        return f"{h} 小时 {m} 分 {s} 秒"
    if m:
        return f"{m} 分 {s} 秒"
    return f"{s} 秒"
