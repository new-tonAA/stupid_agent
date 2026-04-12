# agents/refinement_agent.py  —— 失败驱动自动精化智能体
from dataclasses import dataclass
from core.llm_client import LLMClient
from agents.planner_agent import TestTask
from agents.executor_agent import TestReport, TaskResult


REFINE_SYSTEM = """
你是一名资深软件测试工程师，正在对失败的测试用例进行精化分析。

【核心原则】精化测试必须基于原始失败场景，不能换题！

任务：
1. 首先判断失败原因：
   - 类型A：程序本身的缺陷（输出错误、崩溃、异常行为）
   - 类型B：测试命令/期望值设计有误（命令语法错、期望值不对）
   - 类型C：环境问题（路径错误、工具未找到）

2. 针对不同类型生成精化变体：
   - 类型A（程序缺陷）：
     * 必须使用与原始命令相同或更小的输入范围
     * 尝试 +1/-1、0、负数、最大值等边界微调
     * 目标：找到触发缺陷的最小输入，确认缺陷可复现
     * oracle_type 必须能检测到异常（用 stdout_match 或 exit_code）
   - 类型B（设计有误）：
     * 修正命令或期望值，重新测试同一场景
   - 类型C（环境问题）：
     * 使用提供的绝对路径修正命令

【严禁】：
- 不能生成与原始失败场景完全无关的新测试
- 不能把 oracle_type 设为 crash_check（太宽松，几乎必过）
- 不能修改 expected 使其与实际输出一致（掩盖问题）

返回 JSON 数组：
[
  {
    "task_id": "原任务ID_v1",
    "category": "精化测试",
    "description": "对[原任务ID]的精化：具体说明",
    "commands": ["使用绝对路径的完整命令"],
    "expected": "期望行为",
    "oracle_type": "stdout_match 或 exit_code（不要用 crash_check）",
    "expected_value": "具体期望值",
    "failure_type": "A 或 B 或 C",
    "refine_reason": "为什么生成这个变体"
  }
]

SQL注意：避免单引号字符串，用数字或 char() 函数。
"""


@dataclass
class RefinementResult:
    original_task_id: str
    original_verdict: str
    new_tasks: list[TestTask]
    refine_reasons: list[str]


class RefinementAgent:
    def __init__(self):
        self._llm = LLMClient()

    def refine(self, report: TestReport, framework: dict) -> list[TestTask]:
        failed_results = [
            r for r in report.results
            if r.verdict in ("FAIL", "ERROR") and r.verdict != "TIMEOUT"
        ]

        if not failed_results:
            print("\n[RefinementAgent] 所有测试均通过，无需精化。")
            return []

        print(f"\n[RefinementAgent] 发现 {len(failed_results)} 个失败任务，开始精化...")

        all_new_tasks = []
        for i, result in enumerate(failed_results):
            print(f"  [{i+1}/{len(failed_results)}] 精化任务 {result.task.task_id}: {result.task.description}")
            ref = self._refine_one(result, framework)
            if ref and ref.new_tasks:
                all_new_tasks.extend(ref.new_tasks)
                for t, reason in zip(ref.new_tasks, ref.refine_reasons):
                    print(f"    → {t.task_id}: {reason}")
            else:
                print(f"    → 无法生成有效变体，跳过")

        print(f"\n[RefinementAgent] 共生成 {len(all_new_tasks)} 个精化测试任务。")
        return all_new_tasks

    def _refine_one(self, result: TaskResult, framework: dict) -> RefinementResult | None:
        tool_path = framework.get("_tool_path", "")

        cmd_summary = []
        for cr in result.cmd_results:
            cmd_summary.append(
                f"命令: {cr.command}\n"
                f"退出码: {cr.returncode}\n"
                f"stdout: {cr.stdout[:300]}\n"
                f"stderr: {cr.stderr[:300]}"
            )

        # 分析原始任务的期望意图
        orig_intent = ""
        if result.task.oracle_type == "exit_code":
            if str(result.task.expected_value) == "0":
                orig_intent = "原测试期望程序成功执行（退出码0）"
            else:
                orig_intent = "原测试期望程序报错（退出码非0）"
        elif result.task.oracle_type == "stdout_match":
            orig_intent = f"原测试期望输出包含：{result.task.expected_value}"
        elif result.task.oracle_type == "stdout_exact":
            orig_intent = f"原测试期望输出精确为：{result.task.expected_value}"

        # 分析实际结果
        actual_summary = ""
        if result.cmd_results:
            last = result.cmd_results[-1]
            actual_summary = (
                f"实际退出码: {last.returncode}\n"
                f"实际stdout: {last.stdout[:200] or '（无输出）'}\n"
                f"实际stderr: {last.stderr[:200] or '（无）'}"
            )

        user_msg = (
            f"失败任务 {result.task.task_id}：{result.task.description}\n"
            f"类别：{result.task.category}\n"
            f"期望行为：{result.task.expected}\n"
            f"oracle类型：{result.task.oracle_type}，期望值：{result.task.expected_value}\n"
            f"测试意图：{orig_intent}\n"
            f"判定结果：{result.verdict}（失败原因：期望与实际不符）\n\n"
            f"【实际执行结果】\n{actual_summary}\n\n"
            f"【完整执行详情】\n" + "\n---\n".join(cmd_summary) + "\n\n"
            f"现有分析：{result.analysis}\n\n"
            + (f"工具绝对路径（命令必须使用）：\"{tool_path}\"\n" if tool_path else "")
            + "\n【重要】请基于以上实际失败场景生成精化任务。\n"
            + "精化任务的 oracle_type 和 expected_value 必须与原始测试意图一致，\n"
            + "不能因为实际输出是非零就把 expected_value 改成 nonzero 来蒙混过关。"
        )

        try:
            raw = self._llm.chat_json(REFINE_SYSTEM, user_msg)
            if not isinstance(raw, list) or not raw:
                return None

            new_tasks = []
            reasons = []
            for i, item in enumerate(raw[:3]):
                task = TestTask(
                    task_id=item.get("task_id", f"{result.task.task_id}_r{i+1}"),
                    category=item.get("category", "精化测试"),
                    description=item.get("description", ""),
                    commands=item.get("commands", []),
                    expected=item.get("expected", ""),
                    oracle_type=item.get("oracle_type", "manual"),
                    expected_value=item.get("expected_value"),
                )
                task = self._validate_task(task, result)
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

    def _validate_task(self, task: TestTask, original: TaskResult) -> TestTask:
        """校验精化任务合理性，防止过于宽松的判定"""
        # crash_check 太宽松，改为 exit_code 期望 0
        if task.oracle_type == "crash_check":
            task.oracle_type = "exit_code"
            task.expected_value = "0"

        # stdout_match/exact 没有 expected_value → 改 manual
        if task.oracle_type in ("stdout_match", "stdout_exact") and not task.expected_value:
            task.oracle_type = "manual"

        # exit_code + nonzero：如果原始任务期望的是 0（成功），不能改成 nonzero
        if (task.oracle_type == "exit_code"
                and str(task.expected_value).lower() == "nonzero"
                and original.task.oracle_type == "exit_code"
                and str(original.task.expected_value) == "0"):
            # 原始期望成功，精化也应该期望成功
            task.expected_value = "0"
            task.description = "[已修正：原任务期望退出码0] " + task.description

        # 命令与原始完全相同 → 加警告标记
        orig_cmds = [cr.command for cr in original.cmd_results]
        if task.commands and task.commands[0] in orig_cmds:
            task.description = (
                f"[命令与原始相同，结果可能仍为{original.verdict}] "
                + task.description
            )

        return task