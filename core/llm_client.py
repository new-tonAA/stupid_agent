# core/llm_client.py  —— 封装 OpenRouter API 调用（兼容 OpenAI 格式）
import json
import re
from openai import OpenAI
from config import ANTHROPIC_API_KEY, MAX_TOKENS

# OpenRouter 上的 Claude 模型名
OPENROUTER_MODEL = "anthropic/claude-sonnet-4-5"


class LLMClient:
    """
    通过 OpenRouter 调用 Claude，使用 OpenAI 兼容接口。
    """

    def __init__(self):
        if not ANTHROPIC_API_KEY:
            raise EnvironmentError(
                "未找到 ANTHROPIC_API_KEY，请在环境变量中设置 OpenRouter 的 key。\n"
                "例如：$env:ANTHROPIC_API_KEY='sk-or-...'"
            )
        self._client = OpenAI(
            api_key=ANTHROPIC_API_KEY,
            base_url="https://openrouter.ai/api/v1",
        )

    def chat(self, system: str, user: str) -> str:
        """单轮对话，返回模型回复文本"""
        msg = self._client.chat.completions.create(
            model=OPENROUTER_MODEL,
            max_tokens=MAX_TOKENS,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return msg.choices[0].message.content

    def chat_json(self, system: str, user: str) -> dict | list:
        """
        要求模型以 JSON 格式回复，自动解析并返回 Python 对象。
        """
        json_system = (
            system
            + "\n\n【重要】你的回复必须是合法 JSON，不要包含任何 markdown 代码块、"
            "解释文字或前言。直接输出 JSON。"
        )
        raw = self.chat(json_system, user)
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.DOTALL)
        return json.loads(cleaned)