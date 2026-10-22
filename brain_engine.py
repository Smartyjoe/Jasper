"""
brain_engine.py — Multi-Provider Brain Engine for Jasper V2

Supports three configurable providers selected via .env:
  1. gemini     — Google Gemini API via official SDK with Automatic Function Calling (tools)
  2. openrouter — OpenRouter API (supports openrouter/free, meta-llama, deepseek, etc.)
  3. ollama     — Local Ollama instance (supports qwen, llama, mistral, gemma, etc.)

Configuration (.env):
  PRIMARY_PROVIDER="gemini" | "openrouter" | "ollama"
  PRIMARY_MODEL="<model-identifier>"
  GEMINI_API_KEY="<key>"
  OPENROUTER_API_KEY="<key>"
  OLLAMA_BASE_URL="http://localhost:11434"
"""

import abc
import json
import os
import sys
import time
import traceback
from typing import Any, Callable, Dict, List, Optional

import httpx

try:
    from google import genai
    from google.genai import errors as genai_errors
    from google.genai import types as genai_types
    _GENAI_AVAILABLE = True
except ImportError:
    genai = None
    genai_errors = None
    genai_types = None
    _GENAI_AVAILABLE = False


class BaseBrain(abc.ABC):
    """Abstract base class for Jasper brain backends."""

    def __init__(self, provider_name: str, model_name: str, system_prompt: str, tools: Optional[List[Any]] = None):
        self.provider_name = provider_name
        self.model_name = model_name
        self.system_prompt = system_prompt
        self.tools = tools or []
        self._history: List[Dict[str, str]] = []

    @abc.abstractmethod
    def generate_response(self, user_text: str) -> str:
        """Process user text and return Jasper's final spoken answer."""
        pass

    def seed_history(self, turns: List[Dict[str, str]]) -> None:
        """Seed conversation history with recent turns."""
        self._history = [t for t in turns if t.get("user") and t.get("response")]

    def reset_history(self) -> None:
        """Clear session conversation history."""
        self._history.clear()


# =============================================================================
# 1. GEMINI BRAIN PROVIDER
# =============================================================================

class GeminiBrain(BaseBrain):
    """Google Gemini Brain using the google-genai SDK with function calling."""

    def __init__(
        self,
        model_name: str,
        system_prompt: str,
        tools: Optional[List[Any]] = None,
        api_key: Optional[str] = None,
        timeout_ms: int = 12000,
        fallback_model: str = "gemini-3.8-flash",
    ):
        super().__init__("gemini", model_name, system_prompt, tools)
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY", "")
        self.timeout_ms = max(10000, timeout_ms)  # Gemini enforces >= 10s deadline
        self.fallback_model = fallback_model
        self.models_to_try = [self.model_name]
        if self.fallback_model and self.fallback_model != self.model_name:
            self.models_to_try.append(self.fallback_model)

        self._client: Optional[genai.Client] = None
        self._sessions: Dict[str, Any] = {}
        self._init_client()

    def _init_client(self) -> None:
        if not _GENAI_AVAILABLE:
            raise RuntimeError("google-genai package is not installed.")
        if not self.api_key:
            print("[WARN] [Gemini] GEMINI_API_KEY not found in environment.", file=sys.stderr)
            return

        self._client = genai.Client(
            api_key=self.api_key,
            http_options=genai_types.HttpOptions(timeout=self.timeout_ms),
        )
        self._sessions.clear()

    def _get_config(self) -> Any:
        return genai_types.GenerateContentConfig(
            system_instruction=self.system_prompt,
            tools=self.tools,
            temperature=0.7,
            max_output_tokens=1024,
        )

    def _get_session(self, model: str):
        if not self._client:
            self._init_client()
        if not self._client:
            raise RuntimeError("Gemini client not initialized (missing API key).")

        if model not in self._sessions:
            history_contents = []
            for item in self._history[-6:]:
                u = item.get("user", "").strip()
                r = item.get("response", "").strip()
                if u and r:
                    history_contents.append(
                        genai_types.Content(role="user", parts=[genai_types.Part(text=u)])
                    )
                    history_contents.append(
                        genai_types.Content(role="model", parts=[genai_types.Part(text=r)])
                    )

            if history_contents:
                self._sessions[model] = self._client.chats.create(
                    model=model, history=history_contents, config=self._get_config()
                )
            else:
                self._sessions[model] = self._client.chats.create(
                    model=model, config=self._get_config()
                )
        return self._sessions[model]

    def generate_response(self, user_text: str) -> str:
        for model in self.models_to_try:
            for attempt in range(2):
                try:
                    session = self._get_session(model)
                    response = session.send_message(user_text)
                    if response and response.text:
                        answer = response.text.strip()
                        self._history.append({"user": user_text, "response": answer})
                        return answer
                    return "I wasn't able to produce an answer for that, sir."

                except genai_errors.ServerError as s_err:
                    if "503" in str(s_err) and attempt == 0:
                        print(f"[WARN] [Gemini] 503 overload on {model}. Retrying once immediately.", file=sys.stderr)
                        continue
                    print(f"[ERROR] [Gemini] ServerError on {model}: {s_err}", file=sys.stderr)
                    break

                except Exception as exc:
                    print(f"[ERROR] [Gemini] Call failed on {model}: {exc}", file=sys.stderr)
                    traceback.print_exc(file=sys.stderr)
                    self._sessions.pop(model, None)
                    break

        return "I'm seeing heavy cloud traffic on Gemini right now. Please try again in a moment."


# =============================================================================
# 2. OPENROUTER BRAIN PROVIDER
# =============================================================================

class OpenRouterBrain(BaseBrain):
    """OpenRouter Brain supporting free and commercial models via OpenAI-compatible REST API."""

    OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

    def __init__(
        self,
        model_name: str,
        system_prompt: str,
        api_key: Optional[str] = None,
        timeout_seconds: float = 40.0,
    ):
        super().__init__("openrouter", model_name, system_prompt, [])
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
        self.timeout = timeout_seconds
        self.client = httpx.Client(timeout=self.timeout)

    def generate_response(self, user_text: str) -> str:
        if not self.api_key:
            err_msg = (
                "OpenRouter API key is missing. Please set OPENROUTER_API_KEY in your .env file "
                "to use OpenRouter models."
            )
            print(f"[ERROR] [OpenRouter] {err_msg}", file=sys.stderr)
            return "My OpenRouter API key is missing from the configuration, sir. Please check your settings."

        messages = [{"role": "system", "content": self.system_prompt}]
        for item in self._history[-6:]:
            u = item.get("user", "").strip()
            r = item.get("response", "").strip()
            if u and r:
                messages.append({"role": "user", "content": u})
                messages.append({"role": "assistant", "content": r})

        messages.append({"role": "user", "content": user_text})

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": "https://github.com/Smartyjoe/Jasper",
            "X-Title": "Jasper Windows Voice Assistant",
            "Content-Type": "application/json",
        }

        payload = {
            "model": self.model_name,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 1024,
        }

        for attempt in range(2):
            try:
                resp = self.client.post(self.OPENROUTER_URL, headers=headers, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    choices = data.get("choices", [])
                    if choices and "message" in choices[0]:
                        answer = choices[0]["message"].get("content", "").strip()
                        if answer:
                            self._history.append({"user": user_text, "response": answer})
                            return answer
                    return "I received an empty response from OpenRouter, sir."

                if resp.status_code in (429, 502, 503, 504) and attempt == 0:
                    print(f"[WARN] [OpenRouter] Status {resp.status_code}. Retrying once...", file=sys.stderr)
                    time.sleep(1.0)
                    continue

                error_detail = resp.text[:300]
                print(f"[ERROR] [OpenRouter] HTTP {resp.status_code} on {self.model_name}: {error_detail}", file=sys.stderr)
                return f"OpenRouter returned error code {resp.status_code}. Please verify model availability."

            except httpx.TimeoutException:
                print(f"[ERROR] [OpenRouter] Request timed out ({self.timeout}s) on {self.model_name}", file=sys.stderr)
                if attempt == 0:
                    continue
                return "The OpenRouter model took too long to reply. Please try again."

            except Exception as exc:
                print(f"[ERROR] [OpenRouter] Unexpected error: {exc}", file=sys.stderr)
                traceback.print_exc(file=sys.stderr)
                return "I encountered a network issue communicating with OpenRouter."

        return "I was unable to get a response from OpenRouter. Please try again."


# =============================================================================
# 3. OLLAMA BRAIN PROVIDER
# =============================================================================

class OllamaBrain(BaseBrain):
    """Local Ollama Brain using Ollama's native API."""

    def __init__(
        self,
        model_name: str,
        system_prompt: str,
        base_url: Optional[str] = None,
        timeout_seconds: float = 90.0,
    ):
        super().__init__("ollama", model_name, system_prompt, [])
        self.base_url = (base_url or os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")).rstrip("/")
        self.timeout = timeout_seconds
        self.client = httpx.Client(timeout=self.timeout)
        self._verify_connection()

    def _verify_connection(self) -> bool:
        try:
            r = self.client.get(f"{self.base_url}/api/tags", timeout=3.0)
            if r.status_code == 200:
                tags = r.json().get("models", [])
                names = [m.get("name") for m in tags if isinstance(m, dict)]
                if self.model_name not in names and f"{self.model_name}:latest" not in names:
                    print(
                        f"[WARN] [Ollama] Model '{self.model_name}' not found in local Ollama list {names}. "
                        f"Ollama will attempt to pull it or fail if unavailable.",
                        file=sys.stderr,
                    )
                return True
        except Exception as exc:
            print(
                f"[WARN] [Ollama] Could not reach Ollama at {self.base_url}: {exc}. "
                "Ensure 'ollama serve' is running.",
                file=sys.stderr,
            )
        return False

    def generate_response(self, user_text: str) -> str:
        messages = [{"role": "system", "content": self.system_prompt}]
        for item in self._history[-6:]:
            u = item.get("user", "").strip()
            r = item.get("response", "").strip()
            if u and r:
                messages.append({"role": "user", "content": u})
                messages.append({"role": "assistant", "content": r})

        messages.append({"role": "user", "content": user_text})

        payload = {
            "model": self.model_name,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": 0.7,
            },
        }

        try:
            endpoint = f"{self.base_url}/api/chat"
            resp = self.client.post(endpoint, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                msg = data.get("message", {})
                answer = msg.get("content", "").strip()
                if answer:
                    self._history.append({"user": user_text, "response": answer})
                    return answer
                return "Local Ollama produced an empty response, sir."

            print(f"[ERROR] [Ollama] HTTP {resp.status_code}: {resp.text[:300]}", file=sys.stderr)
            return f"Ollama returned error {resp.status_code}. Is model '{self.model_name}' downloaded?"

        except httpx.ConnectError:
            print(f"[ERROR] [Ollama] Connection to {self.base_url} refused. Is Ollama running?", file=sys.stderr)
            return "I cannot connect to your local Ollama server. Please verify that Ollama is running."

        except httpx.TimeoutException:
            print(f"[ERROR] [Ollama] Generation timed out after {self.timeout}s on {self.model_name}", file=sys.stderr)
            return "Local model generation timed out. Your system may be under heavy load."

        except Exception as exc:
            print(f"[ERROR] [Ollama] Unexpected error: {exc}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            return "I encountered an error running the local model."


# =============================================================================
# FACTORY FUNCTION
# =============================================================================

DEFAULT_MODELS = {
    "gemini": "gemini-3.5-flash-lite",
    "openrouter": "openrouter/free",
    "ollama": "qwen3-0.6b:latest",
}


def get_brain_engine(
    system_prompt: str,
    tools: Optional[List[Any]] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    memory_store = None,
) -> BaseBrain:
    """
    Factory to construct the configured Brain engine.

    Reads configuration from .env:
      - PRIMARY_PROVIDER: 'gemini' | 'openrouter' | 'ollama'
      - PRIMARY_MODEL: model name for the selected provider
    """
    prov = (provider or os.environ.get("PRIMARY_PROVIDER") or os.environ.get("BRAIN_PROVIDER") or "gemini").lower().strip()
    mod = (model or os.environ.get("PRIMARY_MODEL") or os.environ.get("BRAIN_MODEL") or "").strip()

    if not mod:
        mod = DEFAULT_MODELS.get(prov, "gemini-3.5-flash-lite")

    print(f"[INIT] Brain Provider: '{prov}' | Primary Model: '{mod}'")

    if prov == "openrouter":
        brain = OpenRouterBrain(
            model_name=mod,
            system_prompt=system_prompt,
            api_key=os.environ.get("OPENROUTER_API_KEY", ""),
        )
    elif prov == "ollama":
        brain = OllamaBrain(
            model_name=mod,
            system_prompt=system_prompt,
            base_url=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
        )
    elif prov == "gemini":
        brain = GeminiBrain(
            model_name=mod,
            system_prompt=system_prompt,
            tools=tools or [],
            fallback_model=os.environ.get("GEMINI_FALLBACK_MODEL", "gemini-3.8-flash"),
        )
    else:
        print(f"[WARN] Unknown brain provider '{prov}', falling back to 'gemini'.", file=sys.stderr)
        brain = GeminiBrain(
            model_name=mod or "gemini-3.5-flash-lite",
            system_prompt=system_prompt,
            tools=tools or [],
        )

    # Seed history from memory store if available
    if memory_store:
        try:
            turns = []
            for t in memory_store.get_recent_turns(6):
                u = (t.get("raw_transcript") or "").strip()
                r = (t.get("response") or "").strip()
                if u and r:
                    turns.append({"user": u, "response": r})
            if turns:
                brain.seed_history(turns)
        except Exception as exc:
            print(f"[WARN] [BrainEngine] History seeding failed: {exc}", file=sys.stderr)

    return brain
