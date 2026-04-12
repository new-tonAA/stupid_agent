# config.py  —— 项目配置
import os

# ── LLM 平台选择 ────────────────────────────────────────────────
# 改这里切换平台：
#   "openrouter"  → 使用 openrouter.ai（claude-sonnet-4-5）
#   "v3"          → 使用 api.v3.cm（可选各种模型）
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "openrouter")

# ── OpenRouter 配置 ─────────────────────────────────────────────
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_MODEL = "anthropic/claude-sonnet-4-5"

# ── api.v3.cm 配置 ──────────────────────────────────────────────
V3_API_KEY = os.environ.get("V3_API_KEY", "")
V3_BASE_URL = "https://api.v3.cm/v1"
V3_MODEL = os.environ.get("V3_MODEL", "gpt-4o")  # 可换成 qwen3-vl-plus 等

# ── 兼容旧代码：ANTHROPIC_API_KEY 自动路由 ──────────────────────
# 旧代码里用 ANTHROPIC_API_KEY，这里自动根据平台取对应的 key
def get_api_key() -> str:
    if LLM_PROVIDER == "v3":
        return V3_API_KEY or os.environ.get("ANTHROPIC_API_KEY", "")
    else:
        return OPENROUTER_API_KEY or os.environ.get("ANTHROPIC_API_KEY", "")

ANTHROPIC_API_KEY = get_api_key()

# ── 通用参数 ────────────────────────────────────────────────────
MAX_TOKENS = 4096

# ── 终端执行 ────────────────────────────────────────────────────
TERMINAL_TIMEOUT = 30
MAX_RETRIES = 2

# ── 输出目录 ────────────────────────────────────────────────────
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
os.makedirs(OUTPUT_DIR, exist_ok=True)