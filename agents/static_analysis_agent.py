# agents/static_analysis_agent.py  —— 静态分析智能体
"""
职责：
  1. 下载或读取被测程序源码
  2. 提取关键函数/模块（避免一次性喂太多 token）
  3. 调用 LLM 分析源码中的潜在风险点
  4. 输出结构化的「风险报告」，供 PlannerAgent 生成针对性测试用例

对于 sqlite3 这类超大源码（23万行），采用分块抽样策略：
  - 自动识别高风险函数名（除法、内存分配、字符串处理等）
  - 只截取这些函数附近的代码片段送给 LLM
  - 避免超出 token 限制
"""

import os
import re
import urllib.request
from dataclasses import dataclass, field
from core.llm_client import LLMClient


# sqlite3 amalgamation 源码下载地址
SQLITE3_SRC_URL = "https://www.sqlite.org/2024/sqlite-amalgamation-3460100.zip"
SQLITE3_SRC_DIR = "sqlite3_src"
SQLITE3_SRC_FILE = "sqlite3.c"

# 触发分析的高风险关键词（函数名/模式）
HIGH_RISK_PATTERNS = [
    r"\/",           # 除法操作
    r"malloc\s*\(",  # 内存分配
    r"realloc\s*\(", # 内存重分配
    r"free\s*\(",    # 内存释放
    r"strlen\s*\(",  # 字符串长度
    r"strcpy\s*\(",  # 字符串复制（经典溢出风险）
    r"sprintf\s*\(", # 格式化字符串
    r"atoi\s*\(",    # 字符串转整数
    r"overflow",     # 溢出相关注释
    r"SQLITE_MAX",   # 最大值限制
    r"assert\s*\(",  # 断言（说明开发者认为这里可能出错）
]

STATIC_ANALYSIS_SYSTEM = """
你是一名资深 C 语言安全分析专家，擅长发现代码中的潜在缺陷和边界问题。

用户会给你一段 C 源码片段（来自 sqlite3.c），请你：
1. 识别代码中的潜在风险点，包括但不限于：
   - 整数溢出/下溢
   - 除零风险
   - 空指针解引用
   - 缓冲区溢出
   - 内存泄漏
   - 未处理的错误返回值
   - 边界条件处理不当
2. 对每个风险点，描述触发条件和可能的行为
3. 给出针对性的测试建议（具体的输入值或场景）

返回 JSON 数组，每个元素是一个风险点：
[
  {
    "risk_id": "R01",
    "location": "函数名或代码位置描述",
    "risk_type": "风险类型（如：整数溢出、除零、空指针等）",
    "description": "风险描述",
    "trigger_condition": "触发这个风险需要什么输入或条件",
    "test_suggestion": "建议的测试用例描述（具体 SQL 语句或操作）",
    "severity": "high / medium / low"
  }
]

如果代码片段中没有明显风险，返回空数组 []。
"""

SUMMARY_SYSTEM = """
你是一名软件测试专家。用户会给你多个代码片段的静态分析结果（风险点列表），
请综合所有结果，提炼出最值得测试的 5~8 个核心风险点，去掉重复的，按严重程度排序。

返回 JSON 数组，格式与输入相同：
[
  {
    "risk_id": "R01",
    "location": "...",
    "risk_type": "...",
    "description": "...",
    "trigger_condition": "...",
    "test_suggestion": "具体的 SQL 语句或测试场景",
    "severity": "high / medium / low"
  }
]
"""


@dataclass
class RiskPoint:
    risk_id: str
    location: str
    risk_type: str
    description: str
    trigger_condition: str
    test_suggestion: str
    severity: str = "medium"

    def to_dict(self) -> dict:
        return self.__dict__

    def to_prompt_text(self) -> str:
        return (
            f"[{self.risk_id}] {self.risk_type} @ {self.location}\n"
            f"  描述: {self.description}\n"
            f"  触发条件: {self.trigger_condition}\n"
            f"  测试建议: {self.test_suggestion}\n"
            f"  严重程度: {self.severity}"
        )


@dataclass
class StaticAnalysisReport:
    source_file: str
    total_lines: int
    analyzed_snippets: int
    risk_points: list[RiskPoint] = field(default_factory=list)

    def summary_for_planner(self) -> str:
        """生成给 PlannerAgent 看的摘要文本"""
        if not self.risk_points:
            return "静态分析未发现明显风险点。"
        lines = [
            f"静态分析发现 {len(self.risk_points)} 个潜在风险点"
            f"（分析了 {self.analyzed_snippets} 个代码片段，共 {self.total_lines} 行源码）：",
            ""
        ]
        for rp in self.risk_points:
            lines.append(rp.to_prompt_text())
            lines.append("")
        return "\n".join(lines)


class StaticAnalysisAgent:
    """
    Agent 0.5：静态分析智能体。
    在 PlannerAgent 之前运行，分析源码风险，增强测试用例针对性。
    """

    def __init__(self, workdir: str = "."):
        self._llm = LLMClient()
        self._workdir = workdir

    def analyze(self, framework: dict) -> StaticAnalysisReport:
        """
        分析被测项目源码，返回静态分析报告。
        framework 中需要有 source_files 或可下载的源码。
        """
        project_name = framework.get("project_name", "")
        print(f"\n[StaticAnalysisAgent] 开始静态分析: {project_name}")

        # 获取源码文件路径
        src_path = self._get_source_file(framework)

        if not src_path:
            print("[StaticAnalysisAgent] ⚠️  无法获取源码，跳过静态分析。")
            return StaticAnalysisReport(
                source_file="",
                total_lines=0,
                analyzed_snippets=0,
                risk_points=[],
            )

        print(f"[StaticAnalysisAgent] 源码文件: {src_path}")

        # 读取源码
        with open(src_path, "r", encoding="utf-8", errors="ignore") as f:
            source_lines = f.readlines()

        total_lines = len(source_lines)
        print(f"[StaticAnalysisAgent] 源码共 {total_lines} 行")

        # 提取高风险代码片段
        snippets = self._extract_risky_snippets(source_lines)
        print(f"[StaticAnalysisAgent] 提取到 {len(snippets)} 个高风险代码片段，开始逐一分析...")

        # 逐片段分析
        all_risks = []
        for i, (label, snippet) in enumerate(snippets):
            print(f"  [分析 {i+1}/{len(snippets)}] {label}")
            risks = self._analyze_snippet(label, snippet)
            all_risks.extend(risks)

        print(f"[StaticAnalysisAgent] 原始风险点共 {len(all_risks)} 个，正在综合提炼...")

        # 综合提炼，去重排序
        final_risks = self._summarize_risks(all_risks)
        print(f"[StaticAnalysisAgent] ✅ 最终提炼出 {len(final_risks)} 个核心风险点")

        for rp in final_risks:
            icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(rp.severity, "⚪")
            print(f"  {icon} [{rp.risk_id}] {rp.risk_type} - {rp.description[:60]}")

        return StaticAnalysisReport(
            source_file=src_path,
            total_lines=total_lines,
            analyzed_snippets=len(snippets),
            risk_points=final_risks,
        )

    # ──────────────────────────────────────────────────────────
    # 源码获取
    # ──────────────────────────────────────────────────────────

    def _get_source_file(self, framework: dict) -> str:
        """获取源码文件路径，如果没有则尝试下载"""
        # 1. 用 framework 里指定的源文件
        source_files = framework.get("source_files", [])
        for sf in source_files:
            full_path = os.path.join(self._workdir, sf)
            if os.path.isfile(full_path):
                return full_path

        # 2. sqlite3 特殊处理：从项目目录找或下载
        project_name = framework.get("project_name", "").lower()
        if "sqlite" in project_name:
            return self._get_sqlite3_source()

        return ""

    def _get_sqlite3_source(self) -> str:
        """获取 sqlite3.c 源码文件"""
        # 先找本地
        candidates = [
            os.path.join(self._workdir, SQLITE3_SRC_FILE),
            os.path.join(self._workdir, SQLITE3_SRC_DIR, SQLITE3_SRC_FILE),
            os.path.join(self._workdir, SQLITE3_SRC_DIR, "sqlite-amalgamation-3460100", SQLITE3_SRC_FILE),
        ]
        for p in candidates:
            if os.path.isfile(p):
                print(f"[StaticAnalysisAgent] 找到本地源码: {p}")
                return p

        # 下载
        print(f"[StaticAnalysisAgent] 本地无源码，尝试从 sqlite.org 下载...")
        confirm = input("[StaticAnalysisAgent] 是否下载 sqlite3.c 源码用于静态分析？(y/n): ").strip().lower()
        if confirm != "y":
            print("[StaticAnalysisAgent] 用户跳过，静态分析将不使用源码。")
            return ""

        import zipfile
        import urllib.request

        zip_path = os.path.join(self._workdir, "sqlite3_src.zip")
        extract_dir = os.path.join(self._workdir, SQLITE3_SRC_DIR)

        try:
            print(f"[StaticAnalysisAgent] 下载中: {SQLITE3_SRC_URL}")
            urllib.request.urlretrieve(SQLITE3_SRC_URL, zip_path)

            os.makedirs(extract_dir, exist_ok=True)
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(extract_dir)
            os.remove(zip_path)

            # 找 sqlite3.c
            for root, dirs, files in os.walk(extract_dir):
                if SQLITE3_SRC_FILE in files:
                    found = os.path.join(root, SQLITE3_SRC_FILE)
                    print(f"[StaticAnalysisAgent] ✅ 源码下载完成: {found}")
                    return found

        except Exception as e:
            print(f"[StaticAnalysisAgent] 下载失败: {e}")

        return ""

    # ──────────────────────────────────────────────────────────
    # 代码片段提取
    # ──────────────────────────────────────────────────────────

    def _extract_risky_snippets(
        self, lines: list[str], max_snippets: int = 6, context: int = 40
    ) -> list[tuple[str, str]]:
        """
        在源码中找高风险行，提取其上下文片段。
        返回 [(标签, 代码文本), ...]
        max_snippets：最多分析几个片段（控制 API 费用）
        context：每个片段上下各取多少行
        """
        hit_lines = []  # [(行号, 匹配模式)]

        for i, line in enumerate(lines):
            for pattern in HIGH_RISK_PATTERNS:
                if re.search(pattern, line, re.IGNORECASE):
                    hit_lines.append((i, pattern))
                    break

        if not hit_lines:
            return []

        # 去重聚合：相邻行合并成一个片段，均匀采样
        merged = self._merge_nearby_hits(hit_lines, gap=context)

        # 均匀采样 max_snippets 个
        step = max(1, len(merged) // max_snippets)
        sampled = merged[::step][:max_snippets]

        snippets = []
        for center_line, pattern in sampled:
            start = max(0, center_line - context)
            end = min(len(lines), center_line + context)
            snippet_text = "".join(lines[start:end])
            label = f"行 {start+1}~{end}（触发模式: {pattern}）"
            snippets.append((label, snippet_text))

        return snippets

    def _merge_nearby_hits(
        self, hits: list[tuple[int, str]], gap: int
    ) -> list[tuple[int, str]]:
        """把距离小于 gap 的命中行合并，取中点"""
        if not hits:
            return []
        merged = [hits[0]]
        for line_no, pattern in hits[1:]:
            if line_no - merged[-1][0] < gap:
                continue  # 太近，跳过
            merged.append((line_no, pattern))
        return merged

    # ──────────────────────────────────────────────────────────
    # LLM 分析
    # ──────────────────────────────────────────────────────────

    def _analyze_snippet(self, label: str, snippet: str) -> list[RiskPoint]:
        """让 LLM 分析一个代码片段，返回风险点列表"""
        user_msg = f"代码位置：{label}\n\n```c\n{snippet[:3000]}\n```"
        try:
            raw = self._llm.chat_json(STATIC_ANALYSIS_SYSTEM, user_msg)
            if not isinstance(raw, list):
                return []
            risks = []
            for i, item in enumerate(raw):
                risks.append(RiskPoint(
                    risk_id=item.get("risk_id", f"R{i:02d}"),
                    location=item.get("location", label),
                    risk_type=item.get("risk_type", "未知"),
                    description=item.get("description", ""),
                    trigger_condition=item.get("trigger_condition", ""),
                    test_suggestion=item.get("test_suggestion", ""),
                    severity=item.get("severity", "medium"),
                ))
            return risks
        except Exception as e:
            print(f"    [!] 片段分析失败: {e}")
            return []

    def _summarize_risks(self, all_risks: list[RiskPoint]) -> list[RiskPoint]:
        """综合所有片段的风险点，提炼核心结果"""
        if not all_risks:
            return []

        if len(all_risks) <= 5:
            # 数量少就直接用
            for i, rp in enumerate(all_risks):
                rp.risk_id = f"R{i+1:02d}"
            return all_risks

        # 数量多则让 LLM 综合提炼
        all_dicts = [rp.to_dict() for rp in all_risks]
        import json
        user_msg = f"以下是从多个代码片段分析得到的风险点：\n\n{json.dumps(all_dicts, ensure_ascii=False, indent=2)}"

        try:
            summarized = self._llm.chat_json(SUMMARY_SYSTEM, user_msg)
            if not isinstance(summarized, list):
                return all_risks[:8]
            result = []
            for i, item in enumerate(summarized):
                result.append(RiskPoint(
                    risk_id=f"R{i+1:02d}",
                    location=item.get("location", ""),
                    risk_type=item.get("risk_type", ""),
                    description=item.get("description", ""),
                    trigger_condition=item.get("trigger_condition", ""),
                    test_suggestion=item.get("test_suggestion", ""),
                    severity=item.get("severity", "medium"),
                ))
            return result
        except Exception as e:
            print(f"[StaticAnalysisAgent] 综合提炼失败: {e}")
            return all_risks[:8]