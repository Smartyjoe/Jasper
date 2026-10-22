"""
stt_engine.py - Gemini Live transcription for Jasper
====================================================
Model : gemini-3.5-transcribe-live (dedicated speech-to-text, NOT the translate model)

What this version does differently from the previous one
-------------------------------------------------------
* Reads `input_transcription` (what YOU said) and `interim_input_transcription`
  (live partials) instead of the translated `output_transcription`.
* Uses the server's finalized transcripts instead of a text-silence timer.
  A short `merge_window` joins finals that belong to one thought.
* `custom_vocabulary` biases recognition toward "Jasper", "Hermes", "OpenCode".
* The microphone is opened once in its own thread and survives reconnects.
* Sessions are limited to ~10 minutes by the API, so this refreshes the
  connection proactively (when you are not speaking) and reconnects with
  backoff after any error. It no longer dies silently.
* `close()` really stops everything.

Fixes applied in this revision
-------------------------------
1. The microphone no longer captures audio before the live session is ready.
   Previously everything captured during the ~5-7 s connection window was
   silently thrown away by `_drain_audio()`, so the first thing you said after
   starting Jasper was always lost.
2. `silence_threshold` is no longer ignored. It is now used as a client-side
   safety net: if the server has not finalised a turn but the speaker has gone
   quiet, the best interim hypothesis is promoted to a finished sentence.
3. `target_lang` is still accepted for backwards compatibility but is now
   translated into `language_codes` instead of being silently dropped.
4. Full diagnostics: connection state, microphone level meter, chunks sent,
   interim results, final results, emitted sentences, reconnects and errors
   are all logged with timestamps.

Public API is unchanged:
    stt = STT(...); stt.connect(); text = stt.listen()
    stt.sentence_queue   (queue.Queue of str)
    stt.set_muted(True/False)
    stt.close()

Extra (optional) helpers:
    STT(on_interim=callback)       -> called with each interim hypothesis
    stt.seconds_since_speech()     -> seconds since the last interim text
    stt.wait_until_ready(timeout)  -> block until the live session is up
    stt.listen(timeout=5.0)        -> wait at most N seconds for a sentence

Requirements: pip install -U google-genai pyaudio python-dotenv
"""

import asyncio
import os
import queue
import shutil
import sys
import threading
import time
from collections import deque
from typing import Callable, List, Optional

import pyaudio
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

# -- Audio ---------------------------------------------------------------------
FORMAT = pyaudio.paInt16
CHANNELS = 1
SEND_SAMPLE_RATE = 16000
CHUNK_SIZE = 1600                      # 100 ms @ 16 kHz (docs recommend ~100 ms chunks)
AUDIO_MIME = f"audio/pcm;rate={SEND_SAMPLE_RATE}"

# -- Model ---------------------------------------------------------------------
MODEL = "gemini-3.5-transcribe-live"
DEFAULT_LANGUAGE_CODES = ["en-US"]     # use [] for automatic language detection
DEFAULT_VOCABULARY = ["Jasper", "Hermes", "OpenCode"]

# Live transcription sessions are capped at 10 minutes by the API.
SESSION_SOFT_LIMIT = 540.0             # refresh after 9 min, once you are quiet
SESSION_HARD_LIMIT = 580.0             # refresh no matter what
RECONNECT_MAX_BACKOFF = 15.0

# Client-side speech detection (used for the safety net and the level meter).
# This laptop's microphone has a noise floor around RMS 200, so the speech
# threshold sits well above it - otherwise every frame reads as "SPEECH".
VOICE_THRESHOLD = 700                  # RMS of a 16-bit sample
LEVEL_LOG_INTERVAL = 1.0               # seconds between microphone level reports


class Utterance(str):
    """
    A transcribed sentence that also carries the wall-clock window during which
    the audio for that sentence was captured.

    It subclasses `str`, so it behaves exactly like the plain strings the
    engine used to return (`text.lower()`, `print(text)`, `text in other`, ...).
    The extra attributes let the voice loop decide whether a sentence was
    captured while Jasper was speaking, using *capture* time rather than the
    time the sentence happened to be dequeued.
    """

    __slots__ = ("t0", "t1", "turn_id")

    def __new__(cls, text: str, t0: float = 0.0, t1: float = 0.0, turn_id: int = 0):
        obj = super().__new__(cls, text)
        obj.t0 = t0
        obj.t1 = t1
        obj.turn_id = turn_id
        return obj


class STT:
    def __init__(
        self,
        target_lang: Optional[str] = None,      # e.g. "en" -> language_codes ["en-US"]
        flush: bool = False,
        silence_threshold: float = 1.2,         # seconds of quiet before we finalise
        *,
        language_codes: Optional[List[str]] = None,
        custom_vocabulary: Optional[List[str]] = None,
        merge_window: float = 0.6,
        mode: Optional[str] = None,             # None/"VERBATIM" (default) or "SMART"
        on_interim: Optional[Callable[[str], None]] = None,
        verbose: bool = True,
        debug_preview: bool = False,            # in-place `\r` live preview
    ):
        self.target_lang = target_lang
        self.flush = flush
        self.silence_threshold = silence_threshold
        self.custom_vocabulary = DEFAULT_VOCABULARY if custom_vocabulary is None else custom_vocabulary
        self.merge_window = merge_window
        self.mode = mode
        self.on_interim = on_interim
        self.verbose = verbose
        self.debug_preview = debug_preview

        # `target_lang` is legacy: map it onto a BCP-47 code instead of
        # discarding it, so the caller's intent is honoured.
        if language_codes is not None:
            self.language_codes = language_codes
        elif target_lang:
            self.language_codes = [self._to_bcp47(target_lang)]
        else:
            self.language_codes = list(DEFAULT_LANGUAGE_CODES)

        self.sentence_queue: "queue.Queue[str]" = queue.Queue()

        self._audio_q: "queue.Queue[bytes]" = queue.Queue(maxsize=100)   # ~10 s of audio
        self._muted = threading.Event()
        self._lock = threading.Lock()
        self._pending: List[str] = []
        self._last_final = 0.0
        self._last_interim = 0.0
        self._last_interim_text = ""
        # Capture window of the utterance currently being assembled.
        self._utterance_start = 0.0
        self._utterance_end = 0.0
        self._turn_id = 0

        self._is_running = False
        self._capture_enabled = threading.Event()   # mic only records while the session is up
        self._ready = threading.Event()             # set once the session is usable
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._mic_thread: Optional[threading.Thread] = None

        self._stats = {"chunks": 0, "bytes": 0, "interims": 0, "finals": 0,
                       "sentences": 0, "sessions": 0, "drops": 0}

    # ------------------------------------------------------------------ public

    def connect(self) -> None:
        if self._is_running:
            return
        if not os.environ.get("GEMINI_API_KEY"):
            raise RuntimeError("GEMINI_API_KEY is not set")
        self._is_running = True
        self._mic_thread = threading.Thread(target=self._mic_worker, daemon=True, name="STTMic")
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="STTLoop")
        self._mic_thread.start()
        self._thread.start()
        self._log("starting - connect() returned, waiting for live session...")

    def listen(self, timeout: Optional[float] = None) -> str:
        """Block until a sentence is available. Raises queue.Empty on timeout."""
        if timeout is None:
            return self.sentence_queue.get()
        return self.sentence_queue.get(timeout=timeout)

    def close(self) -> None:
        self._is_running = False
        self._capture_enabled.clear()
        self._ready.clear()
        for t in (self._thread, self._mic_thread):
            if t and t.is_alive():
                t.join(timeout=2.0)
        self._log("closed.")

    def set_muted(self, muted: bool) -> None:
        """Stop/resume sending microphone audio (used while Jasper speaks)."""
        if muted:
            self._muted.set()
            with self._lock:
                self._pending.clear()
                self._last_interim_text = ""
            self._clear_preview()
            self._log("muted (microphone audio discarded)")
        else:
            self._drain_audio()
            self._muted.clear()
            self._log("unmuted")

    def wait_until_ready(self, timeout: Optional[float] = 30.0) -> bool:
        """Block until the live session is connected and capturing audio."""
        return self._ready.wait(timeout=timeout)

    def seconds_since_speech(self) -> float:
        """Seconds since the last interim transcription text arrived."""
        if not self._last_interim:
            return float("inf")
        return time.monotonic() - self._last_interim

    @property
    def is_ready(self) -> bool:
        return self._ready.is_set()

    @property
    def stats(self) -> dict:
        return dict(self._stats)

    # --------------------------------------------------------------- utilities

    @staticmethod
    def _to_bcp47(tag: str) -> str:
        """Map a short language tag onto a BCP-47 code the API understands."""
        t = (tag or "").strip()
        if not t:
            return "en-US"
        if "-" in t:
            return t
        return {"en": "en-US", "es": "es-ES", "fr": "fr-FR", "de": "de-DE",
                "it": "it-IT", "pt": "pt-BR", "nl": "nl-NL", "ru": "ru-RU",
                "ja": "ja-JP", "ko": "ko-KR", "zh": "cmn-Hans-CN",
                "hi": "hi-IN", "ar": "ar-EG"}.get(t.lower(), t)

    def _log(self, msg: str) -> None:
        if not self.verbose:
            return
        stamp = time.strftime("%H:%M:%S")
        sys.stderr.write(f"\r\033[K[STT {stamp}] {msg}\n")
        sys.stderr.flush()

    def _drain_audio(self) -> None:
        while True:
            try:
                self._audio_q.get_nowait()
            except queue.Empty:
                break

    def _clear_preview(self) -> None:
        if self.flush and self.debug_preview:
            sys.stdout.write("\r\033[K")
            sys.stdout.flush()

    def _render_live_preview(self, text: str) -> None:
        """
        Show the interim hypothesis in the terminal.

        The in-place `\r` redraw garbles interleaved log output, so it is only
        enabled when `debug_preview` is set. The Jasper voice loop leaves it
        off and relies on the timestamped [STT] log lines instead.
        """
        if not (self.flush and self.debug_preview and text):
            return
        cols = max(20, shutil.get_terminal_size().columns - 10)
        disp = ("..." + text[-cols:]) if len(text) > cols else text
        sys.stdout.write(f"\r\033[KLive: {disp}")
        sys.stdout.flush()

    def _rms(self, data: bytes) -> float:
        """RMS level of a 16-bit PCM chunk (cheap, no numpy dependency)."""
        if len(data) < 2:
            return 0.0
        total = 0
        count = len(data) // 2
        for i in range(0, count * 2, 2):
            sample = data[i] | (data[i + 1] << 8)
            if sample >= 32768:
                sample -= 65536
            total += sample * sample
        return (total / count) ** 0.5

    def _build_config(self) -> types.LiveConnectConfig:
        kwargs = {"language_codes": self.language_codes}
        if self.custom_vocabulary:
            kwargs["custom_vocabulary"] = self.custom_vocabulary
        if self.mode:
            kwargs["mode"] = self.mode
        try:
            transcription = types.AudioTranscriptionConfig(**kwargs)
        except Exception as exc:
            self._log(
                f"google-genai does not accept {sorted(kwargs)} ({exc}). "
                "Run: pip install -U google-genai. Falling back to defaults."
            )
            transcription = types.AudioTranscriptionConfig()
        return types.LiveConnectConfig(
            response_modalities=["TEXT"],
            input_audio_transcription=transcription,
        )

    # ------------------------------------------------------------- microphone

    def _mic_worker(self) -> None:
        """Own the microphone for the whole life of the STT object."""
        pya = pyaudio.PyAudio()
        stream = None
        last_level_log = 0.0
        level_window: deque = deque(maxlen=100)
        try:
            while self._is_running:
                try:
                    if stream is None:
                        stream = pya.open(
                            format=FORMAT,
                            channels=CHANNELS,
                            rate=SEND_SAMPLE_RATE,
                            input=True,
                            frames_per_buffer=CHUNK_SIZE,
                        )
                        self._log(f"microphone open ({stream._stream} @ {SEND_SAMPLE_RATE} Hz)")
                    data = stream.read(CHUNK_SIZE, exception_on_overflow=False)
                except Exception as exc:
                    self._log(f"microphone error: {exc!r}; retrying in 2s")
                    if stream is not None:
                        try:
                            stream.close()
                        except Exception:
                            pass
                        stream = None
                    time.sleep(2.0)
                    continue

                if self._muted.is_set():
                    continue

                # Do NOT record while the live session is not connected. The old
                # behaviour buffered this audio and then threw it away, which
                # silently ate the first thing the user said.
                if not self._capture_enabled.is_set():
                    continue

                # Microphone level meter - makes "is it hearing me?" obvious.
                level = self._rms(data)
                level_window.append(level)
                now = time.monotonic()
                if now - last_level_log >= LEVEL_LOG_INTERVAL:
                    last_level_log = now
                    peak = max(level_window) if level_window else 0.0
                    floor = min(level_window) if level_window else 0.0
                    avg = sum(level_window) / len(level_window) if level_window else 0.0
                    bar = "#" * int(min(30, peak / 60))
                    self._log(f"mic level avg={avg:6.1f} peak={peak:6.1f} "
                              f"floor={floor:6.1f} "
                              f"{'SPEECH' if peak > VOICE_THRESHOLD else 'quiet '} {bar}")

                try:
                    self._audio_q.put_nowait(data)
                    self._stats["chunks"] += 1
                    self._stats["bytes"] += len(data)
                except queue.Full:
                    try:
                        self._audio_q.get_nowait()      # drop oldest
                        self._stats["drops"] += 1
                    except queue.Empty:
                        pass
                    try:
                        self._audio_q.put_nowait(data)
                    except queue.Full:
                        pass
        finally:
            if stream is not None:
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    pass
            try:
                pya.terminate()
            except Exception:
                pass

    # ---------------------------------------------------------- async session

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._main())
        except Exception as exc:
            self._log(f"fatal STT loop error: {exc!r}")
        finally:
            self._loop.close()

    async def _main(self) -> None:
        client = genai.Client(
            http_options={"api_version": "v1beta"},
            api_key=os.environ.get("GEMINI_API_KEY"),
        )
        config = self._build_config()
        self._log(f"model={MODEL} language_codes={self.language_codes} "
                  f"vocabulary={self.custom_vocabulary} merge_window={self.merge_window}s")
        flusher = asyncio.create_task(self._flush_loop())
        backoff = 1.0
        try:
            while self._is_running:
                started = time.monotonic()
                try:
                    await self._run_session(client, config)
                    backoff = 1.0
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self._log(f"session error: {exc!r}")
                    if time.monotonic() - started > 30.0:
                        backoff = 1.0               # it ran fine for a while
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, RECONNECT_MAX_BACKOFF)
        finally:
            self._capture_enabled.clear()
            self._ready.clear()
            flusher.cancel()
            await asyncio.gather(flusher, return_exceptions=True)

            await client.aio.aclose()

    async def _run_session(self, client, config) -> None:
        async with client.aio.live.connect(model=MODEL, config=config) as session:
            self._stats["sessions"] += 1
            self._drain_audio()
            # Only now start feeding the microphone into the session.
            self._capture_enabled.set()
            self._ready.set()
            self._log(f"connected (session #{self._stats['sessions']}) - LISTENING NOW")
            started = time.monotonic()
            tasks = [
                asyncio.create_task(self._send_audio(session)),
                asyncio.create_task(self._receive(session)),
            ]
            try:
                while self._is_running:
                    await asyncio.sleep(0.1)

                    for t in tasks:
                        if t.done():
                            t.result()              # re-raises the task's error
                            raise RuntimeError("STT task ended unexpectedly")

                    now = time.monotonic()
                    age = now - started
                    quiet = (now - self._last_interim) > 1.5 and not self._pending
                    if age > SESSION_SOFT_LIMIT and quiet:
                        self._log("refreshing session (10-minute limit)")
                        return
                    if age > SESSION_HARD_LIMIT:
                        self._log("refreshing session (hard limit)")
                        return
            finally:
                self._capture_enabled.clear()
                self._ready.clear()
                for t in tasks:
                    t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    async def _send_audio(self, session) -> None:
        while self._is_running:
            try:
                data = await asyncio.to_thread(self._audio_q.get, True, 0.2)
            except queue.Empty:
                continue
            try:
                await session.send_realtime_input(
                    audio=types.Blob(data=data, mime_type=AUDIO_MIME)
                )
            except Exception as exc:
                # The socket is going away; let the session loop reconnect.
                self._log(f"send failed: {exc!r}")
                raise

    async def _receive(self, session) -> None:
        while self._is_running:
            try:
                async for response in session.receive():
                    if not self._is_running:
                        return
                    sc = getattr(response, "server_content", None)
                    if not sc:
                        continue

                    interim = getattr(sc, "interim_input_transcription", None)
                    if interim is not None and getattr(interim, "text", None):
                        self._on_interim(interim.text)

                    final = getattr(sc, "input_transcription", None)
                    if final is not None and getattr(final, "text", None):
                        text = final.text.strip()
                        if text:
                            self._on_final(text)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # 1008 on teardown is normal - do not spam a traceback.
                self._log(f"receive ended: {type(exc).__name__}: {exc}")
                raise
            # receive() may end at a turn boundary; loop and keep listening.
            await asyncio.sleep(0.01)

    # ----------------------------------------------------- transcript handling

    def _on_interim(self, text: str) -> None:
        now = time.monotonic()
        if not self._last_interim_text:
            # First fragment of a new utterance: remember when it was captured.
            self._utterance_start = now
        self._utterance_end = now
        self._last_interim = now
        self._stats["interims"] += 1
        self._last_interim_text = text
        self._render_live_preview(text)
        if self.on_interim:
            try:
                self.on_interim(text)
            except Exception as exc:
                self._log(f"on_interim callback failed: {exc!r}")

    def _on_final(self, text: str) -> None:
        now = time.monotonic()
        with self._lock:
            self._pending.append(text)
            self._last_final = now
            if not self._utterance_start:
                self._utterance_start = now
            self._utterance_end = now
        self._stats["finals"] += 1
        self._log(f"final transcript: {text!r}")

    def _emit(self, line: str) -> None:
        """Publish a finished sentence together with its capture window."""
        self._turn_id += 1
        t0 = self._utterance_start or self._last_final
        t1 = self._utterance_end or self._last_final
        self.sentence_queue.put(Utterance(line, t0, t1, self._turn_id))
        self._utterance_start = 0.0
        self._utterance_end = 0.0

    async def _flush_loop(self) -> None:
        """Emit one sentence once the speaker has really stopped (merge_window)."""
        while self._is_running:
            await asyncio.sleep(0.05)
            line = ""
            promote = False
            with self._lock:
                now = time.monotonic()
                if self._pending:
                    if (now - self._last_final) < self.merge_window:
                        continue
                    if (now - self._last_interim) < self.merge_window:
                        continue
                    line = " ".join(self._pending).strip()
                    self._pending.clear()
                    self._last_interim_text = ""
                elif self._last_interim_text:
                    # Safety net: the server has not finalised, but the speaker
                    # has gone quiet for longer than silence_threshold. Promote
                    # the best interim hypothesis instead of waiting forever.
                    if (now - self._last_interim) >= self.silence_threshold:
                        line = self._last_interim_text.strip()
                        self._last_interim_text = ""
                        promote = True
            if line:
                self._clear_preview()
                self._stats["sentences"] += 1
                if promote:
                    self._log(f"emitted (promoted interim, no server final): {line!r}")
                else:
                    self._log(f"emitted: {line!r}")
                self._emit(line)


if __name__ == "__main__":
    stt = STT(flush=True)
    stt.connect()
    print("Waiting for the live session to come up...", flush=True)
    if stt.wait_until_ready(30.0):
        print("READY - speak now.", flush=True)
    else:
        print("Session did not come up in time; see the [STT] log above.", flush=True)
    try:
        while True:
            print(stt.listen(), flush=True)
    except KeyboardInterrupt:
        stt.close()
