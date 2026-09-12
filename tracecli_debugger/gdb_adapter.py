"""
GDB Adapter.

This module is the ONLY layer that interacts directly with the GDB process.
It launches `gdb --interpreter=mi2`, writes MI commands to stdin, and
reads MI output from stdout until the `(gdb)` prompt reappears.

Higher-level modules only communicate in terms of DebugAction and Evidence,
making this adapter replaceable by an LLDB adapter in the future.
"""
import fcntl
import os
import re
import select
import shutil
import subprocess
import time
from typing import List, Optional


class GDBNotFoundError(RuntimeError):
    """Raised when the GDB binary is not found on PATH."""


class GDBStartupError(RuntimeError):
    """Raised when GDB fails to launch or process pipe breaks."""


class GDBTimeoutError(RuntimeError):
    """Raised when GDB or the inferior does not respond before timeout."""


class GDBAdapter:
    PROMPT = "(gdb)"

    def __init__(self, executable: str, gdb_path: str = "gdb", timeout: float = 10.0):
        if not shutil.which(gdb_path):
            raise GDBNotFoundError(f"gdb executable not found on PATH: {gdb_path!r}")
        if not os.path.isfile(executable):
            raise FileNotFoundError(f"target executable not found: {executable!r}")

        self.executable = os.path.abspath(executable)
        self.gdb_path = gdb_path
        self.timeout = timeout
        self.proc: Optional[subprocess.Popen] = None
        self._buf = ""

    def start(self) -> List[str]:
        try:
            self.proc = subprocess.Popen(
                [self.gdb_path, "--nx", "-q", "--interpreter=mi2", self.executable],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=0,
            )
        except OSError as e:
            raise GDBStartupError(f"failed to start gdb: {e}") from e

        fd = self.proc.stdout.fileno()
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

        return self._read_until_prompt()

    def send(self, mi_command: str) -> List[str]:
        self._write(mi_command)
        return self._read_until_prompt()

    def _write(self, mi_command: str) -> None:
        if self.proc is None or self.proc.poll() is not None:
            raise GDBStartupError("gdb process is not running")
        try:
            self.proc.stdin.write((mi_command + "\n").encode())
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            raise GDBStartupError(f"gdb process pipe closed unexpectedly: {e}") from e

    def _read_until_prompt(self, deadline: Optional[float] = None) -> List[str]:
        lines: List[str] = []
        fd = self.proc.stdout.fileno()
        actual_deadline = deadline if deadline is not None else time.time() + self.timeout

        while True:
            # Drain complete lines from internal buffer before reading fd
            while "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                lines.append(line)
                if line.strip() == self.PROMPT:
                    return lines

            remaining = actual_deadline - time.time()
            if remaining <= 0:
                raise GDBTimeoutError(f"gdb did not respond within {self.timeout}s")

            ready, _, _ = select.select([fd], [], [], min(remaining, 0.2))
            if not ready:
                continue

            try:
                chunk = os.read(fd, 65536)
            except BlockingIOError:
                continue

            if not chunk:
                # Process closed output / EOF
                if self._buf:
                    lines.append(self._buf)
                    self._buf = ""
                return lines

            self._buf += chunk.decode(errors="replace")

    def send_and_wait_for_stop(self, mi_command: str, overall_timeout: Optional[float] = None) -> List[str]:
        """
        Executes an inferior-running command (-exec-run, -exec-continue, -exec-step, -exec-next).

        GDB MI defaults to asynchronous mode: the command returns an immediate
        '^running' response with a '(gdb)' prompt. The actual '*stopped',
        'reason=exited', or signal notification arrives later with its own prompt.
        This method reads output chunks until an actual stop/exit/error event is received.
        """
        self._write(mi_command)

        timeout_duration = overall_timeout or self.timeout
        deadline = time.time() + timeout_duration
        all_lines: List[str] = []

        while True:
            chunk = self._read_until_prompt(deadline=deadline)
            all_lines.extend(chunk)
            joined = "\n".join(chunk)

            if (
                "*stopped" in joined
                or "^error" in joined
                or re.search(r'reason="exited', joined)
            ):
                return all_lines

            if not chunk:
                return all_lines

            if time.time() >= deadline:
                raise GDBTimeoutError(f"target execution timed out after {timeout_duration}s")

    def close(self) -> None:
        if self.proc is None:
            return

        if self.proc.poll() is None:
            try:
                self._write("-gdb-exit")
            except Exception:
                pass
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    self.proc.terminate()
                    self.proc.wait(timeout=1)
                except (subprocess.TimeoutExpired, OSError):
                    try:
                        self.proc.kill()
                    except OSError:
                        pass

        try:
            if self.proc.stdin:
                self.proc.stdin.close()
            if self.proc.stdout:
                self.proc.stdout.close()
        except OSError:
            pass

        self.proc = None
