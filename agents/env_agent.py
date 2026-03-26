# agents/env_agent.py  —— 环境探测智能体
import os
import platform
import glob
import shutil
from core.terminal import TerminalExecutor
from core.llm_client import LLMClient

SQLITE3_WIN_URL = "https://www.sqlite.org/2024/sqlite-tools-win-x64-3460100.zip"
SQLITE3_WIN_ZIP = "sqlite3_tools.zip"
SQLITE3_WIN_DIR = "sqlite3_bin"

ENV_FIX_SYSTEM = """
你是系统环境配置专家。给你编译器候选路径列表和原始命令，请：
1. 选最合适的编译器（优先64位、版本新的）
2. 生成修正后的 compile_cmd（Windows路径含空格用双引号）
3. 生成修正后的 binary 路径（Windows加.exe后缀）
4. 生成运行前缀

返回JSON：
{"compiler_path":"","compile_cmd":"","binary":"","run_prefix":"","os_type":"Windows或Linux或Mac","notes":""}
"""

ENV_INSTALL_SYSTEM = """
用户机器上找不到编译器，生成安装命令。
要求：非交互式（加-y），只返回一条命令，不要解释不要代码块。
Windows用winget，Ubuntu用apt-get，Mac用brew。
"""

ENV_VERIFY_SYSTEM = """
判断安装是否成功，返回JSON：
{"success":true或false,"likely_path":"编译器路径或空","notes":"说明"}
"""


class EnvAgent:
    def __init__(self, workdir: str = "."):
        self._terminal = TerminalExecutor(workdir=workdir)
        self._llm = LLMClient()
        self._os = platform.system()
        self._workdir = workdir

    def detect_and_fix(self, framework: dict) -> dict:
        print("\n[EnvAgent] 开始探测系统环境...")
        print(f"[EnvAgent] 操作系统: {self._os} {platform.version()[:40]}")

        compile_cmd = framework.get("compile_cmd", "")
        binary = framework.get("binary", "")

        if compile_cmd:
            return self._handle_compiler(framework)
        if binary:
            return self._handle_binary_tool(framework)
        return framework

    # ── 编译器项目 ────────────────────────────────────────────────

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
            print("[EnvAgent] ❌ 自动安装失败，将使用原始命令。")
            return framework

        fixed = self._ask_llm_to_fix(framework, candidates)
        if fixed:
            print(f"[EnvAgent] ✅ 修正结果: {fixed.get('notes')}")
            framework = dict(framework)
            framework["compile_cmd"] = fixed.get("compile_cmd", framework["compile_cmd"])
            framework["binary"] = fixed.get("binary", framework.get("binary", ""))
            framework["_run_prefix"] = fixed.get("run_prefix", "")
            framework["_os_type"] = fixed.get("os_type", self._os)
            framework["_tool_path"] = fixed.get("compiler_path", "")
            os_note = (
                f"当前操作系统: {fixed.get('os_type', self._os)}。"
                f"运行可执行文件: {fixed.get('run_prefix')}。"
            )
            if self._os == "Windows":
                os_note += "Windows下用反斜杠，不要用./。"
            framework["extra_notes"] = os_note + " " + framework.get("extra_notes", "")
        return framework

    # ── 二进制工具（sqlite3等）────────────────────────────────────

    def _handle_binary_tool(self, framework: dict) -> dict:
        binary = framework.get("binary", "")
        tool_name = os.path.basename(binary).replace(".exe", "")
        print(f"[EnvAgent] 检查工具是否可用: {tool_name}")

        tool_path = self._find_tool(tool_name)

        if not tool_path:
            print(f"[EnvAgent] ⚠️  未找到 {tool_name}，尝试自动获取...")
            tool_path = self._auto_get_binary_tool(tool_name)

        if not tool_path:
            print(f"[EnvAgent] ❌ 无法获取 {tool_name}，请手动安装。")
            return framework

        print(f"[EnvAgent] ✅ 工具路径: {tool_path}")

        framework = dict(framework)
        framework["binary"] = tool_path
        framework["_tool_path"] = tool_path

        quoted = f'"{tool_path}"'
        if self._os == "Windows":
            os_note = (
                f"当前操作系统: Windows。"
                f"sqlite3绝对路径: {tool_path}。"
                f"每条命令必须用此格式：{quoted} :memory: \"SQL;\" "
                f"禁止使用相对路径或裸名sqlite3。"
            )
        else:
            os_note = (
                f"当前操作系统: {self._os}。"
                f"sqlite3路径: {tool_path}。"
                f"调用格式: {quoted} :memory: \"SQL;\" 。"
            )
        framework["extra_notes"] = os_note + " " + framework.get("extra_notes", "")
        return framework

    # ── 核心：查找工具（Python原生，不依赖子进程PATH）────────────

    def _find_tool(self, tool_name: str) -> str:
        """
        按优先级查找工具：
        1. shutil.which（读当前Python进程PATH，conda激活后可用）
        2. CONDA_PREFIX环境变量直接拼路径
        3. glob搜索Anaconda目录
        4. glob搜索项目本地目录（之前下载的）
        """
        exe_name = tool_name + (".exe" if self._os == "Windows" else "")

        # 1. shutil.which
        found = shutil.which(exe_name) or shutil.which(tool_name)
        if found:
            print(f"  [EnvAgent] shutil.which 找到: {found}")
            return found

        # 2. 当前 conda 环境
        conda_prefix = os.environ.get("CONDA_PREFIX", "")
        if conda_prefix and self._os == "Windows":
            candidate = os.path.join(conda_prefix, "Library", "bin", exe_name)
            if os.path.isfile(candidate):
                print(f"  [EnvAgent] conda环境找到: {candidate}")
                return candidate

        # 3. glob搜Anaconda目录
        if self._os == "Windows":
            for d in "CDEFGH":
                if not os.path.exists(d + ":\\"):
                    continue
                for sub in ["Anaconda", "anaconda3", "miniconda3", "miniconda", "Anaconda3"]:
                    root = f"{d}:\\{sub}"
                    if not os.path.exists(root):
                        continue
                    pattern = os.path.join(root, "**", exe_name)
                    hits = glob.glob(pattern, recursive=True)
                    if hits:
                        # 优先选当前conda环境
                        for h in hits:
                            if conda_prefix and conda_prefix.lower() in h.lower():
                                print(f"  [EnvAgent] glob找到(当前环境): {h}")
                                return h
                        print(f"  [EnvAgent] glob找到: {hits[0]}")
                        return hits[0]
        else:
            r = self._terminal.run(f"which {tool_name}")
            if r.success and r.stdout.strip():
                return r.stdout.strip()

        # 4. 项目本地目录（之前下载的）
        for candidate in [
            os.path.join(self._workdir, exe_name),
            os.path.join(self._workdir, SQLITE3_WIN_DIR, exe_name),
        ]:
            if os.path.isfile(candidate):
                print(f"  [EnvAgent] 本地目录找到: {candidate}")
                return candidate

        return ""

    # ── 自动安装 ──────────────────────────────────────────────────

    def _auto_get_binary_tool(self, tool_name: str) -> str:
        if tool_name == "sqlite3":
            return self._download_sqlite3()
        return self._install_via_package_manager(tool_name)

    def _download_sqlite3(self) -> str:
        if self._os != "Windows":
            return self._install_via_package_manager("sqlite3")

        print(f"[EnvAgent] 正在从 sqlite.org 下载 sqlite3 预编译包...")
        confirm = input("[EnvAgent] 是否下载并安装 sqlite3？(y/n): ").strip().lower()
        if confirm != "y":
            return ""

        import urllib.request, zipfile
        zip_path = os.path.join(self._workdir, SQLITE3_WIN_ZIP)
        extract_dir = os.path.join(self._workdir, SQLITE3_WIN_DIR)

        try:
            urllib.request.urlretrieve(SQLITE3_WIN_URL, zip_path)
            os.makedirs(extract_dir, exist_ok=True)
            with zipfile.ZipFile(zip_path, 'r') as zf:
                zf.extractall(extract_dir)
            os.remove(zip_path)
            for root, dirs, files in os.walk(extract_dir):
                for f in files:
                    if f.lower() == "sqlite3.exe":
                        found = os.path.join(root, f)
                        print(f"[EnvAgent] ✅ sqlite3.exe: {found}")
                        return found
        except Exception as e:
            print(f"[EnvAgent] 下载失败: {e}")
        return ""

    def _install_via_package_manager(self, tool_name: str) -> str:
        install_cmd = self._ask_llm_install_cmd(tool_name)
        if not install_cmd:
            return ""
        print(f"[EnvAgent] 安装命令: {install_cmd}")
        confirm = input("[EnvAgent] 是否执行？(y/n): ").strip().lower()
        if confirm != "y":
            return ""
        self._terminal.run(install_cmd, timeout=180)
        return self._find_tool(tool_name)

    # ── 编译器搜索 ────────────────────────────────────────────────

    def _find_compilers(self, compiler_type: str) -> list[str]:
        exe_name = compiler_type + (".exe" if self._os == "Windows" else "")

        # shutil.which 优先
        found = shutil.which(exe_name) or shutil.which(compiler_type)
        candidates = [found] if found else []

        if self._os == "Windows":
            # glob搜Dev-Cpp、MinGW等常见位置
            for d in "CDEFGH":
                if not os.path.exists(d + ":\\"):
                    continue
                for sub in ["Dev-Cpp", "MinGW", "mingw64", "TDM-GCC-64", "msys64"]:
                    root = f"{d}:\\{sub}"
                    if not os.path.exists(root):
                        continue
                    pattern = os.path.join(root, "**", exe_name)
                    hits = glob.glob(pattern, recursive=True)
                    candidates.extend(hits)
        else:
            r = self._terminal.run(f"which {compiler_type}")
            if r.success and r.stdout.strip():
                candidates.append(r.stdout.strip())

        return list(dict.fromkeys(c for c in candidates if c and os.path.isfile(c)))

    def _auto_install_compiler(self, compiler_type: str) -> list[str]:
        install_cmd = self._ask_llm_install_cmd(compiler_type)
        if not install_cmd:
            return []
        print(f"[EnvAgent] 安装命令: {install_cmd}")
        confirm = input("[EnvAgent] 是否执行？(y/n): ").strip().lower()
        if confirm != "y":
            return []
        print("[EnvAgent] 安装中（最长3分钟）...")
        self._terminal.run(install_cmd, timeout=180)
        return self._find_compilers(compiler_type)

    def _detect_compiler_type(self, compile_cmd: str) -> str:
        cmd = compile_cmd.lower()
        if "g++" in cmd or ".cpp" in cmd or ".cc" in cmd:
            return "g++"
        elif "gcc" in cmd or ".c" in cmd:
            return "gcc"
        elif "clang" in cmd:
            return "clang"
        elif "javac" in cmd:
            return "javac"
        return "gcc"

    # ── LLM 辅助 ─────────────────────────────────────────────────

    def _ask_llm_install_cmd(self, tool_name: str) -> str:
        try:
            raw = self._llm.chat(
                ENV_INSTALL_SYSTEM,
                f"OS: {self._os} {platform.version()[:40]}\n需要安装: {tool_name}"
            )
            return raw.strip().strip("`").splitlines()[0].strip()
        except Exception as e:
            print(f"[EnvAgent] 生成安装命令失败: {e}")
            return ""

    def _ask_llm_to_fix(self, framework: dict, candidates: list[str]) -> dict | None:
        user_msg = (
            f"OS: {self._os}\n"
            f"compile_cmd: {framework.get('compile_cmd','')}\n"
            f"binary: {framework.get('binary','')}\n"
            f"candidates:\n" + "\n".join(f"  - {c}" for c in candidates)
        )
        try:
            return self._llm.chat_json(ENV_FIX_SYSTEM, user_msg)
        except Exception as e:
            print(f"[EnvAgent] LLM调用失败: {e}")
            return None