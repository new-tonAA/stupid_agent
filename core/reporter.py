# core/reporter.py  —— 测试报告输出器
import json
import os
from datetime import datetime
from agents.executor_agent import TestReport
from config import OUTPUT_DIR


class Reporter:
    """将 TestReport 保存为 JSON + Markdown 两种格式"""

    def save(self, report: TestReport) -> tuple[str, str]:
        """返回 (json_path, md_path)"""
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        base = os.path.join(OUTPUT_DIR, f"report_{ts}")

        json_path = base + ".json"
        md_path = base + ".md"

        self._save_json(report, json_path)
        self._save_markdown(report, md_path)

        return json_path, md_path

    # ──────────────────────────────────────────────

    def _save_json(self, report: TestReport, path: str):
        data = {
            "project": report.project_name,
            "generated_at": datetime.now().isoformat(),
            "summary": {
                "total": report.total,
                "passed": report.passed,
                "failed": report.failed,
                "errors": report.errors,
                "pass_rate": f"{report.pass_rate:.1f}%",
            },
            "results": [r.to_dict() for r in report.results],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"[Reporter] JSON 报告已保存: {path}")

    def _save_markdown(self, report: TestReport, path: str):
        lines = [
            f"# 测试报告：{report.project_name}",
            f"",
            f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"",
            f"## 摘要",
            f"",
            f"| 项目 | 数值 |",
            f"|------|------|",
            f"| 总测试数 | {report.total} |",
            f"| 通过 | {report.passed} |",
            f"| 失败 | {report.failed} |",
            f"| 错误 | {report.errors} |",
            f"| 通过率 | {report.pass_rate:.1f}% |",
            f"",
            f"## 详细结果",
            f"",
        ]

        for r in report.results:
            verdict_icon = {"PASS": "✅", "FAIL": "❌", "ERROR": "⚠️", "TIMEOUT": "⏱️"}.get(r.verdict, "❓")
            lines.append(f"### {verdict_icon} {r.task.task_id} — {r.task.description}")
            lines.append(f"")
            lines.append(f"- **类别**：{r.task.category}")
            lines.append(f"- **判定**：{r.verdict}")
            lines.append(f"- **期望行为**：{r.task.expected}")
            lines.append(f"")

            if r.cmd_results:
                lines.append("**执行命令及结果：**")
                lines.append("")
                for cr in r.cmd_results:
                    lines.append(f"```bash")
                    lines.append(f"$ {cr.command}")
                    if cr.stdout:
                        lines.append(f"# stdout: {cr.stdout[:300]}")
                    if cr.stderr:
                        lines.append(f"# stderr: {cr.stderr[:300]}")
                    lines.append(f"# exit code: {cr.returncode}")
                    lines.append(f"```")
                    lines.append("")

            if r.analysis:
                lines.append(f"**缺陷分析：**")
                lines.append(f"")
                lines.append(f"> {r.analysis.replace(chr(10), '  ')}")
                lines.append(f"")

            lines.append("---")
            lines.append("")

        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print(f"[Reporter] Markdown 报告已保存: {path}")