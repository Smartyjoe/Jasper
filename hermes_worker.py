"""
hermes_worker.py - Managed worker for Hermes delegation.

Each worker instance owns its own state, so several jobs can run concurrently
without clobbering each other. The adapter now returns the Hermes session id,
which lets a follow-up call continue the same conversation.
"""

import enum
import sys
import time
from typing import Any, Dict, Optional

from hermes_adapter import HermesAdapter, HermesError


class WorkerState(enum.Enum):
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


class HermesWorker:
    """Managed async worker for Hermes tasks with state tracking and progress reporting."""

    def __init__(self, timeout: float = 300.0):
        self.adapter = HermesAdapter(timeout=timeout)
        self.timeout = timeout
        self.state = WorkerState.IDLE
        self.last_result: Optional[str] = None
        self.error: Optional[str] = None
        self.start_time: float = 0.0
        self.end_time: float = 0.0

    @property
    def session_id(self) -> Optional[str]:
        """The Hermes session id from the most recent completed call."""
        return self.adapter.last_session_id

    def run_task(self, query: str, resume: Optional[str] = None) -> Dict[str, Any]:
        """Execute a Hermes task while tracking lifecycle states."""
        self.state = WorkerState.STARTING
        self.start_time = time.monotonic()
        self.last_result = None
        self.error = None

        try:
            self.state = WorkerState.RUNNING
            result = self.adapter.ask(query, resume=resume)
            self.state = WorkerState.COMPLETED
            self.last_result = result
            self.end_time = time.monotonic()
            return {
                "status": "completed",
                "result": result,
                "duration_sec": round(self.end_time - self.start_time, 3),
                "error": None,
                "session_id": self.session_id,
            }
        except HermesError as herr:
            self.end_time = time.monotonic()
            if "timed out" in str(herr).lower():
                self.state = WorkerState.TIMED_OUT
                self.error = "Hermes timed out"
            else:
                self.state = WorkerState.FAILED
                self.error = str(herr)

            return {
                "status": self.state.value,
                "result": "Sorry, I couldn't reach my brain right now.",
                "duration_sec": round(self.end_time - self.start_time, 3),
                "error": self.error,
                "session_id": self.session_id,
            }
        except Exception as exc:
            self.end_time = time.monotonic()
            self.state = WorkerState.FAILED
            self.error = str(exc)
            return {
                "status": "failed",
                "result": "Sorry, an unexpected error occurred while consulting Hermes.",
                "duration_sec": round(self.end_time - self.start_time, 3),
                "error": str(exc),
                "session_id": self.session_id,
            }
