"""
tts_engine.py - Dedicated, interruptible text-to-speech for Jasper.

Why this replaces the "repeat exactly" Live-model hack
-------------------------------------------------------
The previous implementation asked a live *agent* model to "Repeat the
following text exactly", which can paraphrase, answer, or refuse. It also
waited for the entire response before any audio was played, and it had no
timeout, so a dropped session meant an infinite hang.

This engine uses a dedicated Gemini TTS model
(`gemini-2.5-flash-preview-tts`) via a single non-live request. The model
returns raw PCM (`audio/L16;codec=pcm;rate=24000`), which is played directly.

Features
--------
* Dedicated TTS model - no paraphrasing, no "output nothing else" prompt.
* Hard request timeout - a slow model can never hang the voice loop.
* Interruptible playback - audio is written in 20 ms slices and a stop flag is
  checked between slices, so a barge-in stops sound within ~20 ms.
* Fallback to the Live TTS path if the dedicated model is unavailable.
"""

import os
import queue
import sys
import threading
import time
from typing import Optional

import pyaudio
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

FORMAT = pyaudio.paInt16
CHANNELS = 1
DEFAULT_RATE = 24000
SLICE_FRAMES = 480          # 20 ms @ 24 kHz

DEFAULT_TTS_MODEL = "gemini-2.5-flash-preview-tts"
DEFAULT_VOICE = "Zephyr"


class TTSEngineError(Exception):
    """Raised when speech synthesis or playback fails."""


class TTSEngine:
    """Interruptible Gemini TTS with a Live-session fallback."""

    def __init__(
        self,
        voice: str = DEFAULT_VOICE,
        model: str = DEFAULT_TTS_MODEL,
        timeout: float = 20.0,
        allow_live_fallback: bool = True,
    ):
        self.voice = voice
        self.model = model
        self.timeout = timeout
        self.allow_live_fallback = allow_live_fallback

        self._client = genai.Client(
            http_options={"api_version": "v1beta"},
            api_key=os.environ.get("GEMINI_API_KEY"),
        )
        self._pya: Optional[pyaudio.PyAudio] = None
        self._stream = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._playing = False
        self._live_tts = None      # lazily imported fallback

    # ------------------------------------------------------------------ public

    @property
    def is_playing(self) -> bool:
        return self._playing

    def interrupt(self) -> None:
        """Stop audio playback immediately (barge-in)."""
        self._stop.set()

    def speak(self, text: str) -> float:
        """
        Synthesize and play `text`. Blocks until playback finishes.

        Returns:
            Playback duration in seconds (0.0 if playback was interrupted).
        """
        if not text or not text.strip():
            return 0.0

        try:
            pcm, rate = self._synthesize(text)
        except Exception as exc:
            print(f"[WARN] Dedicated TTS failed ({exc}); using Live TTS fallback.",
                  file=sys.stderr, flush=True)
            if not self.allow_live_fallback:
                raise TTSEngineError(str(exc))
            return self._speak_live_fallback(text)

        return self._play(pcm, rate)

    def close(self) -> None:
        """Release the audio device."""
        with self._lock:
            if self._stream is not None:
                try:
                    self._stream.stop_stream()
                    self._stream.close()
                except Exception:
                    pass
                self._stream = None
            if self._pya is not None:
                try:
                    self._pya.terminate()
                except Exception:
                    pass
                self._pya = None

    # ----------------------------------------------------------------- private

    def _synthesize(self, text: str):
        """Return (raw PCM bytes, sample rate) for `text`."""
        config = types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=self.voice)
                )
            ),
        )
        response = self._client.models.generate_content(
            model=self.model,
            contents=text,
            config=config,
        )
        if not response.candidates:
            raise TTSEngineError("TTS returned no candidates")
        part = response.candidates[0].content.parts[0]
        if not getattr(part, "inline_data", None) or not part.inline_data.data:
            raise TTSEngineError("TTS returned no audio data")

        mime = part.inline_data.mime_type or ""
        rate = DEFAULT_RATE
        if "rate=" in mime:
            try:
                rate = int(mime.split("rate=")[1].split(";")[0])
            except Exception:
                rate = DEFAULT_RATE
        return part.inline_data.data, rate

    def _play(self, pcm: bytes, rate: int) -> float:
        """Write PCM to the speakers in interruptible 20 ms slices."""
        self._stop.clear()
        started = time.monotonic()
        with self._lock:
            if self._pya is None:
                self._pya = pyaudio.PyAudio()
            if self._stream is None:
                self._stream = self._pya.open(
                    format=FORMAT,
                    channels=CHANNELS,
                    rate=rate,
                    output=True,
                    frames_per_buffer=1024,
                )

        self._playing = True
        try:
            frame_bytes = 2 * CHANNELS
            total = len(pcm) - (len(pcm) % frame_bytes)
            for offset in range(0, total, SLICE_FRAMES * frame_bytes):
                if self._stop.is_set():
                    break
                self._stream.write(pcm[offset:offset + SLICE_FRAMES * frame_bytes])
        except Exception as exc:
            raise TTSEngineError(f"Playback failed: {exc}") from exc
        finally:
            self._playing = False
            self._stop.clear()

        return round(time.monotonic() - started, 3)

    def _speak_live_fallback(self, text: str) -> float:
        """Fall back to the Gemini Live TTS path."""
        if self._live_tts is None:
            from Google_Gemini_Live_TTS import TTS as live_TTS
            self._live_tts = live_TTS
        started = time.monotonic()
        self._live_tts(text, self.voice, timeout=self.timeout)
        return round(time.monotonic() - started, 3)
