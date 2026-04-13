#!/usr/bin/env python3
# main.py  —— 软件测试智能体入口
"""
完整流程：
  人工填写框架
    → EnvAgent（探测/安装工具，修正路径）
    → StaticAnalysisAgent（分析源码风险点）
    → PlannerAgent（框架 + 静态分析 → 测试任务）
    → ExecutorAgent 第一轮（执行所有任务）
    → RefinementAgent（针对失败任务生成精化变体）
    → ExecutorAgent 第二轮（执行精化任务）
    → 合并报告输出
"""

import os
import sys

# ──────────────────────────────────────────────────────────────────────
#  ★ 修改这里：填入你的被测项目信息
# ──────────────────────────────────────────────────────────────────────
TEST_FRAMEWORK = {
    "project_name": "SQLite3 数据库引擎",
    "language": "C (预编译二进制)",
    "source_files": [],
    "binary": "sqlite3",
    "compile_cmd": "",
    "description": (
        "SQLite3 是世界上使用最广泛的嵌入式关系型数据库引擎，由 C 语言编写。"
        "通过命令行工具 sqlite3 可以执行 SQL 语句、管理数据库文件。"
        "调用方式：sqlite3 [数据库文件] \"SQL语句\"  "
        "或  echo SQL语句 | sqlite3 :memory: （使用内存数据库）。"
    ),
    "test_goals": [
        "测试超长字符串插入（VARCHAR 超过正常长度）",
        "测试 NULL 值在各种运算和函数中的行为",
        "测试整数边界值：INT 最大值、最小值、溢出",
        "测试浮点数特殊值：极大数、极小数",
        "测试 SQL 注入风险输入（特殊字符、引号嵌套）",
        "测试空数据库操作（查询不存在的表）",
        "测试嵌套 SQL 子查询的正确性",
        "测试 UNIQUE 约束、NOT NULL 约束违反时的行为",
        "测试事务：BEGIN/COMMIT/ROLLBACK 的正确性",
        "测试除零：SELECT 1/0",
        "测试批量插入 1000 条记录的性能和正确性",
        "测试大字段：插入超长 TEXT 数据",
        "测试复杂 JOIN：多表关联查询的正确性",
        "测试聚合函数（COUNT/SUM/AVG/MAX/MIN）的正确性",
    ],
    "extra_notes": (
        "sqlite3 是命令行工具，每次调用执行 SQL 后退出。"
        "使用 :memory: 作为数据库名避免产生文件。"
        "Windows 下用 echo SQL | sqlite3.exe :memory: 格式传入 SQL。"
    ),
}
# ──────────────────────────────────────────────────────────────────────


def main():
    from agents.env_agent import EnvAgent
    from agents.static_analysis_agent import StaticAnalysisAgent
    from agents.planner_agent import PlannerAgent
    from agents.executor_agent import ExecutorAgent
    from agents.refinement_agent import RefinementAgent
    from core.terminal import TerminalExecutor
    from core.reporter import Reporter

    project_root = os.path.dirname(os.path.abspath(__file__))

    print("=" * 60)
    print("  软件测试智能体 —— Software Testing Agent")
    print("=" * 60)
    print(f"  项目：{TEST_FRAMEWORK['project_name']}")
    print(f"  根目录：{project_root}")
    print("=" * 60)

    # ── Step 1：EnvAgent ─────────────────────────────────────────
    env_agent = EnvAgent(workdir=project_root)
    framework = env_agent.detect_and_fix(TEST_FRAMEWORK)

    # ── Step 2：编译（sqlite3 跳过）─────────────────────────────
    compile_cmd = framework.get("compile_cmd", "")
    if compile_cmd:
        print(f"\n[Main] 编译被测程序...")
        t = TerminalExecutor(workdir=project_root)
        res = t.run(compile_cmd)
        if not res.success:
            print(f"[Main] ⚠️  编译失败: {res.stderr}")
        else:
            print("[Main] ✅ 编译成功。")

    # ── Step 3：StaticAnalysisAgent ──────────────────────────────
    static_agent = StaticAnalysisAgent(workdir=project_root)
    static_report = static_agent.analyze(framework)

    # ── Step 4：PlannerAgent ─────────────────────────────────────
    planner = PlannerAgent()
    tasks = planner.plan(framework, static_report=static_report)

    print(f"\n[Main] 测试任务预览（共 {len(tasks)} 个）：")
    for task in tasks:
        tag = " ★" if "静态" in task.category else ""
        print(f"  {task.task_id} [{task.category}]{tag} {task.description}")
    print("（★ 表示由静态分析驱动）")
    input("\n按 Enter 开始第一轮测试...")

    # ── Step 5：第一轮执行 ───────────────────────────────────────
    executor = ExecutorAgent(workdir=project_root)
    report = executor.run_all(
        tasks,
        project_name=framework["project_name"],
        framework=framework,
    )

    print(report.summary())
    print(f"\n第一轮完成：{report.passed} 通过 / {report.failed} 失败 / {report.errors} 错误")

    # ── Step 6：多轮精化迭代 ────────────────────────────────────
    # 精化目的：
    #   - 确认失败是"程序bug"还是"测试设计问题"
    #   - 如果是程序bug，找到最小复现用例
    #   - 如果还有失败，继续迭代（但不重复已测场景）
    MAX_REFINE_ROUNDS = 3   # 最多迭代几轮
    all_refined_reports = []
    all_refined_tasks = []
    confirmed_bugs = []     # 跨轮次确认的真实 bug

    current_report = report  # 每轮基于上一轮的失败继续精化
    refiner = RefinementAgent()
    # 记录所有已测过的命令，避免重复
    tested_commands = set(
        cr.command
        for r in report.results
        for cr in r.cmd_results
    )

    for round_num in range(1, MAX_REFINE_ROUNDS + 1):
        still_failed = [
            r for r in current_report.results
            if r.verdict in ("FAIL", "ERROR")
        ]
        if not still_failed:
            print(f"\n[Main] 第{round_num}轮精化前无失败用例，停止迭代。")
            break

        print(f"\n{'='*50}")
        print(f"  精化迭代第 {round_num} 轮（还有 {len(still_failed)} 个失败）")
        print(f"{'='*50}")

        # 生成精化任务，去掉已测过的命令
        round_tasks = refiner.refine(current_report, framework)
        # 过滤重复命令
        new_tasks = []
        for t in round_tasks:
            filtered_cmds = [c for c in t.commands if c not in tested_commands]
            if filtered_cmds:
                t.commands = filtered_cmds
                new_tasks.append(t)
            else:
                print(f"  [Main] 跳过重复任务: {t.task_id}")

        if not new_tasks:
            print(f"[Main] 第{round_num}轮没有新的精化任务，停止迭代。")
            break

        print(f"\n[Main] 第{round_num}轮精化任务（{len(new_tasks)} 个）：")
        for task in new_tasks:
            print(f"  {task.task_id} {task.description[:60]}")
        input(f"\n按 Enter 开始第{round_num}轮精化测试...")

        round_report = executor.run_all(
            new_tasks,
            project_name=f"{framework['project_name']}（精化第{round_num}轮）",
            framework=framework,
        )
        print(round_report.summary())

        # 记录本轮命令，防止下轮重复
        for r in round_report.results:
            for cr in r.cmd_results:
                tested_commands.add(cr.command)

        all_refined_reports.append(round_report)
        all_refined_tasks.extend(new_tasks)

        # 判断哪些是真实 bug（精化后仍然失败，且分析不含"符合预期"）
        for r in round_report.results:
            if not r.passed:
                analysis_lower = (r.analysis or "").lower()
                is_design_issue = any(kw in analysis_lower for kw in
                    ["未发现缺陷", "符合预期", "测试设计", "期望值", "正常行为", "正确行为"])
                if not is_design_issue:
                    confirmed_bugs.append(r)
                    print(f"  🐛 确认 bug：{r.task.task_id} - {r.task.description[:50]}")

        current_report = round_report  # 下一轮基于本轮失败继续

    if confirmed_bugs:
        print(f"\n[Main] 🐛 多轮精化后确认 {len(confirmed_bugs)} 个疑似程序缺陷：")
        for r in confirmed_bugs:
            print(f"  - {r.task.task_id}: {r.task.description[:60]}")
    else:
        print("\n[Main] ✅ 多轮精化后未确认程序缺陷（失败均为测试设计问题）。")

    # ── Step 7：合并报告 + 输出 ──────────────────────────────────
    from core.reporter import merge_reports, append_static_analysis
    from core.reporter import append_refined_results, append_overall_summary

    # 用精化结果替换原始失败结果，生成最终合并报告
    if all_refined_reports:
        print("\n[Main] 合并精化结果到最终报告...")
        final_report = merge_reports(report, all_refined_reports)
        print(f"[Main] 合并后：{final_report.passed} 通过 / "
              f"{final_report.failed} 失败 / {final_report.errors} 错误")
    else:
        final_report = report

    reporter = Reporter()
    json_path, md_path = reporter.save(final_report)
    append_static_analysis(md_path, static_report)

    # 精化过程作为附录追加
    for i, rpt in enumerate(all_refined_reports):
        append_refined_results(md_path, rpt, all_refined_tasks,
                               round_num=i+1, confirmed_bugs=confirmed_bugs)

    append_overall_summary(md_path, final_report, static_report,
                           confirmed_bugs=confirmed_bugs)

    print(f"\n[Main] 报告已生成：")
    print(f"  JSON     : {json_path}")
    print(f"  Markdown : {md_path}")
    print("\n[Main] 测试完成。")




if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    main()