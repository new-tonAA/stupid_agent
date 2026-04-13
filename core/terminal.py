import os
import json
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from config import OUTPUT_DIR, TERMINAL_TIMEOUT


@dataclass
class CmdResult:
    command: str
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    error: Optional[str] = None
    elapsed: float = 0.0
    timestamp: float = 0.0

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
            "elapsed": round(self.elapsed, 3),
            "timestamp": self.timestamp,
        }


class SessionRecorder:
    """Persist all terminal session events as JSONL."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        self.path = os.path.join(OUTPUT_DIR, f"session_{session_id}.jsonl")
        self._f = open(self.path, "w", encoding="utf-8")
        self._pending_count = 0
        self._last_flush = time.time()

    def write(self, event_type: str, data: str):
        event = {
            "ts": time.time(),
            "type": event_type,
            "data": data,
        }
        self._f.write(json.dumps(event, ensure_ascii=False) + "\n")
        self._pending_count += 1
        now = time.time()
        if self._pending_count >= 50 or (now - self._last_flush) >= 0.5:
            self._f.flush()
            self._pending_count = 0
            self._last_flush = now

    def close(self):
        if self._f and not self._f.closed:
            try:
                self._f.flush()
            except Exception:
                pass
            self._f.close()

    @staticmethod
    def load(path: str) -> list[dict]:
        events = []
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        events.append(json.loads(line))
        except Exception:
            pass
        return events


# Optional hooks set by web_app.py
_push_hook: Optional[Callable[[str, str], None]] = None
_session_recorder: Optional[SessionRecorder] = None
_console_echo: bool = True


def set_push_hook(fn: Optional[Callable[[str, str], None]]):
    global _push_hook
    _push_hook = fn


def set_session_recorder(recorder: Optional[SessionRecorder]):
    global _session_recorder
    _session_recorder = recorder


def set_console_echo(enabled: bool):
    global _console_echo
    _console_echo = bool(enabled)


def _emit(event_type: str, data: str):
    """Emit to console, session recorder, and optional web push hook."""
    prefix_map = {
        "cmd": "  [Terminal] $ ",
        "stdout": "  [stdout] ",
        "stderr": "  [stderr] ",
        "rc": "  [rc] ",
        "info": "  [info] ",
        "error": "  [!] ",
    }
    if _console_echo:
        print(prefix_map.get(event_type, "  ") + data)

    if _session_recorder:
        _session_recorder.write(event_type, data)

    if _push_hook:
        _push_hook(event_type, data)


class TerminalExecutor:
    """Run shell commands with realtime stdout/stderr streaming."""

    def __init__(self, workdir: str = "."):
        self.workdir = os.path.abspath(workdir)
        self._history: list[CmdResult] = []

    def run(self, command: str, timeout: int = TERMINAL_TIMEOUT) -> CmdResult:
        _emit("cmd", command)
        t0 = time.time()

        stdout_lines: list[str] = []
        stderr_lines: list[str] = []

        try:
            env = os.environ.copy()
            proc = subprocess.Popen(
                command,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                cwd=self.workdir,
                env=env,
            )

            def stream_reader(stream, event_type: str, collector: list[str]):
                try:
                    for raw in iter(stream.readline, ""):
                        line = raw.rstrip("\r\n")
                        if not line:
                            continue
                        collector.append(line)
                        _emit(event_type, line)
                finally:
                    try:
                        stream.close()
                    except Exception:
                        pass

            t_out = threading.Thread(
                target=stream_reader,
                args=(proc.stdout, "stdout", stdout_lines),
                daemon=True,
            )
            t_err = threading.Thread(
                target=stream_reader,
                args=(proc.stderr, "stderr", stderr_lines),
                daemon=True,
            )
            t_out.start()
            t_err.start()

            timed_out = False
            try:
                returncode = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                returncode = -1
                _emit("error", f"Command timed out ({timeout}s)")
                proc.kill()

            t_out.join(timeout=2)
            t_err.join(timeout=2)

            elapsed = time.time() - t0
            result = CmdResult(
                command=command,
                returncode=returncode,
                stdout="\n".join(stdout_lines).strip(),
                stderr="\n".join(stderr_lines).strip(),
                timed_out=timed_out,
                elapsed=elapsed,
                timestamp=t0,
            )

            _emit("rc", str(returncode))

        except Exception as e:
            elapsed = time.time() - t0
            _emit("error", str(e))
            result = CmdResult(
                command=command,
                returncode=-1,
                stdout="",
                stderr="",
                error=str(e),
                elapsed=elapsed,
                timestamp=t0,
            )

        self._history.append(result)
        return result

    def run_batch(self, commands: list[str]) -> list[CmdResult]:
        return [self.run(cmd) for cmd in commands]

    def change_dir(self, path: str):
        self.workdir = os.path.abspath(path)
        _emit("info", f"workdir => {self.workdir}")

    @property
    def history(self) -> list[CmdResult]:
        return self._history
