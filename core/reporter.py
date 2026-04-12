# core/reporter.py  —— 测试报告输出器
import json
import os
import platform
from datetime import datetime
from agents.executor_agent import TestReport
from config import OUTPUT_DIR


class Reporter:
    def save(self, report: TestReport) -> tuple[str, str]:
        os.makedirs(OUTPUT_DIR, exist_ok=True)  # 确保目录存在
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        base = os.path.join(OUTPUT_DIR, f"report_{ts}")
        json_path = base + ".json"
        md_path = base + ".md"
        self._save_json(report, json_path)
        self._save_markdown(report, md_path)
        return json_path, md_path

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
        print(f"[Reporter] JSON: {path}")

    def _save_markdown(self, report: TestReport, path: str):
        lines = []

        # ── 封面 ──────────────────────────────────────────────────
        lines += [
            "# 软件测试报告",
            "",
            f"**被测项目**：{report.project_name}  ",
            f"**生成时间**：{datetime.now().strftime('%Y年%m月%d日 %H:%M:%S')}  ",
            f"**测试方法**：黑盒测试 + 静态代码分析驱动白盒测试  ",
            "",
            "---",
            "",
        ]

        # ── 一、测试摘要 ───────────────────────────────────────────
        pass_rate = report.pass_rate
        if pass_rate == 100:
            overall = "✅ 全部通过"
        elif pass_rate >= 80:
            overall = "🟡 基本通过"
        else:
            overall = "🔴 存在较多失败"

        bar_len = 30
        filled = int(bar_len * report.passed / report.total) if report.total else 0
        bar = "█" * filled + "░" * (bar_len - filled)

        lines += [
            "## 一、测试摘要",
            "",
            f"**总体结论**：{overall}",
            "",
            "| 指标 | 数值 |",
            "|------|------|",
            f"| 测试用例总数 | {report.total} |",
            f"| 通过（PASS） | ✅ {report.passed} |",
            f"| 失败（FAIL） | ❌ {report.failed} |",
            f"| 错误（ERROR）| ⚠️ {report.errors} |",
            f"| 通过率 | **{pass_rate:.1f}%** |",
            "",
            "```",
            f"通过率  [{bar}] {pass_rate:.1f}%",
            "```",
            "",
            "---",
            "",
        ]

        # ── 二、测试分类统计 ───────────────────────────────────────
        category_stats: dict = {}
        for r in report.results:
            cat = r.task.category
            if cat not in category_stats:
                category_stats[cat] = {"total": 0, "passed": 0, "failed": 0}
            category_stats[cat]["total"] += 1
            if r.passed:
                category_stats[cat]["passed"] += 1
            else:
                category_stats[cat]["failed"] += 1

        lines += [
            "## 二、测试分类统计",
            "",
            "| 测试类别 | 总数 | 通过 | 失败 | 通过率 |",
            "|----------|------|------|------|--------|",
        ]
        for cat, s in category_stats.items():
            rate = s["passed"] / s["total"] * 100 if s["total"] else 0
            icon = "✅" if s["failed"] == 0 else "❌"
            lines.append(f"| {icon} {cat} | {s['total']} | {s['passed']} | {s['failed']} | {rate:.0f}% |")
        lines += ["", "---", ""]

        # ── 三、全部测试用例详情（含 LLM 分析）────────────────────
        lines += [
            "## 三、测试用例详情",
            "",
            "> 以下为每个测试用例的执行情况，失败用例附有详细的 AI 分析。",
            "",
        ]

        # 询问是否调用 LLM 写分析（控制费用）
        print("\n[Reporter] 是否调用 AI 为每个测试用例撰写详细分析？（会消耗少量 API）")
        use_writer = input("  输入 y 启用，其他键跳过：").strip().lower() == "y"

        writer = None
        if use_writer:
            from agents.report_writer_agent import ReportWriterAgent
            writer = ReportWriterAgent()
            print("[Reporter] AI 报告撰写已启用，正在逐条分析...")

        for r in report.results:
            verdict_icon = {"PASS": "✅", "FAIL": "❌", "ERROR": "⚠️", "TIMEOUT": "⏱️"}.get(r.verdict, "❓")

            lines += [
                f"### {verdict_icon} {r.task.task_id}　{r.task.description}",
                "",
                f"| 项目 | 内容 |",
                f"|------|------|",
                f"| 测试类别 | {r.task.category} |",
                f"| 判定结果 | {verdict_icon} **{r.verdict}** |",
                f"| 期望行为 | {r.task.expected} |",
                "",
            ]

            # 展示所有执行命令
            if r.cmd_results:
                lines.append("**执行过程：**")
                lines.append("")
                for j, cr in enumerate(r.cmd_results):
                    lines += [
                        f"```bash",
                        f"# 命令 {j+1}",
                        f"$ {cr.command}",
                    ]
                    if cr.stdout:
                        # 完整展示输出（截断超长部分）
                        stdout_display = cr.stdout[:500]
                        if len(cr.stdout) > 500:
                            stdout_display += f"\n...（共 {len(cr.stdout)} 字符，已截断）"
                        lines.append(f"# 输出:")
                        for out_line in stdout_display.splitlines():
                            lines.append(f"  {out_line}")
                    if cr.stderr:
                        stderr_display = cr.stderr[:300]
                        lines.append(f"# 错误信息: {stderr_display}")
                    lines.append(f"# 退出码: {cr.returncode}")
                    if cr.timed_out:
                        lines.append("# ⏱️ 命令超时！")
                    lines.append("```")
                    lines.append("")

            # AI 分析（失败时必写，通过时可选）
            if writer and (not r.passed or r.task.category == "静态分析驱动"):
                print(f"  分析 {r.task.task_id}...")
                analysis = writer.write_case_analysis(r)
                lines += [
                    "**📝 测试分析：**",
                    "",
                    f"> {analysis.strip()}",
                    "",
                ]
            elif r.analysis:
                # 用原有的简短分析
                lines += [
                    "**📝 测试分析：**",
                    "",
                    f"> {r.analysis.strip()}",
                    "",
                ]

            lines += ["---", ""]

        # ── 四、问题汇总 ───────────────────────────────────────────
        failed_results = [r for r in report.results if not r.passed]
        lines += ["## 四、问题汇总", ""]

        if not failed_results:
            lines += ["> ✅ 本轮测试未发现程序缺陷，所有用例均通过。", "", "---", ""]
        else:
            # 区分"程序缺陷"和"测试设计问题"
            # 如果 LLM 分析里包含"未发现缺陷"/"符合预期"，归类为测试设计问题
            design_issues = []
            real_bugs = []
            for r in failed_results:
                analysis_lower = (r.analysis or "").lower()
                if any(kw in analysis_lower for kw in
                       ["未发现缺陷", "符合预期", "测试设计", "期望值", "oracle", "正常行为", "正确行为"]):
                    design_issues.append(r)
                else:
                    real_bugs.append(r)

            if real_bugs:
                lines += [
                    f"### 🐛 疑似程序缺陷（{len(real_bugs)} 个）",
                    "",
                    "| 编号 | 描述 | 退出码 | 实际输出 |",
                    "|------|------|--------|----------|",
                ]
                for r in real_bugs:
                    exit_code = r.cmd_results[-1].returncode if r.cmd_results else "N/A"
                    stdout = r.cmd_results[-1].stdout[:30] if r.cmd_results else ""
                    desc = r.task.description[:30] + "..." if len(r.task.description) > 30 else r.task.description
                    lines.append(f"| {r.task.task_id} | {desc} | {exit_code} | {stdout} |")
                lines += [""]

            if design_issues:
                lines += [
                    f"### ⚠️ 测试设计问题（{len(design_issues)} 个，非程序缺陷）",
                    "",
                    "> 以下失败用例经 AI 分析为测试期望值设置有误，程序行为实际符合预期。",
                    "",
                    "| 编号 | 描述 | 说明 |",
                    "|------|------|------|",
                ]
                for r in design_issues:
                    desc = r.task.description[:30] + "..." if len(r.task.description) > 30 else r.task.description
                    hint = r.analysis[:40] + "..." if r.analysis and len(r.analysis) > 40 else (r.analysis or "")
                    lines.append(f"| {r.task.task_id} | {desc} | {hint} |")
                lines += [""]

            lines += ["---", ""]

        # ── 五、测试环境 ───────────────────────────────────────────
        lines += [
            "## 五、测试环境",
            "",
            "| 项目 | 信息 |",
            "|------|------|",
            f"| 操作系统 | {platform.system()} {platform.version()[:40]} |",
            f"| Python 版本 | {platform.python_version()} |",
            f"| 测试时间 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} |",
            f"| 测试框架 | 软件测试智能体 v1.0 |",
            "",
            "---",
            "",
            "*本报告由软件测试智能体自动生成*",
            "",
        ]

        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print(f"[Reporter] Markdown: {path}")


def append_static_analysis(md_path: str, static_report):
    """追加静态代码分析章节"""
    if not static_report or not static_report.risk_points:
        return

    lines = [
        "",
        "## 六、静态代码分析",
        "",
        f"通过对 `{os.path.basename(static_report.source_file)}` 源码进行抽样分析，"
        f"共扫描 **{static_report.analyzed_snippets}** 个高风险代码片段"
        f"（源码总计 {static_report.total_lines:,} 行），"
        f"提炼出 **{len(static_report.risk_points)}** 个潜在风险点。",
        "",
        "| 编号 | 风险类型 | 严重程度 | 简述 |",
        "|------|----------|----------|------|",
    ]

    for rp in static_report.risk_points:
        icon = {"high": "🔴 高", "medium": "🟡 中", "low": "🟢 低"}.get(rp.severity, "⚪")
        desc = rp.description[:40] + "..." if len(rp.description) > 40 else rp.description
        lines.append(f"| {rp.risk_id} | {rp.risk_type} | {icon} | {desc} |")

    lines += [""]

    for rp in static_report.risk_points:
        icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(rp.severity, "⚪")
        lines += [
            f"### {icon} {rp.risk_id}：{rp.risk_type}",
            "",
            f"**风险描述**：{rp.description}",
            "",
            f"**代码位置**：`{rp.location}`",
            "",
            f"**触发条件**：{rp.trigger_condition}",
            "",
            f"**测试建议**：{rp.test_suggestion}",
            "",
            "---",
            "",
        ]

    with open(md_path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"[Reporter] 静态分析章节已追加")


def append_overall_summary(md_path: str, report: TestReport, static_report=None, confirmed_bugs: list = None):
    """调用 LLM 撰写整体总结并追加到报告末尾"""
    print("\n[Reporter] 正在生成整体测试总结...")
    try:
        from agents.report_writer_agent import ReportWriterAgent
        writer = ReportWriterAgent()
        summary = writer.write_overall_summary(report, static_report, confirmed_bugs=confirmed_bugs)

        lines = [
            "",
            "## 八、测试总结",
            "",
            summary,
            "",
            "---",
            "",
            "*本报告由软件测试智能体自动生成*",
        ]
        with open(md_path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print("[Reporter] 测试总结已生成")
    except Exception as e:
        print(f"[Reporter] 总结生成失败: {e}")


def append_refined_results(md_path: str, refined_report, refined_tasks,
                            round_num: int = 1, confirmed_bugs: list = None):
    """追加精化测试章节（支持多轮）"""
    confirmed_ids = {r.task.task_id for r in (confirmed_bugs or [])}
    section_num = 6 + round_num  # 第1轮→七，第2轮→八...
    section_title = f"## {'七八九十'[round_num-1] if round_num <= 4 else str(6+round_num)}、精化测试第 {round_num} 轮"

    lines = [
        "",
        section_title,
        "",
        f"**精化目的**：针对上一轮失败用例，缩小输入范围，"
        f"判断是程序缺陷还是测试设计问题。",
        "",
        "| 指标 | 数值 |",
        "|------|------|",
        f"| 精化任务数 | {refined_report.total} |",
        f"| 通过 | ✅ {refined_report.passed} |",
        f"| 失败 | ❌ {refined_report.failed} |",
        f"| 通过率 | {refined_report.pass_rate:.1f}% |",
        "",
    ]

    if confirmed_ids:
        bug_list = [r for r in refined_report.results if r.task.task_id in confirmed_ids]
        if bug_list:
            lines += [
                "**🐛 本轮确认的疑似程序缺陷：**",
                "",
            ]
            for r in bug_list:
                lines.append(f"- `{r.task.task_id}`：{r.task.description}")
            lines.append("")

    for r in refined_report.results:
        icon = "✅" if r.passed else ("🐛" if r.task.task_id in confirmed_ids else "❌")
        lines += [
            f"### {icon} {r.task.task_id}：{r.task.description}",
            "",
            f"- **判定**：{r.verdict}",
            f"- **期望**：{r.task.expected}",
        ]
        if r.cmd_results:
            last = r.cmd_results[-1]
            lines += ["", "```bash", f"$ {last.command}"]
            if last.stdout:
                lines.append(f"# 输出: {last.stdout[:300]}")
            if last.stderr:
                lines.append(f"# 错误: {last.stderr[:200]}")
            lines += [f"# 退出码: {last.returncode}", "```"]
        if r.analysis:
            lines += ["", f"> {r.analysis.strip()[:400]}"]
        lines += ["", "---", ""]

    with open(md_path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"[Reporter] 精化第{round_num}轮章节已追加")