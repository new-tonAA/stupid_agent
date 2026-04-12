# core/llm_client.py  —— LLM 调用封装，支持 OpenRouter 和 api.v3.cm 双平台
import json
import re
from openai import OpenAI
import config

DEFAULT_MAX_TOKENS = 4096
LONG_MAX_TOKENS = 8192


def _make_client() -> tuple[OpenAI, str]:
    """
    根据 config.LLM_PROVIDER 创建对应的 OpenAI 客户端和模型名。
    返回 (client, model_name)
    """
    provider = config.LLM_PROVIDER.lower()

    if provider == "v3":
        api_key = config.V3_API_KEY or config.ANTHROPIC_API_KEY
        if not api_key:
            raise EnvironmentError(
                "未找到 api.v3.cm 的 API Key。\n"
                "请设置环境变量：$env:V3_API_KEY='你的key'\n"
                "或：$env:ANTHROPIC_API_KEY='你的key'"
            )
        client = OpenAI(api_key=api_key, base_url=config.V3_BASE_URL)
        model = config.V3_MODEL
        print(f"  [LLMClient] 使用平台: api.v3.cm  模型: {model}")

    else:  # openrouter（默认）
        api_key = config.OPENROUTER_API_KEY or config.ANTHROPIC_API_KEY
        if not api_key:
            raise EnvironmentError(
                "未找到 OpenRouter 的 API Key。\n"
                "请设置环境变量：$env:OPENROUTER_API_KEY='sk-or-...'\n"
                "或：$env:ANTHROPIC_API_KEY='sk-or-...'"
            )
        client = OpenAI(api_key=api_key, base_url=config.OPENROUTER_BASE_URL)
        model = config.OPENROUTER_MODEL
        print(f"  [LLMClient] 使用平台: OpenRouter  模型: {model}")

    return client, model


class LLMClient:
    """
    统一的 LLM 调用接口，支持 OpenRouter 和 api.v3.cm。
    切换平台只需改 config.LLM_PROVIDER 或环境变量 LLM_PROVIDER。
    """

    def __init__(self):
        self._client, self._model = _make_client()

    def chat(self, system: str, user: str, max_tokens: int = DEFAULT_MAX_TOKENS) -> str:
        """单轮对话，返回文本"""
        msg = self._client.chat.completions.create(
            model=self._model,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return msg.choices[0].message.content

    def chat_json(self, system: str, user: str, max_tokens: int = LONG_MAX_TOKENS) -> dict | list:
        """
        要求模型以 JSON 格式回复，自动解析。
        带三层容错：直接解析 → 截断修复 → LLM 自修复。
        """
        json_system = (
            system
            + "\n\n【重要输出规范】\n"
            "1. 只输出合法 JSON，不要任何 markdown 代码块标记、解释文字或前言\n"
            "2. JSON 字符串中不能包含未转义的双引号，必须用 \\\" 转义\n"
            "3. SQL 语句中的字符串值改用数字或无需引号的值，避免引号嵌套问题\n"
            "4. 确保 JSON 完整输出，不要截断\n"
        )
        raw = self.chat(json_system, user, max_tokens=max_tokens)
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.DOTALL)

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            print(f"\n  [LLMClient] ❌ JSON 解析失败: {e}")
            print(f"  [LLMClient] 回复长度: {len(raw)} 字符")

            fixed = self._try_fix_truncated_json(cleaned)
            if fixed is not None:
                print("  [LLMClient] ✅ 截断修复成功")
                return fixed

            print("  [LLMClient] 尝试让 LLM 自修复...")
            fixed2 = self._ask_llm_to_fix_json(cleaned)
            if fixed2 is not None:
                print("  [LLMClient] ✅ LLM 修复成功")
                return fixed2

            raise

    def _try_fix_truncated_json(self, raw: str):
        """修复截断的 JSON 数组"""
        last_brace = raw.rfind("},")
        if last_brace == -1:
            last_brace = raw.rfind("}")
        if last_brace == -1:
            return None
        truncated = raw[:last_brace + 1].rstrip().rstrip(",") + "\n]"
        try:
            return json.loads(truncated)
        except json.JSONDecodeError:
            return None

    def _ask_llm_to_fix_json(self, broken_json: str):
        """把损坏的 JSON 发给 LLM 修复"""
        fix_system = (
            "你是 JSON 修复专家。修复用户给出的损坏 JSON，"
            "只返回修复后的合法 JSON，不要任何解释。"
        )
        try:
            raw = self.chat(fix_system, broken_json[:6000], max_tokens=LONG_MAX_TOKENS)
            cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.DOTALL)
            return json.loads(cleaned)
        except Exception:
            return None