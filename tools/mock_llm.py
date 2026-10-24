"""
本地假大模型服务（OpenAI 兼容协议）

用途：在还没申请 API Key、或者不想烧 token 的时候，
      先把「收消息 → 调模型 → 发回复」整条链路跑通。

用法：
    python tools/mock_llm.py
然后另开终端，用下面的方式启动机器人（指向假模型）：
    set LLM_API_KEY=mock
    set LLM_BASE_URL=http://127.0.0.1:9099/v1
    python bot.py
"""
import json
import time

from fastapi import FastAPI, Request

app = FastAPI()

REPLIES = [
    "你好呀，我是群里的小助手。今天想聊点什么？",
    "这个问题我得想想。简单说，先动手跑一遍，出问题再针对性查，比纯看文档快。",
    "哈哈，这个我也踩过坑。建议先把日志打开，八成一眼就能看到原因。",
    "收到。我的建议是分三步：确认现象、缩小范围、验证修复。",
]


@app.post("/v1/chat/completions")
async def chat(req: Request):
    body = await req.json()
    messages = body.get("messages", [])
    user_msg = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
    system_msg = next((m["content"] for m in messages if m.get("role") == "system"), "")
    content = f"[假模型回复] 你刚才说：{user_msg}\n（已加载人设 {len(system_msg)} 字，上下文 {len(messages)} 条）"
    if "你是谁" in user_msg or "介绍" in user_msg:
        content = REPLIES[0]
    payload = {
        "id": "mock-" + str(int(time.time())),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": body.get("model", "mock-model"),
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }
    print(f"[mock_llm] 收到请求: {user_msg[:60]}", flush=True)
    return payload


@app.get("/v1/models")
async def models():
    return {"object": "list", "data": [{"id": "mock-model", "object": "model"}]}


if __name__ == "__main__":
    import uvicorn

    print("[mock_llm] 假模型服务启动在 http://127.0.0.1:9099")
    uvicorn.run(app, host="127.0.0.1", port=9099, log_level="warning")
