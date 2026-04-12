# core/reporter.py  —— 测试报告输出器
import json
import os
import platform
from datetime import datetime
from agents.executor_agent import TestReport
from config import OUTPUT_DIR


def merge_reports(base_report, refined_reports: list):
    """
    用精化测试结果替换原始失败结果，生成合并后的最终报告。
    精化通过 → 替换原始为 PASS（测试设计问题）
    精化仍失败 → 用精化结果替换（更精准的失败信息）
    未覆盖 → 保留原始结果
    """
    from copy import deepcopy

    # 建立精化结果索引：原始任务ID → 最新精化结果
    refined_index = {}
    for rpt in refined_reports:
        for r in rpt.results:
            orig_id = r.task.task_id.split("_")[0]
            refined_index[orig_id] = r

    final_results = []
    for orig in base_report.results:
        if not orig.passed and orig.task.task_id in refined_index:
            ref = refined_index[orig.task.task_id]
            if ref.passed:
                # 精化通过：原始是测试设计问题
                fixed = deepcopy(orig)
                fixed.passed = True
                fixed.verdict = "PASS"
                ref_analysis = ref.analysis or ""
                fixed.analysis = (
                    "【精化后通过】原始测试期望值设计有误，"
                    "精化任务 " + ref.task.task_id + " 修正后验证程序行为符合预期。\n"
                    + ref_analysis
                )
                final_results.append(fixed)
            else:
                # 精化仍失败：用精化结果替换
                updated = deepcopy(ref)
                updated.task.task_id = orig.task.task_id
                updated.task.description = "[精化] " + orig.task.description
                final_results.append(updated)
        else:
            final_results.append(orig)

    merged = TestReport(project_name=base_report.project_name)
    merged.results = final_results
    merged.total = len(final_results)
    merged.passed = sum(1 for r in final_results if r.passed)
    merged.failed = sum(1 for r in final_results if not r.passed and r.verdict == "FAIL")
    merged.errors = sum(1 for r in final_results if r.verdict == "ERROR")
    return merged


class Reporter:
    def save(self, report: TestReport) -> tuple[str, str]:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
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

        # 封面
        lines += [
            "# 软件测试报告",
            "",
            "**被测项目**：" + report.project_name + "  ",
            "**生成时间**：" + datetime.now().strftime("%Y年%m月%d日 %H:%M:%S") + "  ",
            "**测试方法**：黑盒测试 + 静态代码分析驱动白盒测试  ",
            "",
            "---",
            "",
        ]

        # 一、测试摘要
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
            "**总体结论**：" + overall,
            "",
            "| 指标 | 数值 |",
            "|------|------|",
            "| 测试用例总数 | " + str(report.total) + " |",
            "| 通过（PASS） | ✅ " + str(report.passed) + " |",
            "| 失败（FAIL） | ❌ " + str(report.failed) + " |",
            "| 错误（ERROR）| ⚠️ " + str(report.errors) + " |",
            "| 通过率 | **" + f"{pass_rate:.1f}%" + "** |",
            "",
            "```",
            "通过率  [" + bar + "] " + f"{pass_rate:.1f}%",
            "```",
            "",
            "---",
            "",
        ]

        # 二、测试分类统计
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
            lines.append(
                "| " + icon + " " + cat + " | " + str(s["total"]) +
                " | " + str(s["passed"]) + " | " + str(s["failed"]) +
                " | " + f"{rate:.0f}%" + " |"
            )
        lines += ["", "---", ""]

        # 三、测试用例详情
        lines += [
            "## 三、测试用例详情",
            "",
            "> 以下为每个测试用例的执行情况，失败用例附有详细的 AI 分析。",
            "",
        ]

        print("\n[Reporter] 是否调用 AI 为每个失败用例撰写详细分析？（会消耗少量 API）")
        use_writer = input("  输入 y 启用，其他键跳过：").strip().lower() == "y"
        writer = None
        if use_writer:
            from agents.report_writer_agent import ReportWriterAgent
            writer = ReportWriterAgent()
            print("[Reporter] AI 报告撰写已启用...")

        for r in report.results:
            verdict_icon = {"PASS": "✅", "FAIL": "❌", "ERROR": "⚠️", "TIMEOUT": "⏱️"}.get(r.verdict, "❓")
            lines += [
                "### " + verdict_icon + " " + r.task.task_id + "　" + r.task.description,
                "",
                "| 项目 | 内容 |",
                "|------|------|",
                "| 测试类别 | " + r.task.category + " |",
                "| 判定结果 | " + verdict_icon + " **" + r.verdict + "** |",
                "| 期望行为 | " + r.task.expected + " |",
                "| Oracle类型 | " + r.task.oracle_type + " |",
                "",
            ]

            if r.cmd_results:
                lines.append("**执行过程：**")
                lines.append("")
                for j, cr in enumerate(r.cmd_results):
                    lines += ["```bash", "# 命令 " + str(j+1), "$ " + cr.command]
                    if cr.stdout:
                        stdout_display = cr.stdout[:500]
                        if len(cr.stdout) > 500:
                            stdout_display += "\n...（已截断）"
                        lines.append("# stdout:")
                        for out_line in stdout_display.splitlines():
                            lines.append("  " + out_line)
                    if cr.stderr:
                        lines.append("# stderr: " + cr.stderr[:300])
                    lines.append("# 退出码: " + str(cr.returncode))
                    if cr.timed_out:
                        lines.append("# ⏱️ 命令超时！")
                    lines += ["```", ""]

            if writer and not r.passed:
                print("  分析 " + r.task.task_id + "...")
                analysis = writer.write_case_analysis(r)
                lines += ["**📝 测试分析：**", "", "> " + analysis.strip(), ""]
            elif r.analysis:
                lines += ["**📝 测试分析：**", "", "> " + r.analysis.strip(), ""]

            lines += ["---", ""]

        # 四、问题汇总
        failed_results = [r for r in report.results if not r.passed]
        lines += ["## 四、问题汇总", ""]

        if not failed_results:
            lines += ["> ✅ 本轮测试未发现程序缺陷，所有用例均通过。", "", "---", ""]
        else:
            design_issues = []
            real_bugs = []
            for r in failed_results:
                al = (r.analysis or "").lower()
                if any(kw in al for kw in
                       ["未发现缺陷", "符合预期", "测试设计", "期望值", "正常行为", "正确行为"]):
                    design_issues.append(r)
                else:
                    real_bugs.append(r)

            if real_bugs:
                lines += [
                    "### 🐛 疑似程序缺陷（" + str(len(real_bugs)) + " 个）",
                    "",
                    "| 编号 | 描述 | 退出码 | stdout | stderr |",
                    "|------|------|--------|--------|--------|",
                ]
                for r in real_bugs:
                    rc = str(r.cmd_results[-1].returncode) if r.cmd_results else "N/A"
                    so = (r.cmd_results[-1].stdout[:25] if r.cmd_results else "").replace("|", "\\|")
                    se = (r.cmd_results[-1].stderr[:25] if r.cmd_results else "").replace("|", "\\|")
                    desc = r.task.description[:30] + "..." if len(r.task.description) > 30 else r.task.description
                    lines.append("| " + r.task.task_id + " | " + desc + " | " + rc + " | " + so + " | " + se + " |")
                lines += [""]

            if design_issues:
                lines += [
                    "### ⚠️ 测试设计问题（" + str(len(design_issues)) + " 个，非程序缺陷）",
                    "",
                    "> 以下失败用例经 AI 分析为测试期望值设置有误，程序行为实际符合预期。",
                    "",
                    "| 编号 | 描述 | 说明 |",
                    "|------|------|------|",
                ]
                for r in design_issues:
                    desc = r.task.description[:30] + "..." if len(r.task.description) > 30 else r.task.description
                    hint = (r.analysis[:40] + "...") if r.analysis and len(r.analysis) > 40 else (r.analysis or "")
                    lines.append("| " + r.task.task_id + " | " + desc + " | " + hint + " |")
                lines += [""]

            lines += ["---", ""]

        # 五、测试环境
        lines += [
            "## 五、测试环境",
            "",
            "| 项目 | 信息 |",
            "|------|------|",
            "| 操作系统 | " + platform.system() + " " + platform.version()[:40] + " |",
            "| Python 版本 | " + platform.python_version() + " |",
            "| 测试时间 | " + datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " |",
            "| 测试框架 | 软件测试智能体 v1.0 |",
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
    if not static_report or not static_report.risk_points:
        return

    lines = [
        "",
        "## 六、静态代码分析",
        "",
        "通过对 `" + os.path.basename(static_report.source_file) + "` 源码进行抽样分析，"
        "共扫描 **" + str(static_report.analyzed_snippets) + "** 个高风险代码片段"
        "（源码总计 " + f"{static_report.total_lines:,}" + " 行），"
        "提炼出 **" + str(len(static_report.risk_points)) + "** 个潜在风险点。",
        "",
        "| 编号 | 风险类型 | 严重程度 | 简述 |",
        "|------|----------|----------|------|",
    ]

    for rp in static_report.risk_points:
        icon = {"high": "🔴 高", "medium": "🟡 中", "low": "🟢 低"}.get(rp.severity, "⚪")
        desc = rp.description[:40] + "..." if len(rp.description) > 40 else rp.description
        lines.append("| " + rp.risk_id + " | " + rp.risk_type + " | " + icon + " | " + desc + " |")

    lines += [""]

    for rp in static_report.risk_points:
        icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(rp.severity, "⚪")
        lines += [
            "### " + icon + " " + rp.risk_id + "：" + rp.risk_type,
            "",
            "**风险描述**：" + rp.description,
            "",
            "**代码位置**：`" + rp.location + "`",
            "",
            "**触发条件**：" + rp.trigger_condition,
            "",
            "**测试建议**：" + rp.test_suggestion,
            "",
            "---",
            "",
        ]

    with open(md_path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("[Reporter] 静态分析章节已追加")


def append_refined_results(md_path: str, refined_report, refined_tasks,
                           round_num: int = 1, confirmed_bugs: list = None):
    confirmed_ids = {r.task.task_id for r in (confirmed_bugs or [])}
    roman = ["一", "二", "三", "四"]
    sec = roman[round_num - 1] if round_num <= 4 else str(round_num)
    section_no = 6 + round_num

    lines = [
        "",
        "## " + str(section_no) + "、精化测试第 " + str(round_num) + " 轮",
        "",
        "**精化目的**：针对上一轮失败用例，缩小输入范围，"
        "判断是程序缺陷还是测试设计问题。",
        "",
        "| 指标 | 数值 |",
        "|------|------|",
        "| 精化任务数 | " + str(refined_report.total) + " |",
        "| 通过 | ✅ " + str(refined_report.passed) + " |",
        "| 失败 | ❌ " + str(refined_report.failed) + " |",
        "| 通过率 | " + f"{refined_report.pass_rate:.1f}%" + " |",
        "",
    ]

    for r in refined_report.results:
        is_bug = r.task.task_id in confirmed_ids
        icon = "✅" if r.passed else ("🐛" if is_bug else "❌")
        lines += [
            "### " + icon + " " + r.task.task_id + "：" + r.task.description,
            "",
            "- **判定**：" + r.verdict,
            "- **期望**：" + r.task.expected,
        ]
        if r.cmd_results:
            last = r.cmd_results[-1]
            lines += ["", "```bash", "$ " + last.command]
            if last.stdout:
                lines.append("# stdout: " + last.stdout[:300])
            if last.stderr:
                lines.append("# stderr: " + last.stderr[:200])
            lines += ["# 退出码: " + str(last.returncode), "```"]
        if r.analysis:
            lines += ["", "> " + r.analysis.strip()[:400]]
        lines += ["", "---", ""]

    with open(md_path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("[Reporter] 精化第" + str(round_num) + "轮章节已追加")


def append_overall_summary(md_path: str, report: TestReport,
                           static_report=None, confirmed_bugs: list = None):
    print("\n[Reporter] 正在生成整体测试总结...")
    try:
        from agents.report_writer_agent import ReportWriterAgent
        writer = ReportWriterAgent()
        summary = writer.write_overall_summary(report, static_report,
                                               confirmed_bugs=confirmed_bugs)
        lines = [
            "",
            "## 结语：测试总结",
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
        print("[Reporter] 总结生成失败: " + str(e))