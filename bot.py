"""
QQ 机器人入口（社区协议端路线：NapCat + OneBot v11 + NoneBot2）

启动方式：
    python bot.py

它会在 127.0.0.1:8080 起一个反向 WebSocket 服务端，
等待 NapCat 主动连上来（ws://127.0.0.1:8080/onebot/v11/ws）。
"""
import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter

# 读取 .env 配置
nonebot.init()

driver = nonebot.get_driver()
driver.register_adapter(OneBotV11Adapter)

# 加载 src/plugins 下的所有插件
nonebot.load_plugins("src/plugins")

if __name__ == "__main__":
    nonebot.run()
