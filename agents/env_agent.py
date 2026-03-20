# agents/env_agent.py  —— 环境探测智能体
"""
职责：
  1. 探测当前操作系统
  2. 若需要编译：搜索编译器，找不到则自动安装
  3. 若不需要编译：检查二进制工具是否可用，不可用则自动下载/安装
  4. 返回修正后的 framework 字典，供 PlannerAgent 使用
"""

import os
import platform
import urllib.request
import zipfile
import shutil
from core.terminal import TerminalExecutor
from core.llm_client import LLMClient


# ── Prompt：根据候选路径修正编译命令 ─────────────────────────────────
ENV_FIX_SYSTEM = """
你是一名系统环境配置专家。用户会给你：
1. 当前操作系统信息
2. 通过终端命令探测到的编译器候选路径列表
3. 用户填写的原始 compile_cmd 和 binary 路径

请你：
1. 从候选路径中选出最合适的编译器（优先选64位、版本新的）
2. 生成适合当前操作系统的修正后 compile_cmd（Windows 路径含空格时用双引号包裹）
3. 生成修正后的 binary 路径（Windows 需要 .exe 后缀）
4. 生成适合当前 OS 的可执行文件调用前缀（Windows 用 .\\path\\to\\file.exe，Linux/Mac 用 ./path/to/file）

返回 JSON：
{
  "compiler_path": "选中的编译器完整路径",
  "compile_cmd": "修正后的完整编译命令",
  "binary": "修正后的可执行文件路径",
  "run_prefix": "运行可执行文件的完整调用方式",
  "os_type": "Windows 或 Linux 或 Mac",
  "notes": "简短说明"
}
"""

# ── Prompt：生成编译器安装命令 ────────────────────────────────────────
ENV_INSTALL_SYSTEM = """
你是一名系统管理员。用户的机器上找不到所需编译器，请生成安装它的命令。

要求：
1. 根据操作系统生成正确的包管理器命令
2. Windows 优先用 winget，其次 choco
3. Ubuntu/Debian 用 apt-get，CentOS/RHEL 用 yum，Mac 用 brew
4. 命令必须是非交互式的（加 -y 或 --yes 等参数）
5. 只返回一条可直接执行的命令，不要任何解释、不要代码块标记
"""

# ── Prompt：安装后验证 ────────────────────────────────────────────────
ENV_VERIFY_SYSTEM = """
你是一名系统管理员。用户刚刚执行了编译器安装命令，请根据安装输出判断安装是否成功。

返回 JSON：
{
  "success": true 或 false,
  "likely_path": "编译器最可能的完整路径，不确定则为空字符串",
  "notes": "简短说明"
}
"""

# sqlite3 Windows 预编译下载地址（官方）
SQLITE3_WIN_URL = "https://www.sqlite.org/2024/sqlite-tools-win-x64-3460100.zip"
SQLITE3_WIN_ZIP = "sqlite3_tools.zip"
SQLITE3_WIN_DIR = "sqlite3_bin"


class EnvAgent:
    """
    Agent 0：环境探测与自动配置智能体。
    """

    def __init__(self, workdir: str = "."):
        self._terminal = TerminalExecutor(workdir=workdir)
        self._llm = LLMClient()
        self._os = platform.system()  # 'Windows' / 'Linux' / 'Darwin'
        self._workdir = workdir

    def detect_and_fix(self, framework: dict) -> dict:
        print("\n[EnvAgent] 开始探测系统环境...")
        print(f"[EnvAgent] 操作系统: {self._os} {platform.version()[:40]}")

        compile_cmd = framework.get("compile_cmd", "")
        binary = framework.get("binary", "")

        # ── 情况A：需要编译（C/C++ 项目）──────────────────────────
        if compile_cmd:
            return self._handle_compiler(framework)

        # ── 情况B：无需编译，检查二进制工具是否可用 ───────────────
        if binary:
            return self._handle_binary_tool(framework)

        return framework

    # ──────────────────────────────────────────────────────────────
    # 情况 A：编译器项目
    # ──────────────────────────────────────────────────────────────

    def _handle_compiler(self, framework: dict) -> dict:
        compile_cmd = framework.get("compile_cmd", "")
        compiler_type = self._detect_compiler_type(compile_cmd)
        print(f"[EnvAgent] 需要的编译器类型: {compiler_type}")

        candidates = self._find_compilers(compiler_type)
        print(f"[EnvAgent] 找到 {len(candidates)} 个候选编译器:")
        for c in candidates:
            print(f"  - {c}")

        if not candidates:
            print("[EnvAgent] ⚠️  未找到编译器，尝试自动安装...")
            candidates = self._auto_install_compiler(compiler_type)

        if not candidates:
            print("[EnvAgent] ❌ 自动安装失败，将使用原始命令（可能失败）。")
            return framework

        fixed = self._ask_llm_to_fix(framework, candidates)
        if fixed:
            print(f"[EnvAgent] ✅ 修正结果:")
            print(f"  编译器  : {fixed.get('compiler_path')}")
            print(f"  编译命令: {fixed.get('compile_cmd')}")
            print(f"  运行前缀: {fixed.get('run_prefix')}")
            print(f"  说明    : {fixed.get('notes')}")

            framework = dict(framework)
            framework["compile_cmd"] = fixed.get("compile_cmd", framework["compile_cmd"])
            framework["binary"] = fixed.get("binary", framework.get("binary", ""))
            framework["_run_prefix"] = fixed.get("run_prefix", "")
            framework["_os_type"] = fixed.get("os_type", self._os)
            os_note = (
                f"当前操作系统: {fixed.get('os_type', self._os)}。"
                f"运行可执行文件时使用: {fixed.get('run_prefix')}。"
            )
            if self._os == "Windows":
                os_note += "注意 Windows 下路径分隔符用反斜杠，不要用 ./。"
            framework["extra_notes"] = os_note + " " + framework.get("extra_notes", "")
        else:
            print("[EnvAgent] ⚠️  LLM 修正失败，使用原始配置。")

        return framework

    # ──────────────────────────────────────────────────────────────
    # 情况 B：二进制工具（sqlite3 等）
    # ──────────────────────────────────────────────────────────────

    def _handle_binary_tool(self, framework: dict) -> dict:
        binary = framework.get("binary", "")
        tool_name = os.path.basename(binary).replace(".exe", "")
        print(f"[EnvAgent] 检查工具是否可用: {tool_name}")

        # 先检查 PATH 里有没有
        tool_path = self._find_tool_in_path(tool_name)

        # 没有 → 检查项目目录里有没有（之前下载过的）
        if not tool_path:
            local_path = self._find_tool_local(tool_name)
            if local_path:
                tool_path = local_path
                print(f"[EnvAgent] 在本地目录找到: {tool_path}")

        # 还没有 → 自动下载/安装
        if not tool_path:
            print(f"[EnvAgent] ⚠️  未找到 {tool_name}，尝试自动获取...")
            tool_path = self._auto_get_binary_tool(tool_name)

        if not tool_path:
            print(f"[EnvAgent] ❌ 无法获取 {tool_name}，请手动安装。")
            return framework

        print(f"[EnvAgent] ✅ 工具路径: {tool_path}")

        # 更新 framework
        framework = dict(framework)
        framework["binary"] = tool_path
        framework["_tool_path"] = tool_path

        # 生成 OS 相关说明注入给 PlannerAgent
        if self._os == "Windows":
            # Windows 下 echo 管道方式
            os_note = (
                f"当前操作系统: Windows。"
                f"sqlite3 可执行文件路径: {tool_path}。"
                f"执行 SQL 的命令格式: \"{tool_path}\" :memory: \"SQL语句\" "
                f"或 echo SQL语句 | \"{tool_path}\" :memory: 。"
                f"多条SQL用分号分隔。不要用 ./ 前缀。"
            )
        else:
            os_note = (
                f"当前操作系统: {self._os}。"
                f"sqlite3 路径: {tool_path}。"
                f"执行 SQL 的命令格式: echo 'SQL' | {tool_path} :memory: 。"
            )

        framework["extra_notes"] = os_note + " " + framework.get("extra_notes", "")
        return framework

    def _find_tool_in_path(self, tool_name: str) -> str:
        """检查工具是否在 PATH 中可用"""
        if self._os == "Windows":
            r = self._terminal.run(f"where.exe {tool_name}.exe")
            if r.success and r.stdout.strip():
                return r.stdout.strip().splitlines()[0].strip()
            # 也试试不带 .exe
            r = self._terminal.run(f"where.exe {tool_name}")
            if r.success and r.stdout.strip():
                return r.stdout.strip().splitlines()[0].strip()
        else:
            r = self._terminal.run(f"which {tool_name}")
            if r.success and r.stdout.strip():
                return r.stdout.strip()
        return ""

    def _find_tool_local(self, tool_name: str) -> str:
        """在项目目录下查找工具（之前下载过的）"""
        exe_name = tool_name + (".exe" if self._os == "Windows" else "")
        candidates = [
            os.path.join(self._workdir, exe_name),
            os.path.join(self._workdir, SQLITE3_WIN_DIR, exe_name),
            os.path.join(self._workdir, SQLITE3_WIN_DIR, tool_name, exe_name),
        ]
        for p in candidates:
            if os.path.isfile(p):
                return p
        return ""

    def _auto_get_binary_tool(self, tool_name: str) -> str:
        """
        自动下载/安装二进制工具。
        目前支持 sqlite3，其他工具生成安装命令。
        """
        if tool_name == "sqlite3":
            return self._download_sqlite3()
        else:
            # 用包管理器安装
            return self._install_via_package_manager(tool_name)

    def _download_sqlite3(self) -> str:
        """
        Windows：直接从 sqlite.org 下载预编译 zip，解压到项目目录。
        Linux/Mac：用包管理器安装。
        """
        if self._os != "Windows":
            return self._install_via_package_manager("sqlite3")

        print(f"[EnvAgent] 正在从 sqlite.org 下载 sqlite3 预编译包...")
        print(f"[EnvAgent] URL: {SQLITE3_WIN_URL}")

        confirm = input("[EnvAgent] 是否下载并安装 sqlite3？(y/n): ").strip().lower()
        if confirm != "y":
            print("[EnvAgent] 用户取消下载。")
            return ""

        zip_path = os.path.join(self._workdir, SQLITE3_WIN_ZIP)
        extract_dir = os.path.join(self._workdir, SQLITE3_WIN_DIR)

        try:
            # 下载
            print("[EnvAgent] 下载中...")
            urllib.request.urlretrieve(SQLITE3_WIN_URL, zip_path)
            print(f"[EnvAgent] 下载完成: {zip_path}")

            # 解压
            os.makedirs(extract_dir, exist_ok=True)
            with zipfile.ZipFile(zip_path, 'r') as zf:
                zf.extractall(extract_dir)
            print(f"[EnvAgent] 解压到: {extract_dir}")

            # 找 sqlite3.exe
            for root, dirs, files in os.walk(extract_dir):
                for f in files:
                    if f.lower() == "sqlite3.exe":
                        found = os.path.join(root, f)
                        print(f"[EnvAgent] ✅ 找到 sqlite3.exe: {found}")
                        # 清理 zip
                        os.remove(zip_path)
                        return found

            print("[EnvAgent] ❌ 解压后未找到 sqlite3.exe")
            return ""

        except Exception as e:
            print(f"[EnvAgent] 下载失败: {e}")
            print("[EnvAgent] 请手动下载: https://www.sqlite.org/download.html")
            return ""

    def _install_via_package_manager(self, tool_name: str) -> str:
        """用 LLM 生成安装命令并执行"""
        install_cmd = self._ask_llm_install_cmd(tool_name)
        if not install_cmd:
            return ""

        print(f"[EnvAgent] 生成的安装命令: {install_cmd}")
        confirm = input("[EnvAgent] 是否执行此安装命令？(y/n): ").strip().lower()
        if confirm != "y":
            return ""

        print("[EnvAgent] 正在安装...")
        result = self._terminal.run(install_cmd, timeout=180)
        print(f"[EnvAgent] 安装退出码: {result.returncode}")

        # 安装后重新检查
        tool_path = self._find_tool_in_path(tool_name)
        return tool_path

    # ──────────────────────────────────────────────────────────────
    # 编译器相关
    # ──────────────────────────────────────────────────────────────

    def _auto_install_compiler(self, compiler_type: str) -> list[str]:
        install_cmd = self._ask_llm_install_cmd(compiler_type)
        if not install_cmd:
            return []

        print(f"[EnvAgent] 生成的安装命令: {install_cmd}")
        confirm = input("[EnvAgent] 是否执行此安装命令？(y/n): ").strip().lower()
        if confirm != "y":
            return []

        print("[EnvAgent] 正在安装，请稍候（最长等待 3 分钟）...")
        result = self._terminal.run(install_cmd, timeout=180)
        print(f"[EnvAgent] 安装退出码: {result.returncode}")

        verify = self._ask_llm_verify(install_cmd, result)
        if not verify or not verify.get("success"):
            print("[EnvAgent] ❌ 安装失败。")
            return []

        likely = verify.get("likely_path", "").strip()
        if likely and os.path.isfile(likely):
            return [likely]

        print("[EnvAgent] 安装成功，重新搜索编译器...")
        return self._find_compilers(compiler_type)

    def _detect_compiler_type(self, compile_cmd: str) -> str:
        cmd_lower = compile_cmd.lower()
        if "g++" in cmd_lower or ".cpp" in cmd_lower or ".cc" in cmd_lower:
            return "g++"
        elif "gcc" in cmd_lower or ".c" in cmd_lower:
            return "gcc"
        elif "clang++" in cmd_lower:
            return "clang++"
        elif "clang" in cmd_lower:
            return "clang"
        elif "javac" in cmd_lower:
            return "javac"
        elif "python" in cmd_lower:
            return "python"
        return "gcc"

    def _find_compilers(self, compiler_type: str) -> list[str]:
        candidates = []
        if self._os == "Windows":
            candidates += self._find_windows(compiler_type)
        else:
            candidates += self._find_unix(compiler_type)
        return list(dict.fromkeys(c for c in candidates if c.strip()))

    def _find_windows(self, compiler_type: str) -> list[str]:
        results = []

        r = self._terminal.run(f"where.exe {compiler_type}.exe")
        if r.success and r.stdout:
            results.extend(r.stdout.strip().splitlines())

        # PowerShell 数组写法，避免路径含空格的解析问题
        drive_list = []
        for d in "CDEFGH":
            if os.path.exists(d + ":\\"):
                drive_list.append(f"'{d}:\\'")
        if not drive_list:
            drive_list = ["'C:\\'", "'D:\\'"]
        drives_ps = ", ".join(drive_list)

        ps_script = (
            f"$paths = @({drives_ps}); "
            f"Get-ChildItem -Path $paths -Recurse "
            f"-Filter '{compiler_type}.exe' "
            f"-ErrorAction SilentlyContinue | "
            f"Select-Object -ExpandProperty FullName"
        )
        r = self._terminal.run(
            f'powershell -NoProfile -NonInteractive -Command "{ps_script}"',
            timeout=90,
        )
        if r.stdout:
            results.extend(r.stdout.strip().splitlines())

        return [p.strip() for p in results if p.strip() and compiler_type in p.lower()]

    def _find_unix(self, compiler_type: str) -> list[str]:
        results = []
        r = self._terminal.run(f"which {compiler_type}")
        if r.success and r.stdout:
            results.append(r.stdout.strip())
        r = self._terminal.run(
            f"find /usr /opt /home -name '{compiler_type}' -type f 2>/dev/null"
        )
        if r.stdout:
            results.extend(r.stdout.strip().splitlines())
        return results

    # ──────────────────────────────────────────────────────────────
    # LLM 辅助
    # ──────────────────────────────────────────────────────────────

    def _ask_llm_install_cmd(self, tool_name: str) -> str:
        user_msg = (
            f"操作系统: {self._os} {platform.version()[:60]}\n"
            f"需要安装的工具: {tool_name}\n"
            f"请生成安装命令。"
        )
        try:
            raw = self._llm.chat(ENV_INSTALL_SYSTEM, user_msg)
            cmd = raw.strip().strip("`").strip()
            return cmd.splitlines()[0].strip()
        except Exception as e:
            print(f"[EnvAgent] 生成安装命令失败: {e}")
            return ""

    def _ask_llm_verify(self, install_cmd: str, result) -> dict | None:
        user_msg = (
            f"操作系统: {self._os}\n"
            f"安装命令: {install_cmd}\n"
            f"退出码: {result.returncode}\n"
            f"stdout: {result.stdout[:500]}\n"
            f"stderr: {result.stderr[:500]}\n"
        )
        try:
            return self._llm.chat_json(ENV_VERIFY_SYSTEM, user_msg)
        except Exception as e:
            print(f"[EnvAgent] 验证失败: {e}")
            return None

    def _ask_llm_to_fix(self, framework: dict, candidates: list[str]) -> dict | None:
        user_msg = (
            f"操作系统: {self._os} {platform.version()[:60]}\n"
            f"原始 compile_cmd: {framework.get('compile_cmd', '')}\n"
            f"原始 binary: {framework.get('binary', '')}\n"
            f"源文件: {framework.get('source_files', [])}\n\n"
            f"找到的编译器候选路径:\n"
            + "\n".join(f"  - {c}" for c in candidates)
            + "\n\n请生成修正后的配置。"
        )
        try:
            return self._llm.chat_json(ENV_FIX_SYSTEM, user_msg)
        except Exception as e:
            print(f"[EnvAgent] LLM 调用失败: {e}")
            return None