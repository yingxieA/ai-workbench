"""LLM 服务 - qwen-plus OpenAI 兼容"""

from openai import OpenAI
from app.config import settings

_client = None


def get_client():
    global _client
    if _client is None:
        _client = OpenAI(
            api_key=settings.DASHSCOPE_API_KEY,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
    return _client


def chat_stream(messages):
    """流式调用 LLM，yield token。messages 可以是 str 或 list[dict]"""
    if isinstance(messages, str):
        messages = [{"role": "user", "content": messages}]
    client = get_client()
    stream = client.chat.completions.create(model=settings.LLM_MODEL, messages=messages, stream=True, temperature=0.1)
    for chunk in stream:
        if chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content


def chat(messages):
    """非流式调用 LLM，返回完整文本"""
    if isinstance(messages, str):
        messages = [{"role": "user", "content": messages}]
    client = get_client()
    resp = client.chat.completions.create(model=settings.LLM_MODEL, messages=messages, stream=False, temperature=0.1)
    return resp.choices[0].message.content
