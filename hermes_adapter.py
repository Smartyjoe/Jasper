import subprocess
import sys


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

    def ask(self, query: str) -> str:
        """
        Send `query` to Hermes and return the plain-text answer.
        Raises HermesError on failure.
        
        Args:
            query: The text to send to Hermes.
            
        Returns:
            Plain-text answer from Hermes.
        """
        try:
            # Run Hermes as a subprocess with stdin input
            result = subprocess.run(
                ["hermes", "chat", "--query-file", "-", "-Q", "--source", "tool"],
                input=query,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=self.timeout,
            )
            
            # Check return code
            if result.returncode != 0:
                # Log error and raise exception
                error_msg = result.stderr.strip() if result.stderr else "Unknown error"
                print(f"[ERROR] Hermes exited with code {result.returncode}: {error_msg}", file=sys.stderr)
                raise HermesError(f"Hermes exited with code {result.returncode}: {error_msg}")
            
            # Log warning if there's stderr but non-zero exit code
            if result.stderr and result.returncode == 0:
                print(f"[WARN] Hermes stderr: {result.stderr}", file=sys.stderr)
            
            # Parse stdout: split into lines, drop session_id line and any leading blank lines
            lines = result.stdout.split('\n')
            # Find first non-empty line after session_id
            answer_lines = []
            found_content = False
            for line in lines:
                if line.strip() == "":
                    continue  # Skip empty lines
                if line.startswith("session_id:"):
                    continue  # Skip session_id line
                found_content = True
                answer_lines.append(line)
            
            # Join remaining lines to form complete answer
            answer = "\n".join(answer_lines)
            if not answer:
                raise HermesError("Hermes returned empty response")
                
            return answer
            
        except subprocess.TimeoutExpired:
            print(f"[WARN] Hermes timed out after {self.timeout}s", file=sys.stderr)
            raise HermesError("Hermes timed out")
        except FileNotFoundError:
            print("[ERROR] hermes binary not found on PATH", file=sys.stderr)
            raise HermesError("hermes not found")
        except OSError as e:
            print(f"[ERROR] OS error when running Hermes: {e}", file=sys.stderr)
            raise HermesError(f"OS error: {e}")
        except Exception as exc:
            print(f"[ERROR] Unexpected Hermes error: {exc}", file=sys.stderr)
            raise HermesError(f"Unexpected error: {exc}")