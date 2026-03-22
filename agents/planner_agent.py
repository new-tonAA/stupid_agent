# agents/planner_agent.py  —— Agent 1：测试规划智能体
"""
职责：接收人工填写的测试框架 + 静态分析报告（可选），
      调用 LLM 将其细化为结构化的测试任务列表。
"""

from dataclasses import dataclass
from core.llm_client import LLMClient


PLANNER_SYSTEM = """
你是一名资深软件测试工程师。用户会提供：
1. 一个软件项目的描述和测试目标
2. （可能有）静态代码分析发现的风险点

你需要将其细化为具体、可执行的测试任务列表，返回 JSON 数组。

每个测试任务包含以下字段：
- task_id: 任务编号，格式 T01, T02, ...
- category: 测试类别（如 正常功能、边界值、异常输入、性能测试、静态分析驱动 等）
- description: 用一句话描述本测试的目的
- commands: 列表，每条是一行 shell 命令（用于在终端执行）
- expected: 期望的行为描述（文字）
- oracle_type: 以下之一
    "crash_check"    → 只检查程序是否崩溃/挂起
    "exit_code"      → 检查退出码是否为 0 或非 0
    "stdout_match"   → 检查 stdout 是否包含特定字符串
    "stdout_exact"   → 检查 stdout 是否与期望值完全一致
    "manual"         → 需要人工判断
- expected_value: 当 oracle_type 为 stdout_match/stdout_exact 时，填写期望的字符串；其他情况填 null

要求：
- 生成 12~15 个有代表性的测试任务
- 覆盖正常路径、边界值、异常场景
- 如果有静态分析风险点，必须为每个 high/medium 风险点生成至少一个对应的测试任务，
  这类任务的 category 标记为「静态分析驱动」
- commands 中的命令要可以直接在终端执行，不要有占位符
- SQL 语句中避免使用单引号字符串字面量（如 'Alice'），改用数字（如 1,2,3）或 char() 函数，防止 JSON 引号冲突
- 如果 SQL 必须用字符串，用双引号并用反斜杠转义：\"value\"
"""


@dataclass
class TestTask:
    task_id: str
    category: str
    description: str
    commands: list[str]
    expected: str
    oracle_type: str
    expected_value: str | None = None

    def to_dict(self) -> dict:
        return self.__dict__


class PlannerAgent:
    """
    Agent 1：测试规划智能体。
    输入：人工测试框架 + 可选的静态分析报告
    输出：TestTask 列表
    """

    def __init__(self):
        self._llm = LLMClient()

    def plan(self, framework: dict, static_report=None) -> list[TestTask]:
        """
        framework: 测试框架字典
        static_report: StaticAnalysisReport 对象（可选）
        """
        print("\n[PlannerAgent] 正在根据测试框架生成测试任务...")

        # 构建静态分析摘要
        static_summary = ""
        if static_report and static_report.risk_points:
            static_summary = (
                "\n\n【静态代码分析结果】\n"
                + static_report.summary_for_planner()
            )
            print(f"[PlannerAgent] 已载入静态分析结果（{len(static_report.risk_points)} 个风险点）")

        user_prompt = f"""
请为以下软件项目生成详细测试任务：

项目名称：{framework.get('project_name', '未命名')}
编程语言：{framework.get('language', '未知')}
可执行文件：{framework.get('binary', '')}
项目描述：{framework.get('description', '')}
测试目标：{', '.join(framework.get('test_goals', []))}
补充说明：{framework.get('extra_notes', '无')}
{static_summary}

请生成测试任务 JSON 数组。commands 中的命令假设在项目根目录下执行。
"""
        raw_tasks = self._llm.chat_json(PLANNER_SYSTEM, user_prompt)

        tasks = []
        for t in raw_tasks:
            tasks.append(TestTask(
                task_id=t.get("task_id", "T??"),
                category=t.get("category", "未分类"),
                description=t.get("description", ""),
                commands=t.get("commands", []),
                expected=t.get("expected", ""),
                oracle_type=t.get("oracle_type", "manual"),
                expected_value=t.get("expected_value"),
            ))

        # 统计静态分析驱动的任务数
        static_driven = sum(1 for t in tasks if "静态" in t.category)
        print(f"[PlannerAgent] 共生成 {len(tasks)} 个测试任务"
              + (f"（其中 {static_driven} 个由静态分析驱动）" if static_driven else ""))
        return tasks