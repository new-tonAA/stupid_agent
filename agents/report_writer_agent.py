# agents/report_writer_agent.py  —— 报告撰写智能体
"""
职责：
  接收测试结果，调用 LLM 为每个失败/有趣的测试用例
  撰写详细的自然语言分析段落，让报告更易于人类理解。
"""

from core.llm_client import LLMClient
from agents.executor_agent import TestReport, TaskResult


WRITER_SYSTEM = """
你是一名专业的软件测试工程师，正在撰写测试报告。
用户会给你一条测试用例的执行结果，请用清晰、专业的中文写一段测试分析。

要求：
1. 说明测试了什么功能/场景
2. 实际执行结果是什么（具体输出、退出码）
3. 是否发现了程序缺陷，如果是，缺陷的性质是什么
4. 这个结果对软件质量意味着什么
5. 语言自然，像人写的，不要用"首先、其次、最后"这种套话

控制在 150 字以内，不要分点，写成一段话。
"""

SUMMARY_SYSTEM = """
你是一名专业的软件测试工程师，正在撰写测试报告的总结章节。
用户会给你整轮测试的统计数据和所有失败用例的摘要，
请写一段 200~300 字的测试总结，包括：
1. 总体质量评价
2. 发现的主要问题类型
3. 哪些方面表现良好
4. 对被测软件的整体评价
5. 简单的改进建议

语言专业自然，像真实测试报告的结论章节。
"""


class ReportWriterAgent:
    """
    调用 LLM 为测试报告撰写自然语言分析。
    """

    def __init__(self):
        self._llm = LLMClient()

    def write_case_analysis(self, result: TaskResult) -> str:
        """为单个测试用例撰写分析段落"""
        # 构建用例信息
        cmd_info = ""
        if result.cmd_results:
            last = result.cmd_results[-1]
            cmd_info = (
                f"执行命令：{last.command[:200]}\n"
                f"退出码：{last.returncode}\n"
                f"标准输出：{last.stdout[:300] or '（无输出）'}\n"
                f"错误输出：{last.stderr[:300] or '（无）'}\n"
                f"是否超时：{last.timed_out}"
            )

        user_msg = (
            f"测试用例：{result.task.description}\n"
            f"测试类别：{result.task.category}\n"
            f"期望行为：{result.task.expected}\n"
            f"判定结果：{result.verdict}\n\n"
            f"{cmd_info}"
        )

        try:
            return self._llm.chat(WRITER_SYSTEM, user_msg, max_tokens=300)
        except Exception as e:
            return f"（分析生成失败：{e}）"

    def write_overall_summary(self, report: TestReport, static_report=None, confirmed_bugs: list = None) -> str:
        """撰写整体测试总结"""
        failed = [r for r in report.results if not r.passed]
        failed_descs = "\n".join(
            f"- {r.task.task_id} [{r.verdict}] {r.task.description}"
            for r in failed[:10]  # 最多取10个
        )

        static_info = ""
        if static_report and static_report.risk_points:
            high = sum(1 for r in static_report.risk_points if r.severity == "high")
            mid = sum(1 for r in static_report.risk_points if r.severity == "medium")
            static_info = (
                f"\n静态分析发现风险点：高危 {high} 个，中危 {mid} 个，"
                f"共 {len(static_report.risk_points)} 个。"
            )

        bug_info = ""
        if confirmed_bugs:
            bug_descs = "\n".join(
                f"- {r.task.task_id}: {r.task.description}"
                for r in confirmed_bugs[:5]
            )
            bug_info = f"\n多轮精化后确认的疑似程序缺陷：\n{bug_descs}"
        else:
            bug_info = "\n多轮精化后未确认程序缺陷（失败均为测试设计问题）。"

        user_msg = (
            f"被测项目：{report.project_name}\n"
            f"测试用例总数：{report.total}\n"
            f"通过：{report.passed}，失败：{report.failed}，错误：{report.errors}\n"
            f"通过率：{report.pass_rate:.1f}%\n"
            f"{static_info}\n"
            f"失败用例列表：\n{failed_descs or '无'}\n"
            f"{bug_info}\n"
        )

        try:
            return self._llm.chat(SUMMARY_SYSTEM, user_msg, max_tokens=500)
        except Exception as e:
            return f"（总结生成失败：{e}）"