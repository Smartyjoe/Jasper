import fnmatch
import os
import shutil
import subprocess
from typing import Any, Dict, List
from local_actions.base import Action, ActionError


class StorageSummaryAction(Action):
    name = "storage.summary"
    description = "Get drive disk space usage and largest directories/files in approved roots"

    def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        target_root = arguments.get("root", r"C:\Users\USER\kenny")
        valid_root = self.validate_path_in_approved_roots(target_root)

        # Drive total/free
        total, used, free = shutil.disk_usage(valid_root)

        # Largest items in root
        items = []
        try:
            for entry in os.scandir(valid_root):
                try:
                    stat = entry.stat(follow_symlinks=False)
                    size_mb = round(stat.st_size / (1024 * 1024), 2)
                    is_dir = entry.is_dir(follow_symlinks=False)
                    items.append({
                        "name": entry.name,
                        "type": "directory" if is_dir else "file",
                        "size_mb": size_mb,
                    })
                except Exception:
                    continue
        except Exception as e:
            raise ActionError(f"Failed to scan directory '{valid_root}': {e}")

        items.sort(key=lambda x: x["size_mb"], reverse=True)

        return {
            "root_path": valid_root,
            "drive_total_gb": round(total / (1024**3), 2),
            "drive_used_gb": round(used / (1024**3), 2),
            "drive_free_gb": round(free / (1024**3), 2),
            "largest_items": items[:10],
        }


class FilesystemSearchAction(Action):
    name = "filesystem.search"
    description = "Search files/directories matching name/pattern in approved roots"

    def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        pattern = arguments.get("pattern", "*")
        search_root = arguments.get("root", r"C:\Users\USER\kenny")
        max_depth = int(arguments.get("max_depth", 3))
        limit = int(arguments.get("limit", 20))

        valid_root = self.validate_path_in_approved_roots(search_root)
        results = []

        base_depth = valid_root.rstrip(os.sep).count(os.sep)

        for root, dirs, files in os.walk(valid_root):
            cur_depth = root.rstrip(os.sep).count(os.sep) - base_depth
            if cur_depth > max_depth:
                dirs.clear()
                continue

            for name in files + dirs:
                if fnmatch.fnmatch(name.lower(), pattern.lower()):
                    full_p = os.path.join(root, name)
                    rel_p = os.path.relpath(full_p, valid_root)
                    is_dir = os.path.isdir(full_p)
                    try:
                        sz_kb = round(os.path.getsize(full_p) / 1024, 1) if not is_dir else 0
                    except Exception:
                        sz_kb = 0

                    results.append({
                        "name": name,
                        "relative_path": rel_p,
                        "type": "directory" if is_dir else "file",
                        "size_kb": sz_kb,
                    })
                    if len(results) >= limit:
                        break
            if len(results) >= limit:
                break

        return {
            "search_root": valid_root,
            "pattern": pattern,
            "match_count": len(results),
            "matches": results,
        }


class FilesystemReadTextAction(Action):
    name = "filesystem.read_text"
    description = "Read text content from file in approved roots with line/byte limits"

    def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        file_path = arguments.get("path")
        if not file_path:
            raise ActionError("Argument 'path' is required for filesystem.read_text")

        valid_path = self.validate_path_in_approved_roots(file_path)
        if not os.path.isfile(valid_path):
            raise ActionError(f"Path '{valid_path}' is not a file.")

        max_lines = int(arguments.get("max_lines", 50))
        max_bytes = int(arguments.get("max_bytes", 10000))

        lines = []
        bytes_read = 0

        try:
            with open(valid_path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    lines.append(line)
                    bytes_read += len(line.encode("utf-8"))
                    if len(lines) >= max_lines or bytes_read >= max_bytes:
                        break
        except Exception as e:
            raise ActionError(f"Failed to read file '{valid_path}': {e}")

        return {
            "path": valid_path,
            "line_count": len(lines),
            "total_bytes": bytes_read,
            "truncated": len(lines) >= max_lines or bytes_read >= max_bytes,
            "content": "".join(lines),
        }
