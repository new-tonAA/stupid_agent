# config.py  —— 项目配置
import os

# ── Anthropic API ──────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")  # 从环境变量读取
CLAUDE_MODEL = "claude-sonnet-4-20250514"
MAX_TOKENS = 4096

# ── 终端执行 ────────────────────────────────────────────────────
TERMINAL_TIMEOUT = 30        # 每条命令最长等待秒数
MAX_RETRIES = 2              # 命令执行失败重试次数

# ── 输出目录 ────────────────────────────────────────────────────
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
os.makedirs(OUTPUT_DIR, exist_ok=True)