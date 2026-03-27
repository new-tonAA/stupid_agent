# agents/refinement_agent.py  —— 失败驱动自动精化智能体
"""
职责：
  1. 接收 ExecutorAgent 跑完的 TestReport
  2. 对每个 FAIL/ERROR 任务，分析失败原因
  3. 生成 2~3 个更精准的变体测试任务
  4. 返回新的 TestTask 列表供再次执行

这一步模拟了人工测试中"看到失败 → 缩小范围 → 再测"的思路。
"""

from dataclasses import dataclass
from core.llm_client import LLMClient
from agents.planner_agent import TestTask
from agents.executor_agent import TestReport, TaskResult


REFINE_SYSTEM = """
你是一名资深软件测试工程师。用户会给你一个失败的测试任务及其执行结果。

请你：
1. 分析失败原因（是程序 bug、环境问题、还是测试命令本身有误）
2. 如果是程序 bug 或值得深挖的行为：生成 2~3 个更精准的变体测试
   - 缩小输入范围，找到触发问题的最小用例
   - 尝试边界值的细微变化（+1/-1、空值、极值等）
   - 验证问题是否可复现
3. 如果是环境/命令问题：生成修正后的正确命令重新测试

返回 JSON 数组，每个元素是一个新测试任务：
[
  {
    "task_id": "R01_v1",
    "category": "精化测试",
    "description": "对T01失败的精化：缩小边界找最小触发用例",
    "commands": ["具体可执行的命令"],
    "expected": "期望行为",
    "oracle_type": "stdout_match 或 exit_code 或 crash_check",
    "expected_value": "期望值或null",
    "refine_reason": "为什么生成这个变体（一句话）"
  }
]

注意：
- commands 里的命令必须可以直接在终端执行
- SQL 语句避免单引号字符串，用数字或 char() 函数
- 如果原命令有路径问题，修正路径后重新生成
- 如果失败原因是测试设计本身有缺陷（期望值错误），直接修正
- 最多生成 3 个变体，不要为了数量而生成无意义的重复
"""


@dataclass
class RefinementResult:
    original_task_id: str
    original_verdict: str
    new_tasks: list[TestTask]
    refine_reasons: list[str]


class RefinementAgent:
    """
    Agent 2.5：失败驱动自动精化智能体。
    在 ExecutorAgent 第一轮跑完后运行。
    """

    def __init__(self):
        self._llm = LLMClient()

    def refine(self, report: TestReport, framework: dict) -> list[TestTask]:
        """
        输入：第一轮测试报告
        输出：精化后的新测试任务列表（只针对失败任务）
        """
        failed_results = [
            r for r in report.results
            if r.verdict in ("FAIL", "ERROR") and r.verdict != "TIMEOUT"
        ]

        if not failed_results:
            print("\n[RefinementAgent] 所有测试均通过，无需精化。")
            return []

        print(f"\n[RefinementAgent] 发现 {len(failed_results)} 个失败任务，开始精化...")

        all_new_tasks = []
        refinement_results = []

        for i, result in enumerate(failed_results):
            print(f"  [{i+1}/{len(failed_results)}] 精化任务 {result.task.task_id}: {result.task.description}")
            ref = self._refine_one(result, framework)
            if ref and ref.new_tasks:
                all_new_tasks.extend(ref.new_tasks)
                refinement_results.append(ref)
                for t, reason in zip(ref.new_tasks, ref.refine_reasons):
                    print(f"    → {t.task_id}: {reason}")
            else:
                print(f"    → 无法生成有效变体，跳过")

        print(f"\n[RefinementAgent] 共生成 {len(all_new_tasks)} 个精化测试任务。")
        return all_new_tasks

    def _refine_one(self, result: TaskResult, framework: dict) -> RefinementResult | None:
        """对单个失败任务生成精化变体"""
        tool_path = framework.get("_tool_path", "")

        # 构建失败信息摘要
        cmd_summary = []
        for cr in result.cmd_results:
            cmd_summary.append(
                f"命令: {cr.command}\n"
                f"退出码: {cr.returncode}\n"
                f"stdout: {cr.stdout[:300]}\n"
                f"stderr: {cr.stderr[:300]}"
            )

        user_msg = (
            f"失败任务 {result.task.task_id}：{result.task.description}\n"
            f"类别：{result.task.category}\n"
            f"期望行为：{result.task.expected}\n"
            f"判定结果：{result.verdict}\n\n"
            f"执行详情：\n" + "\n---\n".join(cmd_summary) + "\n\n"
            f"现有 LLM 分析：{result.analysis}\n\n"
            + (f"工具绝对路径（命令必须使用此路径）：\"{tool_path}\"\n" if tool_path else "")
            + "请生成精化测试任务。"
        )

        try:
            raw = self._llm.chat_json(REFINE_SYSTEM, user_msg)
            if not isinstance(raw, list) or not raw:
                return None

            new_tasks = []
            reasons = []
            for i, item in enumerate(raw[:3]):  # 最多取3个
                task = TestTask(
                    task_id=item.get("task_id", f"{result.task.task_id}_r{i+1}"),
                    category=item.get("category", "精化测试"),
                    description=item.get("description", ""),
                    commands=item.get("commands", []),
                    expected=item.get("expected", ""),
                    oracle_type=item.get("oracle_type", "manual"),
                    expected_value=item.get("expected_value"),
                )
                reason = item.get("refine_reason", "无说明")
                new_tasks.append(task)
                reasons.append(reason)

            return RefinementResult(
                original_task_id=result.task.task_id,
                original_verdict=result.verdict,
                new_tasks=new_tasks,
                refine_reasons=reasons,
            )

        except Exception as e:
            print(f"    [!] 精化失败: {e}")
            return None