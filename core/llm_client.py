# core/llm_client.py  —— 封装 OpenRouter API 调用（兼容 OpenAI 格式）
import json
import re
from openai import OpenAI
from config import ANTHROPIC_API_KEY

OPENROUTER_MODEL = "anthropic/claude-sonnet-4-5"
DEFAULT_MAX_TOKENS = 4096
LONG_MAX_TOKENS = 8192


class LLMClient:
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

    def chat(self, system: str, user: str, max_tokens: int = DEFAULT_MAX_TOKENS) -> str:
        msg = self._client.chat.completions.create(
            model=OPENROUTER_MODEL,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return msg.choices[0].message.content

    def chat_json(self, system: str, user: str, max_tokens: int = LONG_MAX_TOKENS) -> dict | list:
        json_system = (
            system
            + "\n\n【重要输出规范】\n"
            "1. 只输出合法 JSON，不要任何 markdown 代码块标记、解释文字或前言\n"
            "2. JSON 字符串中不能包含未转义的双引号，必须用 \\\" 转义\n"
            "3. SQL 语句中的字符串值改用数字或无需引号的值，避免引号嵌套问题\n"
            "   例如：INSERT INTO t VALUES(1, 2) 而不是 INSERT INTO t VALUES('a', 'b')\n"
            "   如果必须用字符串，用 char(65) 这类函数代替字面量\n"
            "4. 确保 JSON 完整输出，不要截断\n"
        )
        raw = self.chat(json_system, user, max_tokens=max_tokens)

        # 去掉 markdown 代码块
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.DOTALL)

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            print(f"\n  [LLMClient] ❌ JSON 解析失败: {e}")
            print(f"  [LLMClient] 回复总长度: {len(raw)} 字符")

            # 修复策略1：截断修复（应对输出被截断）
            fixed = self._try_fix_truncated_json(cleaned)
            if fixed is not None:
                print("  [LLMClient] ✅ 截断修复成功")
                return fixed

            # 修复策略2：让 LLM 自己修复这个 JSON
            print("  [LLMClient] 尝试让 LLM 修复损坏的 JSON...")
            fixed2 = self._ask_llm_to_fix_json(cleaned)
            if fixed2 is not None:
                print("  [LLMClient] ✅ LLM 修复成功")
                return fixed2

            raise

    def _try_fix_truncated_json(self, raw: str):
        """尝试修复被截断的 JSON 数组"""
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
        """把损坏的 JSON 发给 LLM，让它修复后返回"""
        fix_system = (
            "你是一个 JSON 修复专家。用户会给你一段损坏的 JSON，"
            "请修复其中的语法错误（如未转义的引号、截断等），"
            "只返回修复后的合法 JSON，不要任何解释。"
        )
        fix_user = f"请修复以下损坏的 JSON：\n\n{broken_json[:6000]}"
        try:
            raw = self.chat(fix_system, fix_user, max_tokens=LONG_MAX_TOKENS)
            cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.DOTALL)
            return json.loads(cleaned)
        except Exception:
            return None