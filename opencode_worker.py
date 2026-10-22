import os
import subprocess
import sys
import time
from typing import Any, Dict, Optional


class OpenCodeWorker:
    """Managed worker for delegating coding/repository tasks to OpenCode CLI within approved workspace bounds."""

    APPROVED_WORKSPACE = r"C:\Users\USER\kenny"
    DEFAULT_TIMEOUT = 60.0

    def run_task(self, task_description: str, workspace_path: Optional[str] = None) -> Dict[str, Any]:
        """Run an OpenCode non-interactive coding task in the approved workspace."""
        target_ws = os.path.abspath(workspace_path or self.APPROVED_WORKSPACE)

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
        try:
            # Run opencode run command
            cmd = ["opencode", "run", "--prompt", task_description]
            res = subprocess.run(
                cmd,
                cwd=target_ws,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=self.DEFAULT_TIMEOUT,
            )

            duration = round(time.monotonic() - t0, 3)

            if res.returncode == 0:
                output = res.stdout.strip() or "Task completed successfully by OpenCode."
                return {
                    "status": "completed",
                    "result": output,
                    "duration_sec": duration,
                    "error": None,
                }
            else:
                err_msg = res.stderr.strip() if res.stderr else f"OpenCode exited with code {res.returncode}"
                return {
                    "status": "failed",
                    "result": f"OpenCode task failed: {err_msg}",
                    "duration_sec": duration,
                    "error": err_msg,
                }

        except subprocess.TimeoutExpired:
            duration = round(time.monotonic() - t0, 3)
            return {
                "status": "timed_out",
                "result": "OpenCode task timed out after 60 seconds.",
                "duration_sec": duration,
                "error": "TimeoutExpired",
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
            return {
                "status": "failed",
                "result": f"Unexpected OpenCode worker error: {exc}",
                "duration_sec": duration,
                "error": str(exc),
            }
