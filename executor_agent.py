# agents/executor_agent.py  —— Agent 2：测试执行与分析智能体
"""
职责：
  1. 接收 PlannerAgent 生成的测试任务列表
  2. 调用 TerminalExecutor 在本地终端逐条执行测试命令
  3. 根据 oracle_type 自动判定测试是否通过
  4. 对失败/异常的测试，调用 LLM 进行简短原因分析
  5. 汇总所有结果，返回 TestReport
"""

from dataclasses import dataclass, field
from core.terminal import TerminalExecutor, CmdResult
from core.llm_client import LLMClient
from agents.planner_agent import TestTask


ANALYZER_SYSTEM = """
你是一名软件测试分析专家。用户会给你一条测试任务的信息以及实际执行结果，
请用 2~4 句中文分析：
1. 测试是否发现了程序缺陷？
2. 如果是，缺陷的可能原因是什么？
3. 触发缺陷的输入或场景是什么？
回答简洁，不要废话。
"""


@dataclass
class TaskResult:
    task: TestTask
    cmd_results: list[CmdResult]
    passed: bool
    verdict: str          # PASS / FAIL / ERROR / TIMEOUT
    analysis: str = ""    # LLM 分析文字（仅失败时填写）

    def to_dict(self) -> dict:
        return {
            "task_id": self.task.task_id,
            "category": self.task.category,
            "description": self.task.description,
            "verdict": self.verdict,
            "passed": self.passed,
            "analysis": self.analysis,
            "commands": [r.to_dict() for r in self.cmd_results],
        }


@dataclass
class TestReport:
    project_name: str
    total: int = 0
    passed: int = 0
    failed: int = 0
    errors: int = 0
    results: list[TaskResult] = field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total * 100 if self.total else 0

    def summary(self) -> str:
        lines = [
            f"\n{'='*60}",
            f"  测试报告 —— {self.project_name}",
            f"{'='*60}",
            f"  总计: {self.total}  通过: {self.passed}  失败: {self.failed}  错误: {self.errors}",
            f"  通过率: {self.pass_rate:.1f}%",
            f"{'='*60}",
        ]
        for r in self.results:
            icon = "✅" if r.passed else "❌"
            lines.append(f"  {icon} [{r.verdict}] {r.task.task_id} - {r.task.description}")
            if not r.passed and r.analysis:
                for line in r.analysis.strip().split("\n"):
                    lines.append(f"       💬 {line}")
        lines.append("=" * 60)
        return "\n".join(lines)


class ExecutorAgent:
    """
    Agent 2：测试执行智能体。
    """

    def __init__(self, workdir: str = "."):
        self._terminal = TerminalExecutor(workdir=workdir)
        self._llm = LLMClient()
        self._tool_path = ""  # 由 run_all 从 framework 注入

    def run_all(self, tasks: list[TestTask], project_name: str = "项目",
                framework: dict = None) -> TestReport:
        report = TestReport(project_name=project_name, total=len(tasks))
        self._tool_path = (framework or {}).get("_tool_path", "")

        for i, task in enumerate(tasks):
            print(f"\n[ExecutorAgent] 执行任务 {task.task_id} ({i+1}/{len(tasks)}): {task.description}")
            result = self._run_task(task)
            report.results.append(result)
            if result.passed:
                report.passed += 1
            elif result.verdict == "ERROR":
                report.errors += 1
            else:
                report.failed += 1

        return report

    # ──────────────────────────────────────────────────
    # 内部方法
    # ──────────────────────────────────────────────────

    def _run_task(self, task: TestTask) -> TaskResult:
        cmd_results = []
        for cmd in task.commands:
            cmd_results.append(self._terminal.run(self._fix_cmd_path(cmd)))

        # 使用最后一条命令的结果作为主结果
        last = cmd_results[-1] if cmd_results else None
        passed, verdict = self._judge(task, last)

        analysis = ""
        if not passed and last is not None:
            analysis = self._analyze_failure(task, last)

        return TaskResult(
            task=task,
            cmd_results=cmd_results,
            passed=passed,
            verdict=verdict,
            analysis=analysis,
        )

    def _judge(self, task: TestTask, result: CmdResult | None) -> tuple[bool, str]:
        """根据 oracle_type 自动判定是否通过"""
        if result is None:
            return False, "ERROR"

        if result.timed_out:
            return False, "TIMEOUT"

        if result.error:
            return False, "ERROR"

        ot = task.oracle_type

        if ot == "crash_check":
            # 程序不应崩溃（signal kill / segfault → returncode < 0 或 139）
            crashed = result.returncode in (-11, -6, 139, 134) or result.returncode < -1
            if crashed:
                return False, "FAIL"
            return True, "PASS"

        elif ot == "exit_code":
            # expected_value: "0" → 期望成功；"nonzero" → 期望失败
            ev = str(task.expected_value or "0").strip().lower()
            if ev == "nonzero":
                passed = result.returncode != 0
            else:
                passed = result.returncode == int(ev)
            return passed, "PASS" if passed else "FAIL"

        elif ot == "stdout_match":
            ev = str(task.expected_value or "")
            passed = ev.lower() in result.stdout.lower()
            return passed, "PASS" if passed else "FAIL"

        elif ot == "stdout_exact":
            ev = str(task.expected_value or "").strip()
            passed = result.stdout.strip() == ev
            return passed, "PASS" if passed else "FAIL"

        elif ot == "manual":
            # 人工判断：默认标记为 PASS，输出供人工审阅
            print(f"  [Manual] 请人工判断此测试是否通过。stdout={result.stdout!r}")
            return True, "PASS"

        else:
            return False, "ERROR"

    def _analyze_failure(self, task: TestTask, result: CmdResult) -> str:
        """调用 LLM 分析失败原因"""
        print(f"  [ExecutorAgent] 正在分析失败原因...")
        user_msg = f"""
测试任务：{task.description}
期望行为：{task.expected}
执行命令：{result.command}
退出码：{result.returncode}
stdout：{result.stdout[:500]}
stderr：{result.stderr[:500]}
是否超时：{result.timed_out}
"""
        try:
            return self._llm.chat(ANALYZER_SYSTEM, user_msg)
        except Exception as e:
            return f"（LLM 分析失败: {e}）"

    def _fix_cmd_path(self, cmd: str) -> str:
        """
        如果命令里用了相对路径（.\\sqlite3.exe 或 sqlite3），
        且我们有绝对路径，自动替换，确保跨机器可用。
        """
        if not self._tool_path:
            return cmd

        import os
        tool_name = os.path.basename(self._tool_path)          # sqlite3.exe
        tool_name_no_ext = os.path.splitext(tool_name)[0]      # sqlite3

        replacements = [
            (f".\\{tool_name}", f'"{self._tool_path}"'),
            (f"./{tool_name}", f'"{self._tool_path}"'),
            (f".\\{tool_name_no_ext}", f'"{self._tool_path}"'),
            (f"./{tool_name_no_ext}", f'"{self._tool_path}"'),
        ]
        for old_str, new_str in replacements:
            if old_str in cmd:
                cmd = cmd.replace(old_str, new_str)
                print(f"  [ExecutorAgent] 路径已修正: {old_str} → {new_str}")
                return cmd

        # 如果命令以裸工具名开头（没有路径前缀），也替换
        import re
        pattern = rf'^{re.escape(tool_name_no_ext)}(?:\.exe)?\b'
        if re.match(pattern, cmd.strip(), re.IGNORECASE):
            cmd = re.sub(pattern, f'"{self._tool_path}"', cmd.strip(), count=1, flags=re.IGNORECASE)
            print(f"  [ExecutorAgent] 裸名已修正 → 使用绝对路径")

        return cmd