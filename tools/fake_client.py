"""
链路自检工具：不需要真的登录 QQ，也能验证「NapCat → 机器人」这条链路通不通。

原理：伪装成一个 OneBot v11 协议端，主动连上机器人的反向 WebSocket，
      发一条假的群消息事件，看机器人有没有回一个 send_group_msg 调用。

用法（另开一个终端，机器人已经 python bot.py 跑起来的情况下）：
    python tools/fake_client.py
"""
import asyncio
import json
import os
import sys
import time

# 本机测试不走代理（很多人环境变量里配了 HTTP_PROXY，会把 127.0.0.1 也代理掉）
os.environ["NO_PROXY"] = "127.0.0.1,localhost"
os.environ["no_proxy"] = "127.0.0.1,localhost"
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(_k, None)

try:
    import websockets
except ImportError:
    sys.exit("缺少依赖，请先执行：pip install websockets")

TOKEN = os.getenv("ONEBOT_ACCESS_TOKEN", "qqbot-dev-token-2026")
SELF_ID = 10001          # 假机器人 QQ
GROUP_ID = 900001        # 假群号
USER_ID = 10002          # 假用户 QQ
URL = os.getenv("FAKE_WS_URL", "ws://127.0.0.1:8080/onebot/v11/ws")


def make_private_event(raw: str) -> dict:
    """构造一条假私聊消息事件（用来验证私聊 AI 分支）"""
    return {
        "post_type": "message",
        "message_type": "private",
        "sub_type": "friend",
        "message_id": int(time.time() * 1000) % 10**9,
        "user_id": USER_ID,
        "time": int(time.time()),
        "self_id": SELF_ID,
        "font": 0,
        "raw_message": raw,
        "message": [{"type": "text", "data": {"text": raw}}],
        "sender": {
            "user_id": USER_ID,
            "nickname": "测试用户",
            "sex": "unknown",
            "age": 0,
        },
    }


def make_event(raw: str, segments: list[dict] | None = None) -> dict:
    return {
        "post_type": "message",
        "message_type": "group",
        "sub_type": "normal",
        "message_id": int(time.time() * 1000) % 10**9,
        "group_id": GROUP_ID,
        "user_id": USER_ID,
        "time": int(time.time()),
        "self_id": SELF_ID,
        "font": 0,
        "raw_message": raw,
        "message": segments or [{"type": "text", "data": {"text": raw}}],
        "sender": {
            "user_id": USER_ID,
            "nickname": "测试用户",
            "card": "测试用户",
            "sex": "unknown",
            "age": 0,
            "area": "",
            "level": "1",
            "role": "member",
            "title": "",
        },
    }


def fake_result(action: str) -> dict:
    """按接口返回合理的数据。get_msg 必须返回完整结构，
    否则适配器解析 reply 会失败，测不了「引用」功能。"""
    if action == "get_msg":
        return {
            "time": int(time.time()),
            "message_type": "group",
            "message_id": 12345,
            "real_id": 12345,
            "sender": {
                "user_id": 10086, "nickname": "小王", "card": "小王",
                "sex": "unknown", "age": 0, "area": "", "level": "1",
                "role": "member", "title": "",
            },
            "message": [{"type": "text", "data": {"text": "明天那节课还上吗"}}],
            "raw_message": "明天那节课还上吗",
            "group": True, "group_id": GROUP_ID,
        }
    return {"message_id": 12345}


T0 = time.time()


async def main() -> int:
    global T0
    T0 = time.time()
    # (展示文本, 消息段)；带 at 段的会触发 AI 对话分支
    tests: list[tuple[str, list[dict] | None]] = [
        ("/ping", None),
        ("/help", None),
        ("/me", None),
        ("@机器人 你好", [
            {"type": "at", "data": {"qq": str(SELF_ID)}},
            {"type": "text", "data": {"text": " 你好，介绍一下你自己"}},
        ]),
        ("[私聊] 你好呀", None),
        # 引用：回复别人一条消息再问机器人
        ("[引用] 这啥意思", [
            {"type": "reply", "data": {"id": "12345"}},
            {"type": "at", "data": {"qq": str(SELF_ID)}},
            {"type": "text", "data": {"text": " 这啥意思"}},
        ]),
        # 表情包（mface）
        ("[表情包] 发个表情", [
            {"type": "at", "data": {"qq": str(SELF_ID)}},
            {"type": "mface", "data": {"summary": "[微笑]"}},
            {"type": "text", "data": {"text": " 啥意思"}},
        ]),
        # 图片
        ("[图片] 看图说话", [
            {"type": "at", "data": {"qq": str(SELF_ID)}},
            {"type": "image", "data": {
                "file": "abc.jpg", "url": "https://example.com/a.jpg",
                "summary": "表情包"}},
            {"type": "text", "data": {"text": " 这啥"}},
        ]),
        # 经典 QQ 表情（face id=21 偷笑）
        ("[QQ表情] 乐", [
            {"type": "at", "data": {"qq": str(SELF_ID)}},
            {"type": "face", "data": {"id": "21"}},
        ]),
    ]
    print(f"[*] 连接 {URL} ...")
    try:
        async with websockets.connect(
            URL,
            additional_headers={
                "Authorization": f"Bearer {TOKEN}",
                "X-Self-Id": str(SELF_ID),
                "X-Client-Role": "Universal",
            },
        ) as ws:
            print("[✓] WebSocket 已连接（Token 校验通过）")
            for raw, segs in tests:
                if raw.startswith("[私聊]"):
                    await ws.send(json.dumps(make_private_event(raw.replace("[私聊] ", ""))))
                else:
                    await ws.send(json.dumps(make_event(raw, segs)))
                print(f"[>] 已发送假消息：{raw}")

            got_reply = False
            deadline = time.time() + 15
            while time.time() < deadline:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=4)
                except asyncio.TimeoutError:
                    continue
                data = json.loads(msg)
                if "action" in data:
                    print(f"[✓] [{time.time() - T0:5.1f}s] 机器人调用了接口：{data['action']}")
                    print(f"    参数：{json.dumps(data.get('params', {}), ensure_ascii=False)[:300]}")
                    got_reply = True
                    fake = {
                        "status": "ok",
                        "retcode": 0,
                        "data": fake_result(data["action"]),
                        "echo": data.get("echo"),
                    }
                    await ws.send(json.dumps(fake))
                if time.time() > deadline - 4:  # 留 4 秒收尾
                    break
            print("\n[✓] 链路自检通过：机器人能收事件、能调接口发消息" if got_reply
                  else "\n[✗] 15 秒内没收到机器人的任何接口调用")
            return 0 if got_reply else 1
    except Exception as e:
        print(f"[✗] 连接失败：{e}")
        print("    常见原因：机器人没启动 / 端口不是 8080 / Token 与 .env 不一致")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
