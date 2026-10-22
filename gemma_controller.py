import json
import re
import sys
import time
import urllib.request
from typing import Any, Dict, List, Optional


class GemmaController:
    """Ollama Gemma 2 2B controller for intent classification, local action routing, and short answers."""

    OLLAMA_ENDPOINT = "http://localhost:11434"
    MODEL_NAME = "gemma-2-2b:latest"
    TIMEOUT = 10.0  # Seconds

    def __init__(self, endpoint: Optional[str] = None, model_name: Optional[str] = None):
        self.endpoint = endpoint or self.OLLAMA_ENDPOINT
        self.model_name = model_name or self.MODEL_NAME

    def warm(self) -> bool:
        """Warm up the Gemma 2 2B model in Ollama memory with keep_alive."""
        url = f"{self.endpoint}/api/generate"
        data = json.dumps({
            "model": self.model_name,
            "prompt": "hi",
            "stream": False,
            "keep_alive": "10m",
            "options": {"num_predict": 10, "num_ctx": 1024}
        }).encode("utf-8")

        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        try:
            t0 = time.monotonic()
            with urllib.request.urlopen(req, timeout=12.0) as resp:
                resp.read()
            print(f"[DEBUG] Gemma warm-up completed in {time.monotonic() - t0:.2f}s")
            return True
        except Exception as exc:
            print(f"[WARN] Gemma warm-up failed: {exc}", file=sys.stderr)
            return False

    def fast_path_classify(self, query: str) -> Optional[Dict[str, Any]]:
        """
        Instant deterministic regex / keyword classifier for local actions & common intents.
        Returns routing dict if matched, None otherwise.
        """
        q = query.lower().strip()

        # Greetings & identity
        if re.search(r"\b(who are you|what's your name|what is your name)\b", q):
            return {
                "route": "answer",
                "action": None,
                "answer_text": "I am Jasper, your local AI assistant.",
                "spoken_ack": "I am Jasper, your local AI assistant.",
                "needs_progress": False,
                "final_style": "concise",
            }

        if re.search(r"\b(how are you|how's it going|how do you do)\b", q):
            return {
                "route": "answer",
                "action": None,
                "answer_text": "I'm operating normally and ready to help.",
                "spoken_ack": "I'm operating normally and ready to help.",
                "needs_progress": False,
                "final_style": "concise",
            }

        if re.search(r"\b(what can you do|help me|capabilities)\b", q):
            return {
                "route": "answer",
                "action": None,
                "answer_text": "I can check system status, time, disk space, inspect code, or delegate complex tasks to Hermes and OpenCode.",
                "spoken_ack": "I can check system status, time, disk space, inspect code, or delegate complex tasks to Hermes and OpenCode.",
                "needs_progress": False,
                "final_style": "concise",
            }

        # Time / Date
        if re.search(r"\b(what time|current time|what's the time|tell me the time|what date|today's date|what day is it)\b", q):
            return {
                "route": "local_action",
                "action": "time.now",
                "arguments": {},
                "needs_progress": False,
                "spoken_ack": "Checking the time.",
                "final_style": "concise",
            }

        # System versions
        if re.search(r"\b(version|versions|python version|node version|git version|os version|system version)\b", q):
            return {
                "route": "local_action",
                "action": "system.versions",
                "arguments": {},
                "needs_progress": False,
                "spoken_ack": "Checking system versions.",
                "final_style": "concise",
            }

        # System memory / RAM / top processes
        if re.search(r"\b(memory|ram|free memory|memory usage|ram usage|top processes)\b", q):
            return {
                "route": "local_action",
                "action": "system.memory",
                "arguments": {},
                "needs_progress": False,
                "spoken_ack": "Checking system memory usage.",
                "final_style": "concise",
            }

        # Running processes
        if re.search(r"\b(running processes|process list|list processes|active processes)\b", q):
            return {
                "route": "local_action",
                "action": "system.processes",
                "arguments": {},
                "needs_progress": False,
                "spoken_ack": "Listing active processes.",
                "final_style": "concise",
            }

        # Storage / Disk space
        if re.search(r"\b(disk space|drive space|free space|storage summary|storage space|disk usage)\b", q):
            return {
                "route": "local_action",
                "action": "storage.summary",
                "arguments": {},
                "needs_progress": True,
                "spoken_ack": "Checking disk storage usage.",
                "final_style": "concise",
            }

        # Project inspection
        if re.search(r"\b(inspect project|project files|project config|configuration files)\b", q):
            return {
                "route": "local_action",
                "action": "project.inspect",
                "arguments": {},
                "needs_progress": False,
                "spoken_ack": "Inspecting project configuration.",
                "final_style": "concise",
            }

        # Project search
        m_search = re.search(r"(?:search project for|find in project|search for)\s+['\"]?([^'\"]+)['\"]?", q)
        if m_search:
            search_query = m_search.group(1).strip()
            return {
                "route": "local_action",
                "action": "project.search",
                "arguments": {"query": search_query},
                "needs_progress": True,
                "spoken_ack": f"Searching project files for '{search_query}'.",
                "final_style": "concise",
            }

        # OpenCode coding route
        if re.search(r"\b(refactor|write code|implement feature|fix bug in repo|opencode)\b", q):
            return {
                "route": "opencode",
                "action": None,
                "arguments": {"task": user_query if 'user_query' in locals() else q},
                "needs_progress": True,
                "spoken_ack": "Delegating coding task to OpenCode.",
                "final_style": "concise",
            }

        return None

    @staticmethod
    def _clean_json_str(raw: str) -> str:
        """Strip markdown code blocks or wrapping quotes from raw JSON output."""
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)
        return cleaned.strip()

    def route_request(self, user_query: str, history: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """
        Route user request using fast-path rules first, falling back to Gemma 2 2B LLM routing.
        """
        # Try fast path first
        fast_res = self.fast_path_classify(user_query)
        if fast_res:
            fast_res["classified_by"] = "fast_path"
            return fast_res

        system_prompt = """You are Jasper's fast local intent router.
Route the user request to JSON.
Routes:
1. "answer": simple questions ("Who are you?", "What is 2+2?")
2. "local_action": local deterministic queries (actions: "time.now", "system.versions", "system.memory", "storage.summary", "project.inspect")
3. "hermes": complex tasks, research, web browsing, multi-step actions
4. "opencode": software coding / repo edits

Schema:
{"route": "answer|local_action|hermes|opencode|clarify", "action": null, "arguments": {}, "spoken_ack": "Checking that now.", "answer_text": "Answer text if route is answer", "final_style": "concise"}"""

        prompt = f"User Request: {user_query!r}\nJSON:"

        payload = {
            "model": self.model_name,
            "system": system_prompt,
            "prompt": prompt,
            "stream": False,
            "keep_alive": "10m",
            "options": {
                "temperature": 0.0,
                "num_predict": 64,
                "num_ctx": 1024,
            }
        }

        url = f"{self.endpoint}/api/generate"
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )

        try:
            t0 = time.monotonic()
            with urllib.request.urlopen(req, timeout=self.TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            
            raw_response = data.get("response", "").strip()
            cleaned_json = self._clean_json_str(raw_response)
            parsed = json.loads(cleaned_json)
            parsed["classified_by"] = "gemma_2b"
            parsed["llm_latency_sec"] = round(time.monotonic() - t0, 3)
            return parsed

        except Exception as exc:
            print(f"[WARN] Gemma LLM routing failed or timed out ({exc}). Falling back to Hermes.", file=sys.stderr)
            return {
                "route": "hermes",
                "action": None,
                "arguments": {},
                "needs_progress": True,
                "spoken_ack": "Sending your request to Hermes.",
                "final_style": "concise",
                "classified_by": "fallback_hermes",
            }
