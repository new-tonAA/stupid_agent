# agents/executor_agent.py  —— 测试执行与分析智能体
"""
判定机制：基于 stdout / stderr / returncode 三元结构的行为验证
"""

import os
from dataclasses import dataclass, field
from core.terminal import TerminalExecutor, CmdResult
from core.llm_client import LLMClient
from agents.planner_agent import TestTask


ANALYZER_SYSTEM = """
你是一名软件测试分析专家。用户会给你一条测试任务的执行结果，
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
    verdict: str
    analysis: str = ""

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
    def __init__(self, workdir: str = "."):
        self._terminal = TerminalExecutor(workdir=workdir)
        self._llm = LLMClient()
        self._tool_path = ""

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

    def _run_task(self, task: TestTask) -> TaskResult:
        cmd_results = []
        for cmd in task.commands:
            cmd_results.append(self._terminal.run(self._fix_cmd_path(cmd)))

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

    # ── 三元结构判定核心 ──────────────────────────────────────────

    def _judge(self, task: TestTask, result: CmdResult | None) -> tuple[bool, str]:
        """
        基于 stdout / stderr / returncode 三元结构判定。
        oracle_type 说明：
          exit_code       → 只看 returncode
          stdout_match    → stdout 或 stderr 包含期望字符串
          stdout_exact    → stdout 精确匹配
          stderr_match    → stderr 包含期望字符串
          any_output_match→ stdout 或 stderr 任一包含期望字符串
          no_crash        → returncode 不是崩溃码（取代旧的 crash_check）
          crash_check     → 兼容旧格式，等同于 no_crash
          manual          → 人工判断，默认 PASS
        """
        if result is None:
            return False, "ERROR"
        if result.timed_out:
            return False, "TIMEOUT"
        if result.error:
            return False, "ERROR"

        ot = task.oracle_type
        ev = str(task.expected_value or "").strip()

        # ── returncode 判定 ────────────────────────────────────────
        if ot == "exit_code":
            return self._judge_exit_code(result, ev)

        # ── stdout 精确匹配 ────────────────────────────────────────
        elif ot == "stdout_exact":
            passed = result.stdout.strip() == ev
            return passed, "PASS" if passed else "FAIL"

        # ── stdout 包含匹配（同时检查 stderr） ────────────────────
        elif ot in ("stdout_match", "any_output_match"):
            # 同时搜索 stdout 和 stderr，避免输出通道混淆
            combined = result.stdout + "\n" + result.stderr
            passed = ev.lower() in combined.lower()
            return passed, "PASS" if passed else "FAIL"

        # ── 仅 stderr 匹配 ─────────────────────────────────────────
        elif ot == "stderr_match":
            passed = ev.lower() in result.stderr.lower()
            return passed, "PASS" if passed else "FAIL"

        # ── 不崩溃判定（取代 crash_check）─────────────────────────
        elif ot in ("no_crash", "crash_check"):
            # 崩溃码：segfault=-11, abort=-6, 139, 134
            crash_codes = {-11, -6, 139, 134}
            crashed = result.returncode in crash_codes or (
                result.returncode < -1 and result.returncode not in {-1}
            )
            return not crashed, "PASS" if not crashed else "FAIL"

        # ── 语义级判定：让 LLM 判断行为是否符合预期 ───────────────
        elif ot == "semantic":
            return self._judge_semantic(task, result)

        # ── 人工判定 ───────────────────────────────────────────────
        elif ot == "manual":
            print(f"  [Manual] stdout={result.stdout!r} stderr={result.stderr!r} rc={result.returncode}")
            return True, "PASS"

        return False, "ERROR"

    def _judge_exit_code(self, result: CmdResult, expected: str) -> tuple[bool, str]:
        """
        退出码判定，支持：
          "0"       → 期望成功
          "nonzero" → 期望任意非零（程序正确报错）
          "1"       → 期望具体退出码
          "19"      → SQLite CONSTRAINT 错误码
          "1,19"    → 期望其中之一
        """
        rc = result.returncode
        expected_lower = expected.lower()

        if expected_lower == "nonzero":
            passed = rc != 0
        elif expected_lower in ("0", "zero", "success"):
            passed = rc == 0
        elif "," in expected:
            # 多个可接受的退出码
            acceptable = {int(x.strip()) for x in expected.split(",") if x.strip().isdigit()}
            passed = rc in acceptable
        elif expected.lstrip("-").isdigit():
            passed = rc == int(expected)
        else:
            # 兜底：非零就算过（保守）
            passed = rc == 0

        return passed, "PASS" if passed else "FAIL"

    def _judge_semantic(self, task: TestTask, result: CmdResult) -> tuple[bool, str]:
        """让 LLM 从语义层面判断行为是否符合预期"""
        system = (
            "你是测试判定专家。根据测试意图和实际执行结果，判断测试是否通过。"
            "只返回 PASS 或 FAIL，不要任何解释。"
        )
        user = (
            f"测试描述：{task.description}\n"
            f"期望行为：{task.expected}\n"
            f"实际 returncode：{result.returncode}\n"
            f"实际 stdout：{result.stdout[:300]}\n"
            f"实际 stderr：{result.stderr[:300]}"
        )
        try:
            answer = self._llm.chat(system, user, max_tokens=10).strip().upper()
            passed = "PASS" in answer
            return passed, "PASS" if passed else "FAIL"
        except Exception:
            return False, "ERROR"

    def _analyze_failure(self, task: TestTask, result: CmdResult) -> str:
        """调用 LLM 分析失败原因"""
        print(f"  [ExecutorAgent] 正在分析失败原因...")
        user_msg = (
            f"测试任务：{task.description}\n"
            f"期望行为：{task.expected}\n"
            f"oracle类型：{task.oracle_type}，期望值：{task.expected_value}\n"
            f"执行命令：{result.command}\n"
            f"退出码：{result.returncode}\n"
            f"stdout：{result.stdout[:500]}\n"
            f"stderr：{result.stderr[:500]}\n"
            f"是否超时：{result.timed_out}"
        )
        try:
            return self._llm.chat(ANALYZER_SYSTEM, user_msg)
        except Exception as e:
            return f"（LLM 分析失败: {e}）"

    def _fix_cmd_path(self, cmd: str) -> str:
        """把命令里所有非绝对路径的工具调用替换成绝对路径"""
        if not self._tool_path:
            return cmd

        import re
        tool_name = os.path.basename(self._tool_path)
        tool_base = os.path.splitext(tool_name)[0]
        quoted = f'"{self._tool_path}"'

        if self._tool_path.lower() in cmd.lower():
            return cmd

        original = cmd
        subs = [
            (f'".\\{tool_name}"', quoted),
            (f'.\\{tool_name}', quoted),
            (f'"./{tool_name}"', quoted),
            (f'./{tool_name}', quoted),
            (f'".\\{tool_base}"', quoted),
            (f'.\\{tool_base}', quoted),
            (f'"./{tool_base}"', quoted),
            (f'./{tool_base}', quoted),
        ]
        for old_s, new_s in subs:
            if old_s.lower() in cmd.lower():
                idx = cmd.lower().find(old_s.lower())
                cmd = cmd[:idx] + new_s + cmd[idx + len(old_s):]
                break

        if cmd == original:
            for bare in [tool_name, tool_base]:
                if cmd.strip().lower().startswith(bare.lower()):
                    cmd = quoted + cmd.strip()[len(bare):]
                    break

        if cmd != original:
            print(f"  [ExecutorAgent] 路径已修正 → {quoted}")
        return cmd
