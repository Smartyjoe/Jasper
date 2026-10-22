import os
import re
from typing import Any, Dict, List
from local_actions.base import Action, ActionError


class ProjectInspectAction(Action):
    name = "project.inspect"
    description = "Inspect standard project config files (package.json, pyproject.toml, requirements.txt, v1-plan.md, etc.)"

    KNOWN_CONFIGS = [
        "package.json",
        "pyproject.toml",
        "requirements.txt",
        "v1-plan.md",
        "jasper-v1-2.md",
        "AGENTS.md",
        "README.md",
    ]

    def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        project_root = arguments.get("root", r"C:\Users\USER\kenny")
        valid_root = self.validate_path_in_approved_roots(project_root)

        found_configs = []
        for cfg in self.KNOWN_CONFIGS:
            full_p = os.path.join(valid_root, cfg)
            if os.path.isfile(full_p):
                try:
                    sz_bytes = os.path.getsize(full_p)
                    found_configs.append({
                        "file": cfg,
                        "size_bytes": sz_bytes,
                        "path": full_p,
                    })
                except Exception:
                    continue

        return {
            "project_root": valid_root,
            "config_files_found": found_configs,
        }


class ProjectSearchAction(Action):
    name = "project.search"
    description = "Search text references or regex in approved project files"

    def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        query = arguments.get("query")
        if not query:
            raise ActionError("Argument 'query' is required for project.search")

        project_root = arguments.get("root", r"C:\Users\USER\kenny")
        valid_root = self.validate_path_in_approved_roots(project_root)

        ext_filter = arguments.get("extensions", [".py", ".md", ".json", ".toml", ".txt"])
        max_matches = int(arguments.get("limit", 15))

        matches = []
        pattern = re.compile(re.escape(query), re.IGNORECASE)

        for root, dirs, files in os.walk(valid_root):
            # Skip virtualenv and git directories
            dirs[:] = [d for d in dirs if d not in (".git", ".venv-stt", "venv", "__pycache__", "node_modules")]

            for fname in files:
                ext = os.path.splitext(fname)[1].lower()
                if ext in ext_filter:
                    full_p = os.path.join(root, fname)
                    rel_p = os.path.relpath(full_p, valid_root)
                    try:
                        with open(full_p, "r", encoding="utf-8", errors="ignore") as f:
                            for idx, line in enumerate(f, 1):
                                if pattern.search(line):
                                    matches.append({
                                        "file": rel_p,
                                        "line_number": idx,
                                        "line_content": line.strip()[:150],
                                    })
                                    if len(matches) >= max_matches:
                                        break
                    except Exception:
                        continue
                if len(matches) >= max_matches:
                    break
            if len(matches) >= max_matches:
                break

        return {
            "project_root": valid_root,
            "query": query,
            "match_count": len(matches),
            "matches": matches,
        }
