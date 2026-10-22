"""
opencode_worker.py - Managed OpenCode CLI delegation for Jasper.

Fixes applied:
  * `opencode run` takes the message as a POSITIONAL argument. The previous
    `--prompt` flag does not exist, so the CLI printed its help text instead
    of running the task and every delegation silently failed.
  * `subprocess.run(timeout=...)` on Windows kills only the direct child; the
    child's own process tree survives. We now use Popen and terminate the whole
    tree with `taskkill /T /F /PID`.
  * Each job gets its own worker instance so concurrent jobs cannot clobber
    each other's state.
"""

import os
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional


def _kill_process_tree(pid: int) -> None:
    """Terminate a process and all of its children on Windows."""
    if os.name != "nt":
        try:
            os.killpg(os.getpgid(pid), 9)
        except Exception:
            pass
        return
    try:
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(pid)],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:
        pass


class OpenCodeWorker:
    """Managed worker for delegating coding/repository tasks to OpenCode CLI."""

    APPROVED_WORKSPACE = r"C:\Users\USER\kenny"
    DEFAULT_TIMEOUT = 300.0

    def __init__(self, timeout: Optional[float] = None, workspace: Optional[str] = None):
        self.timeout = self.DEFAULT_TIMEOUT if timeout is None else timeout
        self.workspace = workspace or self.APPROVED_WORKSPACE

    def run_task(self, task_description: str, workspace_path: Optional[str] = None) -> Dict[str, Any]:
        """Run an OpenCode non-interactive coding task in the approved workspace."""
        target_ws = os.path.abspath(workspace_path or self.workspace)

        # Enforce workspace security boundary
        approved_abs = os.path.abspath(self.APPROVED_WORKSPACE)
        if not (target_ws == approved_abs or target_ws.startswith(approved_abs + os.sep)):
            return {
                "status": "denied",
                "result": f"Access denied: workspace '{target_ws}' is outside approved root '{self.APPROVED_WORKSPACE}'.",
                "duration_sec": 0.0,
                "error": "Security boundary violation",
            }

        t0 = time.monotonic()
        # The message is a POSITIONAL argument. `--prompt` is not a valid flag.
        cmd = ["opencode", "run", task_description]
        proc = None
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=target_ws,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            try:
                stdout, stderr = proc.communicate(timeout=self.timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                _kill_process_tree(proc.pid)
                try:
                    stdout, stderr = proc.communicate(timeout=5)
                except Exception:
                    stdout, stderr = "", ""
                duration = round(time.monotonic() - t0, 3)
                return {
                    "status": "timed_out",
                    "result": f"OpenCode task timed out after {int(self.timeout)} seconds.",
                    "duration_sec": duration,
                    "error": "TimeoutExpired",
                }

            duration = round(time.monotonic() - t0, 3)
            if proc.returncode == 0:
                output = (stdout or "").strip() or "Task completed successfully by OpenCode."
                return {
                    "status": "completed",
                    "result": output,
                    "duration_sec": duration,
                    "error": None,
                }
            err_msg = (stderr or "").strip() or f"OpenCode exited with code {proc.returncode}"
            return {
                "status": "failed",
                "result": f"OpenCode task failed: {err_msg}",
                "duration_sec": duration,
                "error": err_msg,
            }

        except FileNotFoundError:
            duration = round(time.monotonic() - t0, 3)
            return {
                "status": "failed",
                "result": "OpenCode binary was not found on PATH.",
                "duration_sec": duration,
                "error": "FileNotFoundError",
            }
        except Exception as exc:
            duration = round(time.monotonic() - t0, 3)
            if proc is not None and proc.poll() is None:
                try:
                    proc.kill()
                    _kill_process_tree(proc.pid)
                except Exception:
                    pass
            return {
                "status": "failed",
                "result": f"Unexpected OpenCode worker error: {exc}",
                "duration_sec": duration,
                "error": str(exc),
            }
