# core/terminal.py  —— 与本地终端交互的执行器
import subprocess
import shlex
import os
from dataclasses import dataclass, field
from typing import Optional
from config import TERMINAL_TIMEOUT


@dataclass
class CmdResult:
    """单条命令的执行结果"""
    command: str
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    error: Optional[str] = None

    @property
    def success(self) -> bool:
        return self.returncode == 0 and not self.timed_out and not self.error

    def to_dict(self) -> dict:
        return {
            "command": self.command,
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "timed_out": self.timed_out,
            "success": self.success,
            "error": self.error,
        }


class TerminalExecutor:
    """
    封装 subprocess，让 Agent 可以像操作终端一样执行命令。
    支持：shell 命令、工作目录切换、超时控制。
    """

    def __init__(self, workdir: str = "."):
        self.workdir = os.path.abspath(workdir)
        self._history: list[CmdResult] = []

    def run(self, command: str, timeout: int = TERMINAL_TIMEOUT) -> CmdResult:
        """
        执行一条 shell 命令，返回 CmdResult。
        command 可以是完整 shell 字符串，例如 'gcc foo.c -o foo && ./foo 1 2'
        """
        print(f"  [Terminal] $ {command}")
        try:
            # 把当前 Python 进程的完整 PATH 传给子进程
            # 解决 conda 环境下子进程 PATH 丢失的问题
            env = os.environ.copy()
            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=self.workdir,
                env=env,
            )
            result = CmdResult(
                command=command,
                returncode=proc.returncode,
                stdout=proc.stdout.strip(),
                stderr=proc.stderr.strip(),
            )
        except subprocess.TimeoutExpired:
            result = CmdResult(
                command=command,
                returncode=-1,
                stdout="",
                stderr="",
                timed_out=True,
            )
        except Exception as e:
            result = CmdResult(
                command=command,
                returncode=-1,
                stdout="",
                stderr="",
                error=str(e),
            )

        # 打印简短回显
        if result.stdout:
            print(f"  [stdout] {result.stdout[:200]}")
        if result.stderr:
            print(f"  [stderr] {result.stderr[:200]}")
        if result.timed_out:
            print("  [!] 命令超时")

        self._history.append(result)
        return result

    def run_batch(self, commands: list[str]) -> list[CmdResult]:
        """顺序执行多条命令"""
        return [self.run(cmd) for cmd in commands]

    def change_dir(self, path: str):
        """切换工作目录"""
        self.workdir = os.path.abspath(path)
        print(f"  [Terminal] workdir -> {self.workdir}")

    @property
    def history(self) -> list[CmdResult]:
        return self._history