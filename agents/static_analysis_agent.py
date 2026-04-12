# agents/static_analysis_agent.py  —— 静态分析智能体（增强版）
"""
改进点：
  1. 持久化历史：记录每次分析过的行范围，下次自动跳过
  2. LLM 智能选片段：不再靠固定关键词，让 LLM 读函数列表后决定分析哪些
  3. 多策略采样：结合关键词匹配 + LLM 推荐 + 随机采样，最大化覆盖
"""

import os
import re
import json
import random
import urllib.request
import zipfile
from dataclasses import dataclass, field
from core.llm_client import LLMClient

SQLITE3_SRC_URL = "https://www.sqlite.org/2024/sqlite-amalgamation-3460100.zip"
SQLITE3_SRC_DIR = "sqlite3_src"
SQLITE3_SRC_FILE = "sqlite3.c"

# 历史记录文件，记录已分析过的行范围
HISTORY_FILE = "static_analysis_history.json"

# 关键词分层：高风险 / 中风险 / 低风险
HIGH_RISK_PATTERNS = [
    r"\/\s*\(",           # 除法
    r"malloc\s*\(",       # 内存分配
    r"realloc\s*\(",
    r"strcpy\s*\(",       # 不安全字符串
    r"strcat\s*\(",
    r"sprintf\s*\(",
    r"gets\s*\(",
    r"memcpy\s*\(",
    r"overflow",
    r"underflow",
]
MED_RISK_PATTERNS = [
    r"atoi\s*\(",
    r"atof\s*\(",
    r"strlen\s*\(",
    r"assert\s*\(",
    r"SQLITE_MAX",
    r"INT_MAX",
    r"UINT_MAX",
    r"free\s*\(",
    r"NULL\s*==",
    r"==\s*NULL",
]
LOW_RISK_PATTERNS = [
    r"for\s*\(",
    r"while\s*\(",
    r"switch\s*\(",
    r"goto\s+",
    r"return\s+-",
]

SELECTOR_SYSTEM = """
你是一名 C 语言静态分析专家。用户会给你一份 C 源文件的函数列表（函数名和行号），
以及已经分析过的行范围（需要跳过）。

请从未分析过的函数中，选出最值得深入检查的 6 个函数，标准是：
1. 函数名暗示复杂逻辑（parse、compile、exec、eval、alloc、grow、expand、read、write）
2. 函数名暗示边界处理（limit、max、min、check、valid、safe）
3. 函数名暗示错误处理（error、fail、abort、panic、recover）
4. 与内存、字符串、数值转换相关
5. 优先选从未分析过的区域

返回 JSON 数组，每个元素是：
{"func_name": "函数名", "start_line": 行号, "reason": "选择原因（一句话）"}
"""

STATIC_ANALYSIS_SYSTEM = """
你是一名资深 C 语言安全分析专家。分析给定的代码片段，找出潜在风险点。

关注：整数溢出、除零、空指针、缓冲区溢出、内存泄漏、
      未检查返回值、边界条件、类型转换错误、竞态条件。

返回 JSON 数组：
[
  {
    "risk_id": "R01",
    "location": "函数名或描述",
    "risk_type": "风险类型",
    "description": "具体描述",
    "trigger_condition": "触发条件",
    "test_suggestion": "具体测试建议（SQL语句或操作）",
    "severity": "high/medium/low"
  }
]
没有风险则返回 []。
"""

SUMMARY_SYSTEM = """
综合多个代码片段的静态分析结果，提炼 5~8 个最重要的核心风险点，去重排序。
返回与输入相同格式的 JSON 数组。
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
            f"  触发: {self.trigger_condition}\n"
            f"  建议: {self.test_suggestion}\n"
            f"  严重: {self.severity}"
        )


@dataclass
class StaticAnalysisReport:
    source_file: str
    total_lines: int
    analyzed_snippets: int
    risk_points: list[RiskPoint] = field(default_factory=list)
    is_incremental: bool = False   # 是否为增量分析

    def summary_for_planner(self) -> str:
        if not self.risk_points:
            return "静态分析未发现明显风险点。"
        prefix = "（增量分析）" if self.is_incremental else ""
        lines = [
            f"{prefix}静态分析发现 {len(self.risk_points)} 个潜在风险点：", ""
        ]
        for rp in self.risk_points:
            lines.append(rp.to_prompt_text())
            lines.append("")
        return "\n".join(lines)


class StaticAnalysisAgent:
    def __init__(self, workdir: str = "."):
        self._llm = LLMClient()
        self._workdir = workdir
        self._history_path = os.path.join(workdir, HISTORY_FILE)
        self._history = self._load_history()

    # ── 历史管理 ──────────────────────────────────────────────────

    def _load_history(self) -> dict:
        """加载历史分析记录"""
        if os.path.exists(self._history_path):
            try:
                with open(self._history_path, encoding="utf-8") as f:
                    data = json.load(f)
                analyzed = data.get("analyzed_ranges", [])
                print(f"[StaticAnalysisAgent] 载入历史记录：已分析 {len(analyzed)} 个代码片段")
                return data
            except Exception:
                pass
        return {"analyzed_ranges": [], "analyzed_functions": []}

    def _save_history(self, new_ranges: list[tuple[int, int]], new_funcs: list[str]):
        """保存本次分析的行范围和函数名"""
        existing_ranges = self._history.get("analyzed_ranges", [])
        existing_funcs = self._history.get("analyzed_functions", [])
        existing_ranges.extend(new_ranges)
        existing_funcs.extend(new_funcs)
        # 去重
        unique_ranges = list({(s, e) for s, e in existing_ranges})
        unique_funcs = list(set(existing_funcs))
        self._history = {
            "analyzed_ranges": unique_ranges,
            "analyzed_functions": unique_funcs,
            "total_sessions": self._history.get("total_sessions", 0) + 1,
        }
        with open(self._history_path, "w", encoding="utf-8") as f:
            json.dump(self._history, f, ensure_ascii=False, indent=2)
        print(f"[StaticAnalysisAgent] 历史已更新：累计分析 {len(unique_ranges)} 个片段")

    def _is_already_analyzed(self, start: int, end: int) -> bool:
        """检查某行范围是否已分析过（重叠超过50%则跳过）"""
        for s, e in self._history.get("analyzed_ranges", []):
            overlap = min(end, e) - max(start, s)
            span = end - start
            if span > 0 and overlap / span > 0.5:
                return True
        return False

    def _analyzed_functions(self) -> set[str]:
        return set(self._history.get("analyzed_functions", []))

    # ── 主入口 ────────────────────────────────────────────────────

    def analyze(self, framework: dict) -> StaticAnalysisReport:
        project_name = framework.get("project_name", "")
        print(f"\n[StaticAnalysisAgent] 开始静态分析: {project_name}")

        src_path = self._get_source_file(framework)
        if not src_path:
            print("[StaticAnalysisAgent] ⚠️  无法获取源码，跳过。")
            return StaticAnalysisReport("", 0, 0)

        with open(src_path, "r", encoding="utf-8", errors="ignore") as f:
            source_lines = f.readlines()

        total_lines = len(source_lines)
        print(f"[StaticAnalysisAgent] 源码共 {total_lines:,} 行")

        # 提取函数列表
        functions = self._extract_functions(source_lines)
        print(f"[StaticAnalysisAgent] 识别到 {len(functions)} 个函数")

        # 三策略选片段
        snippets = self._select_snippets(source_lines, functions)
        print(f"[StaticAnalysisAgent] 本次分析 {len(snippets)} 个片段（跳过历史已分析片段）")

        if not snippets:
            print("[StaticAnalysisAgent] 所有片段均已在历史中分析过，尝试随机选取新片段...")
            snippets = self._random_sample(source_lines, n=4)

        # 逐片段分析
        all_risks = []
        analyzed_ranges = []
        analyzed_funcs = []

        for i, (label, snippet, start, end, func_name) in enumerate(snippets):
            print(f"  [{i+1}/{len(snippets)}] {label}")
            risks = self._analyze_snippet(label, snippet)
            all_risks.extend(risks)
            analyzed_ranges.append((start, end))
            if func_name:
                analyzed_funcs.append(func_name)

        # 保存历史
        self._save_history(analyzed_ranges, analyzed_funcs)

        # 综合提炼
        print(f"[StaticAnalysisAgent] 原始风险点 {len(all_risks)} 个，综合提炼中...")
        final_risks = self._summarize_risks(all_risks)

        is_incremental = len(self._history.get("analyzed_ranges", [])) > len(snippets)
        print(f"[StaticAnalysisAgent] ✅ 最终 {len(final_risks)} 个核心风险点")
        for rp in final_risks:
            icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(rp.severity, "⚪")
            print(f"  {icon} [{rp.risk_id}] {rp.risk_type} - {rp.description[:60]}")

        return StaticAnalysisReport(
            source_file=src_path,
            total_lines=total_lines,
            analyzed_snippets=len(snippets),
            risk_points=final_risks,
            is_incremental=is_incremental,
        )

    # ── 函数提取 ──────────────────────────────────────────────────

    def _extract_functions(self, lines: list[str]) -> list[dict]:
        """
        从 C 源码中提取函数定义列表。
        返回 [{"name": str, "line": int}, ...]
        """
        functions = []
        # 匹配 C 函数定义：返回类型 函数名(参数) {
        func_pattern = re.compile(
            r'^(?:static\s+|SQLITE_PRIVATE\s+|SQLITE_API\s+)?'
            r'(?:(?:unsigned|signed|const|volatile)\s+)?'
            r'\w[\w\s\*]*\s+'
            r'(\w+)\s*\([^;]*\)\s*\{',
            re.MULTILINE
        )
        for i, line in enumerate(lines):
            m = func_pattern.match(line.strip())
            if m:
                func_name = m.group(1)
                # 过滤掉宏、类型定义等
                if func_name not in ('if', 'for', 'while', 'switch', 'else'):
                    functions.append({"name": func_name, "line": i + 1})
        return functions

    # ── 三策略选片段 ──────────────────────────────────────────────

    def _select_snippets(
        self, lines: list[str], functions: list[dict], max_total: int = 7
    ) -> list[tuple]:
        """
        三策略选取代码片段：
        策略1：关键词命中（高→中→低优先级）
        策略2：LLM 推荐函数
        策略3：补充未覆盖区域
        返回 [(label, snippet_text, start, end, func_name), ...]
        """
        selected = []
        seen_starts = set()

        # ── 策略1：关键词命中，按优先级 ──────────────────────────
        keyword_hits = self._keyword_scan(lines)
        for start, end, label, pattern_level in keyword_hits:
            if len(selected) >= max_total // 2:
                break
            if self._is_already_analyzed(start, end):
                continue
            if start in seen_starts:
                continue
            snippet = "".join(lines[start:end])
            selected.append((label, snippet, start, end, None))
            seen_starts.add(start)

        # ── 策略2：LLM 推荐函数 ──────────────────────────────────
        llm_funcs = self._llm_recommend_functions(functions)
        for rec in llm_funcs:
            if len(selected) >= max_total:
                break
            func_line = rec.get("start_line", 1) - 1
            start = max(0, func_line)
            end = min(len(lines), func_line + 80)
            if self._is_already_analyzed(start, end):
                continue
            if start in seen_starts:
                continue
            snippet = "".join(lines[start:end])
            label = f"函数 {rec.get('func_name')}（LLM推荐：{rec.get('reason', '')}）"
            selected.append((label, snippet, start, end, rec.get("func_name")))
            seen_starts.add(start)

        # ── 策略3：随机补充未覆盖区域 ────────────────────────────
        if len(selected) < max_total:
            extra = self._random_sample(lines, n=max_total - len(selected), exclude_starts=seen_starts)
            selected.extend(extra)

        return selected[:max_total]

    def _keyword_scan(self, lines: list[str]) -> list[tuple]:
        """
        关键词扫描，返回 [(start, end, label, level), ...]
        按优先级排序：高风险优先
        """
        hits = []
        context = 50

        all_patterns = (
            [(p, "high") for p in HIGH_RISK_PATTERNS] +
            [(p, "medium") for p in MED_RISK_PATTERNS] +
            [(p, "low") for p in LOW_RISK_PATTERNS]
        )

        seen_centers = set()
        for i, line in enumerate(lines):
            for pattern, level in all_patterns:
                if re.search(pattern, line, re.IGNORECASE):
                    # 聚合相近行
                    center = i // context
                    if center in seen_centers:
                        continue
                    seen_centers.add(center)
                    start = max(0, i - context // 2)
                    end = min(len(lines), i + context // 2)
                    label = f"行 {start+1}~{end}（关键词: {pattern}，风险级别: {level}）"
                    hits.append((start, end, label, level))
                    break

        # 高风险优先排序
        priority = {"high": 0, "medium": 1, "low": 2}
        hits.sort(key=lambda x: priority.get(x[3], 3))
        return hits

    def _llm_recommend_functions(self, functions: list[dict]) -> list[dict]:
        """让 LLM 从函数列表中推荐最值得分析的函数"""
        if not functions:
            return []

        analyzed = self._analyzed_functions()

        # 过滤掉已分析的，采样发给 LLM（函数太多只发部分）
        unanalyzed = [f for f in functions if f["name"] not in analyzed]
        sample = random.sample(unanalyzed, min(80, len(unanalyzed)))

        func_list = "\n".join(f"  {f['name']} (行{f['line']})" for f in sample)
        already = ", ".join(list(analyzed)[:20]) if analyzed else "无"

        user_msg = (
            f"以下是 sqlite3.c 中的部分函数列表（共 {len(functions)} 个函数）：\n\n"
            f"{func_list}\n\n"
            f"已分析过的函数（请跳过）：{already}\n\n"
            f"请推荐 4~6 个最值得深入检查的函数。"
        )

        try:
            result = self._llm.chat_json(SELECTOR_SYSTEM, user_msg)
            if isinstance(result, list):
                print(f"  [StaticAnalysisAgent] LLM 推荐了 {len(result)} 个函数")
                return result
        except Exception as e:
            print(f"  [StaticAnalysisAgent] LLM 推荐失败: {e}")
        return []

    def _random_sample(
        self, lines: list[str],
        n: int = 3,
        context: int = 60,
        exclude_starts: set = None
    ) -> list[tuple]:
        """随机采样未分析过的代码片段"""
        exclude_starts = exclude_starts or set()
        total = len(lines)
        if total < context:
            return []

        results = []
        attempts = 0
        while len(results) < n and attempts < 50:
            attempts += 1
            start = random.randint(0, total - context)
            end = min(total, start + context)
            if self._is_already_analyzed(start, end):
                continue
            if start in exclude_starts:
                continue
            snippet = "".join(lines[start:end])
            label = f"行 {start+1}~{end}（随机采样）"
            results.append((label, snippet, start, end, None))
            exclude_starts.add(start)

        return results

    # ── LLM 分析 ──────────────────────────────────────────────────

    def _analyze_snippet(self, label: str, snippet: str) -> list[RiskPoint]:
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
        if not all_risks:
            return []
        if len(all_risks) <= 5:
            for i, rp in enumerate(all_risks):
                rp.risk_id = f"R{i+1:02d}"
            return all_risks

        all_dicts = [rp.to_dict() for rp in all_risks]
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

    # ── 源码获取 ──────────────────────────────────────────────────

    def _get_source_file(self, framework: dict) -> str:
        for sf in framework.get("source_files", []):
            full = os.path.join(self._workdir, sf)
            if os.path.isfile(full):
                return full

        project_name = framework.get("project_name", "").lower()
        if "sqlite" in project_name:
            return self._get_sqlite3_source()
        return ""

    def _get_sqlite3_source(self) -> str:
        candidates = [
            os.path.join(self._workdir, SQLITE3_SRC_FILE),
            os.path.join(self._workdir, SQLITE3_SRC_DIR, SQLITE3_SRC_FILE),
            os.path.join(self._workdir, SQLITE3_SRC_DIR,
                         "sqlite-amalgamation-3460100", SQLITE3_SRC_FILE),
        ]
        for p in candidates:
            if os.path.isfile(p):
                print(f"[StaticAnalysisAgent] 找到本地源码: {p}")
                return p

        print("[StaticAnalysisAgent] 本地无源码，尝试从 sqlite.org 下载...")
        confirm = input("[StaticAnalysisAgent] 是否下载 sqlite3.c？(y/n): ").strip().lower()
        if confirm != "y":
            return ""

        zip_path = os.path.join(self._workdir, "sqlite3_src.zip")
        extract_dir = os.path.join(self._workdir, SQLITE3_SRC_DIR)
        try:
            print(f"[StaticAnalysisAgent] 下载中: {SQLITE3_SRC_URL}")
            urllib.request.urlretrieve(SQLITE3_SRC_URL, zip_path)
            os.makedirs(extract_dir, exist_ok=True)
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(extract_dir)
            os.remove(zip_path)
            for root, dirs, files in os.walk(extract_dir):
                if SQLITE3_SRC_FILE in files:
                    found = os.path.join(root, SQLITE3_SRC_FILE)
                    print(f"[StaticAnalysisAgent] ✅ 源码: {found}")
                    return found
        except Exception as e:
            print(f"[StaticAnalysisAgent] 下载失败: {e}")
        return ""