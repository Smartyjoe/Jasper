import datetime
import os
import platform
import subprocess
import sys
from typing import Any, Dict
from local_actions.base import Action, ActionError


class TimeNowAction(Action):
    name = "time.now"
    description = "Get current time and date"

    def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        now = datetime.datetime.now()
        return {
            "current_time": now.strftime("%I:%M:%S %p"),
            "current_date": now.strftime("%A, %B %d, %Y"),
            "iso": now.isoformat(),
        }


class SystemVersionsAction(Action):
    name = "system.versions"
    description = "Get installed versions of Python, Node, Git, Hermes, OpenCode, and OS"

    def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        versions = {
            "python": sys.version.split()[0],
            "os": f"{platform.system()} {platform.release()} ({platform.version()})",
        }

        # Check Node version
        try:
            res = subprocess.run(["node", "--version"], capture_output=True, text=True, timeout=3)
            if res.returncode == 0:
                versions["node"] = res.stdout.strip()
        except Exception:
            versions["node"] = "Not found"

        # Check Git version
        try:
            res = subprocess.run(["git", "--version"], capture_output=True, text=True, timeout=3)
            if res.returncode == 0:
                versions["git"] = res.stdout.strip()
        except Exception:
            versions["git"] = "Not found"

        # Check Hermes version
        try:
            res = subprocess.run(["hermes", "--version"], capture_output=True, text=True, timeout=3)
            if res.returncode == 0:
                versions["hermes"] = res.stdout.strip().split("\n")[0]
        except Exception:
            versions["hermes"] = "Not found"

        # Check OpenCode version
        try:
            res = subprocess.run(["opencode", "--version"], capture_output=True, text=True, timeout=3)
            if res.returncode == 0:
                versions["opencode"] = res.stdout.strip()
        except Exception:
            versions["opencode"] = "Not found"

        return versions


class SystemMemoryAction(Action):
    name = "system.memory"
    description = "Get system memory (RAM) usage and top memory-consuming processes"

    def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        # Use PowerShell to query memory and processes on Windows cleanly
        ps_script = """
        $os = Get-CimInstance Win32_OperatingSystem
        $total = [math]::Round($os.TotalVisibleMemorySize / 1MB, 2)
        $free = [math]::Round($os.FreePhysicalMemory / 1MB, 2)
        $used = [math]::Round($total - $free, 2)
        $percent = [math]::Round(($used / $total) * 100, 1)

        $top = Get-Process | Sort-WorkingSet64 -Descending | Select-Object -First 5 | ForEach-Object {
            @{
                name = $_.ProcessName
                pid = $_.Id
                working_set_mb = [math]::Round($_.WorkingSet64 / 1MB, 1)
            }
        }

        @{
            total_gb = $total
            free_gb = $free
            used_gb = $used
            used_percent = $percent
            top_processes = $top
        } | ConvertTo-Json -Depth 3
        """

        try:
            res = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_script],
                capture_output=True, text=True, timeout=5
            )
            if res.returncode == 0 and res.stdout.strip():
                import json
                return json.loads(res.stdout.strip())
            else:
                raise ActionError(f"PowerShell query failed: {res.stderr}")
        except Exception as e:
            raise ActionError(f"Failed to query system memory: {e}")


class SystemProcessesAction(Action):
    name = "system.processes"
    description = "List active processes or search processes by filter name"

    def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        filter_name = str(arguments.get("filter", "")).strip().lower()
        limit = int(arguments.get("limit", 10))

        ps_script = f"""
        $procs = Get-Process | Select-Object Name, Id, CPU, WorkingSet64
        if ("{filter_name}") {{
            $procs = $procs | Where-Object {{ $_.Name -like "*{filter_name}*" }}
        }}
        $res = $procs | Select-Object -First {limit} | ForEach-Object {{
            @{{
                name = $_.Name
                pid = $_.Id
                cpu_sec = [math]::Round($_.CPU, 1)
                mem_mb = [math]::Round($_.WorkingSet64 / 1MB, 1)
            }}
        }}
        $res | ConvertTo-Json -Depth 2
        """

        try:
            res = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_script],
                capture_output=True, text=True, timeout=5
            )
            if res.returncode == 0:
                import json
                out = res.stdout.strip()
                parsed = json.loads(out) if out else []
                if isinstance(parsed, dict):
                    parsed = [parsed]
                return {"filter": filter_name, "count": len(parsed), "processes": parsed}
            else:
                raise ActionError(f"Process query failed: {res.stderr}")
        except Exception as e:
            raise ActionError(f"Failed to query processes: {e}")
