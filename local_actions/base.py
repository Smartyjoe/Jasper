import os
import time
from typing import Any, Dict, List, Optional


class ActionError(Exception):
    """Base exception for action execution errors."""
    pass


class Action:
    """Base class for all local deterministic actions."""

    name: str = "base"
    description: str = "Base action"
    permission_level: str = "read_only"  # "read_only" | "write" | "high_impact"
    default_timeout: float = 5.0
    output_max_bytes: int = 10000

    APPROVED_ROOTS: List[str] = [
        r"C:\Users\USER\kenny",
        r"C:\Users\USER",
    ]

    @classmethod
    def validate_path_in_approved_roots(cls, target_path: str) -> str:
        """
        Resolve path and verify it lies strictly within approved roots to prevent path traversal.
        """
        resolved = os.path.abspath(target_path)
        for root in cls.APPROVED_ROOTS:
            resolved_root = os.path.abspath(root)
            if resolved == resolved_root or resolved.startswith(resolved_root + os.sep):
                return resolved
        raise ActionError(f"Access denied: path '{target_path}' is outside approved roots.")

    def run(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute action with provided arguments and return structured result dictionary."""
        t0 = time.monotonic()
        try:
            result_data = self.execute(arguments)
            duration = round(time.monotonic() - t0, 3)
            return {
                "action": self.name,
                "status": "success",
                "duration_sec": duration,
                "data": result_data,
                "error": None,
            }
        except ActionError as e:
            duration = round(time.monotonic() - t0, 3)
            return {
                "action": self.name,
                "status": "error",
                "duration_sec": duration,
                "data": None,
                "error": str(e),
            }
        except Exception as exc:
            duration = round(time.monotonic() - t0, 3)
            return {
                "action": self.name,
                "status": "error",
                "duration_sec": duration,
                "data": None,
                "error": f"Unexpected execution error: {exc}",
            }

    def execute(self, arguments: Dict[str, Any]) -> Any:
        raise NotImplementedError("Subclasses must implement execute()")
