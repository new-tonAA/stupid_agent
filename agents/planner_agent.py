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
- SQLite3 退出码说明（设置 expected_value 时必须参考）：
  * 退出码 0：执行成功
  * 退出码 1：SQL语法错误或表不存在等运行时错误（SQLITE_ERROR）
  * 退出码 19：约束违反，如UNIQUE/NOT NULL/FOREIGN KEY（SQLITE_CONSTRAINT）
  * 退出码 14：无法打开数据库文件（SQLITE_CANTOPEN）
  * 测试"查询不存在的表"期望退出码应为 1（nonzero），不是 0
  * 测试"UNIQUE约束违反"期望退出码应为 19
  * 测试"正常查询"期望退出码应为 0
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

        # 从 EnvAgent 获取绝对路径，确保跨机器可用
        tool_path = framework.get('_tool_path') or framework.get('binary', '')
        run_prefix = framework.get('_run_prefix', tool_path)

        user_prompt = f"""
请为以下软件项目生成详细测试任务：

项目名称：{framework.get("project_name", "未命名")}
编程语言：{framework.get("language", "未知")}
【重要】可执行文件绝对路径：{tool_path}
【重要】调用命令必须使用此绝对路径，不要用相对路径或只写文件名
项目描述：{framework.get("description", "")}
测试目标：{", ".join(framework.get("test_goals", []))}
补充说明：{framework.get("extra_notes", "无")}
{static_summary}

请生成测试任务 JSON 数组。
commands 中每条命令必须使用上方提供的【绝对路径】调用可执行文件（路径含空格必须加双引号），例如：
"{tool_path}" :memory: "SELECT 1+1;"
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

        # 自动校验 oracle：试运行每个命令，修正明显错误的期望值
        tasks = self._validate_oracles(tasks, framework)
        return tasks

    def _validate_oracles(self, tasks: list, framework: dict) -> list:
        """
        试运行每条命令，检查 expected_value 是否合理。
        如果期望退出码 0 但实际是非零（且分析认为是正常行为），自动修正。
        """
        from core.terminal import TerminalExecutor
        import os
        terminal = TerminalExecutor(workdir=os.getcwd())

        print(f"[PlannerAgent] 自动校验 oracle（试运行 {len(tasks)} 个任务）...")
        fixed_count = 0

        for task in tasks:
            if not task.commands:
                continue
            if task.oracle_type not in ("exit_code", "stdout_exact"):
                continue  # 只校验依赖具体值的 oracle

            # 试运行第一条命令
            cmd = task.commands[0]
            result = terminal.run(cmd, timeout=10)

            # 如果期望退出码 0 但实际非零
            if (task.oracle_type == "exit_code"
                    and str(task.expected_value) == "0"
                    and result.returncode != 0):
                # 让 LLM 判断实际退出码是否正常
                verdict = self._ask_if_normal(task, result)
                if verdict == "normal":
                    # 程序行为正常，修正期望值
                    task.expected_value = "nonzero"
                    task.expected = (
                        f"程序正确返回错误码 {result.returncode}（{task.expected}）"
                    )
                    fixed_count += 1
                    print(f"  [校验] {task.task_id} 期望值修正：0 → nonzero"
                          f"（实际退出码 {result.returncode}）")

            # 如果期望某个 stdout 但实际为空
            elif (task.oracle_type == "stdout_exact"
                    and task.expected_value
                    and not result.stdout
                    and result.returncode != 0):
                task.oracle_type = "exit_code"
                task.expected_value = "nonzero"
                fixed_count += 1
                print(f"  [校验] {task.task_id} oracle 类型修正：stdout_exact → exit_code nonzero")

        if fixed_count:
            print(f"[PlannerAgent] 共修正 {fixed_count} 个 oracle 期望值")
        else:
            print(f"[PlannerAgent] oracle 校验完成，无需修正")
        return tasks

    def _ask_if_normal(self, task, result) -> str:
        """
        让 LLM 判断命令的实际退出码是否属于正常行为。
        返回 "normal"（正常）或 "bug"（可能是缺陷）
        """
        system = (
            "你是软件测试专家。根据命令执行结果，判断程序行为是否正常。"
            "如果退出码非零是程序正确处理错误输入/边界条件的结果，返回 normal。"
            "如果退出码非零是程序崩溃或异常，返回 bug。"
            "只返回 normal 或 bug 两个词之一，不要其他内容。"
        )
        user = (
            f"测试描述：{task.description}\n"
            f"执行命令：{result.command}\n"
            f"退出码：{result.returncode}\n"
            f"stdout：{result.stdout[:200]}\n"
            f"stderr：{result.stderr[:200]}"
        )
        try:
            answer = self._llm.chat(system, user, max_tokens=10).strip().lower()
            return "normal" if "normal" in answer else "bug"
        except Exception:
            return "bug"  # 不确定时保守处理，不修改期望值