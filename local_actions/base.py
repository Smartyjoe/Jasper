import os
import time
from typing import Any, Dict, List, Optional


class ActionError(Exception):
    """Base exception for action execution errors."""
    pass


# ---------------------------------------------------------------------------
# Secret-file deny list
# ---------------------------------------------------------------------------
# The model can call read_file / filesystem.search. Without this guard a
# request like "read C:\Users\USER\kenny\.env" would hand the live API key to
# the language model, which would then read it out loud through TTS.
SECRET_FILE_NAMES = frozenset({
    ".env", ".env.local", ".env.development", ".env.production", ".env.test",
    "credentials", "credentials.json", "secrets.json", "secret.json",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "known_hosts",
    "authorized_keys", "shadow", "master.key",
})

SECRET_EXTENSIONS = frozenset({
    ".pem", ".key", ".pfx", ".p12", ".keystore", ".kdbx", ".ppk",
    ".jks", ".der", ".crt",
})

SECRET_DIRECTORIES = frozenset({".ssh", ".aws", ".gnupg", ".kube", ".docker", ".azure"})

# Extensions that are commonly secrets even without a secret-looking name.
SECRET_NAME_MARKERS = ("secret", "credential", "password", "passwd", "apikey", "api_key")


def _is_secret_path(path: str) -> bool:
    """Return True when the path points at a credential-bearing file."""
    parts = [p.lower() for p in os.path.abspath(path).split(os.sep) if p]
    if not parts:
        return False

    for part in parts[:-1]:
        if part in SECRET_DIRECTORIES:
            return True

    name = parts[-1]
    if name in SECRET_FILE_NAMES:
        return True
    ext = os.path.splitext(name)[1]
    if ext in SECRET_EXTENSIONS:
        return True
    stem = os.path.splitext(name)[0]
    if any(marker in stem for marker in SECRET_NAME_MARKERS):
        return True
    return False


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
        Credential files are rejected outright.
        """
        resolved = os.path.abspath(target_path)
        for root in cls.APPROVED_ROOTS:
            resolved_root = os.path.abspath(root)
            if resolved == resolved_root or resolved.startswith(resolved_root + os.sep):
                if _is_secret_path(resolved):
                    raise ActionError(
                        "Access denied: refusing to read a credential file. "
                        "Secrets are not exposed to the assistant."
                    )
                return resolved
        raise ActionError(f"Access denied: path '{target_path}' is outside approved roots.")

    @classmethod
    def is_secret_path(cls, target_path: str) -> bool:
        """Public helper so callers can pre-check a path."""
        return _is_secret_path(target_path)


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
