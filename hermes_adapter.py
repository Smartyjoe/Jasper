"""
hermes_adapter.py - Thin, safe wrapper around the Hermes CLI.

Fixes applied:
  * The `session_id:` line is now captured instead of discarded, so a follow-up
    call can continue the same Hermes conversation with `--resume`.
  * `subprocess.run(timeout=...)` on Windows kills only the direct child and
    leaves its process tree running. We now use Popen and terminate the whole
    tree with `taskkill /T /F /PID`.
"""

import os
import subprocess
import sys
import threading
from typing import Any, Dict, Optional


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


class HermesError(Exception):
    """Raised when Hermes subprocess fails or times out."""


class HermesAdapter:
    def __init__(self, timeout: float = 30.0):
        """
        Initialize Hermes adapter with specified timeout.

        Args:
            timeout: Maximum seconds to wait for Hermes response before timing out.
        """
        self.timeout = timeout
        self.last_session_id: Optional[str] = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ public

    def ask(self, query: str, resume: Optional[str] = None) -> str:
        """
        Send `query` to Hermes and return the plain-text answer.
        Raises HermesError on failure.

        Args:
            query: The text to send to Hermes.
            resume: Optional Hermes session id to continue instead of starting
                    a brand new session.
        """
        cmd = ["hermes", "chat", "--query-file", "-", "-Q", "--source", "tool"]
        if resume:
            cmd += ["--resume", resume]

        proc = None
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            try:
                stdout, stderr = proc.communicate(input=query, timeout=self.timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                _kill_process_tree(proc.pid)
                try:
                    proc.communicate(timeout=5)
                except Exception:
                    pass
                print(f"[WARN] Hermes timed out after {self.timeout}s", file=sys.stderr)
                raise HermesError("Hermes timed out")

            if proc.returncode != 0:
                error_msg = (stderr or "").strip() or "Unknown error"
                print(f"[ERROR] Hermes exited with code {proc.returncode}: {error_msg}", file=sys.stderr)
                raise HermesError(f"Hermes exited with code {proc.returncode}: {error_msg}")

            if stderr and stderr.strip():
                print(f"[WARN] Hermes stderr: {stderr.strip()}", file=sys.stderr)

            # Parse stdout: drop the session_id header line, keep the rest.
            lines = (stdout or "").split("\n")
            answer_lines: list[str] = []
            session_id: Optional[str] = None
            for line in lines:
                stripped = line.strip()
                if not stripped:
                    continue
                if stripped.startswith("session_id:"):
                    session_id = stripped.split(":", 1)[1].strip() or None
                    continue
                answer_lines.append(line)

            if session_id:
                with self._lock:
                    self.last_session_id = session_id

            answer = "\n".join(answer_lines).strip()
            if not answer:
                raise HermesError("Hermes returned empty response")
            return answer

        except FileNotFoundError:
            print("[ERROR] hermes binary not found on PATH", file=sys.stderr)
            raise HermesError("hermes not found")
        except OSError as e:
            print(f"[ERROR] OS error when running Hermes: {e}", file=sys.stderr)
            raise HermesError(f"OS error: {e}")
        except HermesError:
            raise
        except Exception as exc:
            print(f"[ERROR] Unexpected Hermes error: {exc}", file=sys.stderr)
            if proc is not None and proc.poll() is None:
                try:
                    proc.kill()
                    _kill_process_tree(proc.pid)
                except Exception:
                    pass
            raise HermesError(f"Unexpected error: {exc}")
