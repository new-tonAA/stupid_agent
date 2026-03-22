#!/usr/bin/env python3
# main.py  —— 软件测试智能体入口
"""
完整流程：
  人工填写框架
    → EnvAgent（探测/安装工具，修正路径）
    → StaticAnalysisAgent（下载源码，分析风险点）
    → PlannerAgent（框架 + 静态分析结果 → 测试任务）
    → ExecutorAgent（终端执行）
    → 报告输出
"""

import os
import sys

# ──────────────────────────────────────────────────────────────────────
#  ★ 修改这里：填入你的被测项目信息
# ──────────────────────────────────────────────────────────────────────
TEST_FRAMEWORK = {
    "project_name": "SQLite3 数据库引擎",

    "language": "C (预编译二进制)",

    "source_files": [],   # EnvAgent 会自动下载源码

    # EnvAgent 会自动找到或下载 sqlite3.exe
    "binary": "sqlite3",

    # 不需要编译
    "compile_cmd": "",

    "description": (
        "SQLite3 是世界上使用最广泛的嵌入式关系型数据库引擎，由 C 语言编写。"
        "通过命令行工具 sqlite3 可以执行 SQL 语句、管理数据库文件。"
        "调用方式：sqlite3 [数据库文件] \"SQL语句\"  "
        "或  echo SQL语句 | sqlite3 :memory: （使用内存数据库）。"
    ),

    "test_goals": [
        # SQL 边界值与异常输入
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
        # 性能与大数据
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
    from core.terminal import TerminalExecutor
    from core.reporter import Reporter

    project_root = os.path.dirname(os.path.abspath(__file__))

    print("=" * 60)
    print("  软件测试智能体 —— Software Testing Agent")
    print("=" * 60)
    print(f"  项目：{TEST_FRAMEWORK['project_name']}")
    print(f"  根目录：{project_root}")
    print("=" * 60)

    # ── Step 1：EnvAgent 探测环境 ────────────────────────────────
    env_agent = EnvAgent(workdir=project_root)
    framework = env_agent.detect_and_fix(TEST_FRAMEWORK)

    # ── Step 2：编译（sqlite3 无需编译，自动跳过）──────────────
    compile_cmd = framework.get("compile_cmd", "")
    if compile_cmd:
        print(f"\n[Main] 编译被测程序...")
        t = TerminalExecutor(workdir=project_root)
        res = t.run(compile_cmd)
        if not res.success:
            print(f"[Main] ⚠️  编译失败！stderr: {res.stderr}")
        else:
            print("[Main] ✅ 编译成功。")

    # ── Step 3：StaticAnalysisAgent 分析源码 ────────────────────
    static_agent = StaticAnalysisAgent(workdir=project_root)
    static_report = static_agent.analyze(framework)

    # ── Step 4：PlannerAgent 细化测试任务 ───────────────────────
    planner = PlannerAgent()
    tasks = planner.plan(framework, static_report=static_report)

    print(f"\n[Main] 测试任务预览：")
    for task in tasks:
        tag = " ★" if "静态" in task.category else ""
        print(f"  {task.task_id} [{task.category}]{tag} {task.description}")

    print("\n（★ 表示由静态分析驱动的测试任务）")
    input("\n按 Enter 开始执行测试...")

    # ── Step 5：ExecutorAgent 执行测试 ──────────────────────────
    executor = ExecutorAgent(workdir=project_root)
    report = executor.run_all(tasks, project_name=framework["project_name"], framework=framework)

    # ── Step 6：输出报告 ─────────────────────────────────────────
    print(report.summary())

    reporter = Reporter()
    json_path, md_path = reporter.save(report)

    # 同时把静态分析结果写入报告
    _append_static_to_report(md_path, static_report)

    print(f"\n[Main] 报告已生成：")
    print(f"  JSON     : {json_path}")
    print(f"  Markdown : {md_path}")
    print("\n[Main] 测试完成。")


def _append_static_to_report(md_path: str, static_report):
    """把静态分析结果追加到 Markdown 报告末尾"""
    if not static_report or not static_report.risk_points:
        return
    with open(md_path, "a", encoding="utf-8") as f:
        f.write("\n## 静态代码分析结果\n\n")
        f.write(f"- 分析源文件：`{static_report.source_file}`\n")
        f.write(f"- 源码总行数：{static_report.total_lines}\n")
        f.write(f"- 分析代码片段数：{static_report.analyzed_snippets}\n")
        f.write(f"- 发现风险点：{len(static_report.risk_points)}\n\n")
        for rp in static_report.risk_points:
            icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(rp.severity, "⚪")
            f.write(f"### {icon} {rp.risk_id} — {rp.risk_type}\n\n")
            f.write(f"- **位置**：{rp.location}\n")
            f.write(f"- **描述**：{rp.description}\n")
            f.write(f"- **触发条件**：{rp.trigger_condition}\n")
            f.write(f"- **测试建议**：{rp.test_suggestion}\n")
            f.write(f"- **严重程度**：{rp.severity}\n\n")
            f.write("---\n\n")


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    main()