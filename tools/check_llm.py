"""
大模型连通性诊断工具

机器人能收消息但 AI 不回话时，先跑这个：
    python tools/check_llm.py

它会做三件事：
  1. 列出你的 Key 能用的所有模型（用来确认模型名有没有写错）
  2. 用 .env 里当前的配置真发一条消息，看能不能拿到回复
  3. 顺便测几个常见模型名，告诉你能用哪个

注意：不会打印你的 API Key。
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

try:
    import httpx
except ImportError:
    sys.exit("缺少 httpx，请先 pip install httpx")

API_KEY = os.getenv("LLM_API_KEY", "")
BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
MODEL = os.getenv("LLM_MODEL", "")

if not API_KEY:
    sys.exit("[X] .env 里 LLM_API_KEY 是空的，先去申请并填上")

print(f"BASE_URL : {BASE_URL}")
print(f"MODEL    : {MODEL}")
print(f"API_KEY  : 已配置（{len(API_KEY)} 位，{API_KEY[:5]}...）")
print("-" * 60)

HEADERS = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}


def list_models():
    print("[1] 拉取可用模型列表 ...")
    try:
        with httpx.Client(timeout=20) as c:
            r = c.get(f"{BASE_URL}/models", headers=HEADERS)
        if r.status_code != 200:
            print(f"    [!] 状态码 {r.status_code}：{r.text[:300]}")
            return None
        ids = [m.get("id", "") for m in r.json().get("data", [])]
        print(f"    [OK] 共 {len(ids)} 个模型：")
        for i in ids:
            mark = "  <== 当前用的" if i == MODEL else ""
            print(f"      - {i}{mark}")
        return ids
    except Exception as e:
        print(f"    [X] 请求失败：{e}")
        return None


def chat(model: str, content: str = "用一句话打个招呼，10 个字以内") -> bool:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "temperature": float(os.getenv("LLM_TEMPERATURE", "0.95")),
        "stream": False,
    }
    try:
        with httpx.Client(timeout=60) as c:
            r = c.post(f"{BASE_URL}/chat/completions", json=payload, headers=HEADERS)
        if r.status_code != 200:
            body = r.text[:400]
            print(f"    [X] {model} → HTTP {r.status_code}")
            print(f"        {body}")
            # 常见错误翻译成人话
            if "Insufficient Balance" in body or "balance" in body.lower():
                print("        → 余额不足，去平台充值")
            elif "invalid" in body.lower() and "api key" in body.lower():
                print("        → API Key 无效或已失效，重新生成一个")
            elif "model" in body.lower() and ("not exist" in body.lower() or "invalid" in body.lower()):
                print("        → 模型名不对，看上面列表里能用的名字")
            elif "401" in str(r.status_code) or "authentication" in body.lower():
                print("        → 鉴权失败，检查 Key 有没有复制全")
            return False
        data = r.json()
        reply = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        print(f"    [OK] {model} → {reply.strip()[:120]}")
        print(f"        token 用量: 输入 {usage.get('prompt_tokens')} / 输出 {usage.get('completion_tokens')}")
        return True
    except Exception as e:
        print(f"    [X] {model} → 请求异常：{e}")
        return False


def main() -> int:
    ids = list_models()
    print()
    print("[2] 用当前配置发一条测试消息 ...")
    ok = chat(MODEL)

    if not ok:
        print()
        print("[3] 自动尝试其他常见模型名 ...")
        candidates = [
            m for m in (ids or []) if any(k in m.lower() for k in ("flash", "chat", "v4"))
        ]
        if not candidates:
            candidates = ["deepseek-v4-flash", "deepseek-v4-pro", "deepseek-chat"]
        for m in candidates[:6]:
            if chat(m):
                print()
                print(f"[建议] 把 .env 的 LLM_MODEL 改成：{m}")
                return 0

    print()
    if ok:
        print("[✓] 模型接口正常。如果机器人还是不回话，那问题在别处：")
        print("    1. 确认改完 .env 后机器人重启过（.env 改动必须重启）")
        print("    2. 看机器人那个黑窗口有没有报错")
        print("    3. 私聊发 /ping 看基础功能是否正常（排除 AI 之外的问题）")
        return 0
    print("[X] 模型接口调不通，按上面的提示改配置后重启机器人")
    return 1


if __name__ == "__main__":
    sys.exit(main())
