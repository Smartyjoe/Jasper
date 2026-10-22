import queue
import sys
import threading
import time
from typing import Any, Dict, Optional, Tuple
from kokoro_voice import KokoroVoice, KokoroVoiceError
from timing_tracker import RequestMetrics


class ResponseCoordinator:
    """Coordinates immediate acknowledgements, progress updates, and final responses with async TTS and UI state notifications."""

    PROGRESS_THRESHOLD = 4.0  # Seconds before emitting progress update

    def __init__(self, kokoro_voice: Optional[KokoroVoice] = None, ui_queue: Optional[queue.Queue] = None):
        self.voice = kokoro_voice or KokoroVoice()
        self.ui_queue = ui_queue
        self.speech_queue = queue.Queue()
        self._stop_event = threading.Event()
        self.worker_thread = threading.Thread(target=self._speech_worker, daemon=True)
        self.worker_thread.start()

    def _speech_worker(self) -> None:
        """Background worker consuming text items, synthesizing, and playing audio asynchronously."""
        while not self._stop_event.is_set():
            try:
                item = self.speech_queue.get(timeout=0.2)
            except queue.Empty:
                continue

            if item is None:
                break

            text, msg_type, metrics = item

            try:
                if metrics:
                    metrics.mark("tts_gen_start")

                wav_path, gen_time = self.voice.synthesize(text)

                if metrics:
                    metrics.mark("tts_gen_end")
                    metrics.record_duration(f"tts_synthesis_{msg_type}", gen_time)
                    metrics.mark("playback_start")

                # Notify UI of speaking state
                if self.ui_queue:
                    self.ui_queue.put(("speaking", text[:32]))

                # Play audio
                play_time = self.voice.play_wav(wav_path)

                if metrics:
                    metrics.mark("playback_end")
                    metrics.record_duration(f"tts_playback_{msg_type}", play_time)

                # Return UI to idle or current status
                if self.ui_queue and self.speech_queue.empty():
                    self.ui_queue.put(("idle", "Say 'Hey Jasper'"))

            except KokoroVoiceError as kve:
                print(f"[WARN] ResponseCoordinator TTS error ({msg_type}): {kve}", file=sys.stderr)
            except Exception as exc:
                print(f"[ERROR] Unexpected ResponseCoordinator error ({msg_type}): {exc}", file=sys.stderr)
            finally:
                self.speech_queue.task_done()

    def speak_ack(self, ack_text: str, metrics: Optional[RequestMetrics] = None) -> None:
        """Enqueue immediate short acknowledgement speech."""
        if ack_text:
            self.speech_queue.put((ack_text, "ack", metrics))

    def speak_progress(self, progress_text: str, metrics: Optional[RequestMetrics] = None) -> None:
        """Enqueue progress update speech."""
        if progress_text:
            self.speech_queue.put((progress_text, "progress", metrics))

    def speak_final(self, final_text: str, metrics: Optional[RequestMetrics] = None) -> None:
        """Enqueue final response speech."""
        if final_text:
            self.speech_queue.put((final_text, "final", metrics))

    def cancel_pending(self) -> None:
        """Clear pending queued speech and stop ongoing playback immediately."""
        try:
            while not self.speech_queue.empty():
                try:
                    self.speech_queue.get_nowait()
                    self.speech_queue.task_done()
                except queue.Empty:
                    break
            self.voice.stop_playback()
            if self.ui_queue:
                self.ui_queue.put(("idle", "Say 'Hey Jasper'"))
        except Exception as exc:
            print(f"[WARN] Failed to cancel pending speech: {exc}", file=sys.stderr)

    def format_action_result(self, action_name: str, result_data: Dict[str, Any]) -> str:
        """Format raw local action data into concise, natural spoken text."""
        if not result_data:
            return "The action completed with no return data."

        if action_name == "time.now":
            t = result_data.get("current_time", "")
            d = result_data.get("current_date", "")
            return f"It is {t} on {d}."

        if action_name == "system.versions":
            py = result_data.get("python", "unknown")
            node = result_data.get("node", "unknown")
            git = result_data.get("git", "unknown")
            return f"Python is version {py}. Node is {node}. Git is {git}."

        if action_name == "system.memory":
            free = result_data.get("free_gb", "unknown")
            used = result_data.get("used_percent", "unknown")
            return f"System memory is {used} percent used with {free} gigabytes free."

        if action_name == "storage.summary":
            free = result_data.get("drive_free_gb", "unknown")
            total = result_data.get("drive_total_gb", "unknown")
            return f"Drive has {free} gigabytes free out of {total} gigabytes total."

        if action_name == "project.inspect":
            cfgs = result_data.get("config_files_found", [])
            names = [c["file"] for c in cfgs]
            if names:
                return f"Found project configuration files: {', '.join(names)}."
            return "No standard project configuration files found."

        if action_name == "project.search":
            count = result_data.get("match_count", 0)
            q = result_data.get("query", "")
            return f"Found {count} references for '{q}' in project files."

        return f"Action {action_name} executed successfully."
