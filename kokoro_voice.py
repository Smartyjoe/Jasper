import json
import os
import subprocess
import time
import wave
from typing import Optional, Tuple
import numpy as np


class KokoroVoiceError(Exception):
    """Raised when TTS generation or playback fails."""


class KokoroVoice:
    KOKORO_PYTHON = r"C:\Users\USER\kokoro-demo\venv\Scripts\python.exe"
    KOKORO_DIR    = r"C:\Users\USER\kokoro-demo"
    OUTPUT_WAV    = r"C:\Users\USER\kenny\jasper_response.wav"
    DEFAULT_VOICE = "am_fenrir"
    GEN_SCRIPT    = r"C:\Users\USER\kokoro-demo\gen_script.py"

    def __init__(self, voice: str = DEFAULT_VOICE, speed: float = 1.0):
        """
        Initialize Kokoro voice TTS engine.

        Args:
            voice: The voice to use (e.g., "am_fenrir", "af_heart").
            speed: Speech speed multiplier (1.0 = normal speed).
        """
        self.voice = voice
        self.speed = speed
        self._is_playing = False

    def synthesize(self, text: str, output_wav: Optional[str] = None) -> Tuple[str, float]:
        """
        Synthesize `text` to WAV file without playing audio.

        Args:
            text: Text to synthesize.
            output_wav: Path to output WAV file (defaults to self.OUTPUT_WAV).

        Returns:
            Tuple of (output_wav_path, generation_time_seconds).
        """
        target_wav = output_wav or self.OUTPUT_WAV
        gen_start = time.monotonic()

        try:
            args_json = json.dumps({
                "model": os.path.join(self.KOKORO_DIR, "kokoro-v1.0.onnx"),
                "voices": os.path.join(self.KOKORO_DIR, "voices-v1.0.bin"),
                "text": text,
                "voice": self.voice,
                "speed": self.speed,
                "output": target_wav,
            })

            result = subprocess.run(
                [self.KOKORO_PYTHON, self.GEN_SCRIPT, args_json],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=20,
            )

            if result.returncode != 0:
                error_msg = result.stderr.strip() if result.stderr else "Unknown error"
                raise KokoroVoiceError(f"Kokoro generation failed: {error_msg}")

            gen_duration = time.monotonic() - gen_start
            return target_wav, round(gen_duration, 3)

        except subprocess.TimeoutExpired:
            raise KokoroVoiceError("Kokoro timed out")
        except FileNotFoundError:
            raise KokoroVoiceError("Kokoro Python environment not found")
        except Exception as exc:
            raise KokoroVoiceError(f"Kokoro generation error: {exc}")

    def play_wav(self, wav_path: Optional[str] = None) -> float:
        """
        Play audio file from `wav_path`. Blocks until playback is complete.

        Args:
            wav_path: Path to WAV file to play (defaults to self.OUTPUT_WAV).

        Returns:
            Playback duration in seconds.
        """
        target_wav = wav_path or self.OUTPUT_WAV
        play_start = time.monotonic()

        try:
            import sounddevice as sd

            with wave.open(target_wav, 'rb') as wav_file:
                n_channels = wav_file.getnchannels()
                sample_width = wav_file.getsampwidth()
                sample_rate = wav_file.getframerate()
                n_frames = wav_file.getnframes()

                raw_data = wav_file.readframes(n_frames)

                if sample_width == 2:
                    dtype = np.int16
                elif sample_width == 4:
                    dtype = np.int32
                else:
                    raise KokoroVoiceError(f"Unsupported sample width: {sample_width}")

                if sample_width == 2:
                    data = np.frombuffer(raw_data, dtype=dtype).astype(np.float32) / 32768.0
                else:
                    data = np.frombuffer(raw_data, dtype=dtype).astype(np.float32) / np.iinfo(dtype).max

                if n_channels == 2:
                    data = data.reshape(-1, 2)

            self._is_playing = True
            sd.play(data, sample_rate)
            sd.wait()
            self._is_playing = False

            play_duration = time.monotonic() - play_start
            return round(play_duration, 3)

        except Exception as exc:
            self._is_playing = False
            raise KokoroVoiceError(f"Kokoro playback failed: {exc}")

    def stop_playback(self) -> None:
        """Stop any ongoing sounddevice audio playback immediately."""
        try:
            import sounddevice as sd
            sd.stop()
            self._is_playing = False
        except Exception:
            pass

    @property
    def is_playing(self) -> bool:
        return self._is_playing

    def speak(self, text: str) -> Tuple[float, float]:
        """
        Generate speech and play it sequentially.

        Returns:
            Tuple of (generation_time_sec, playback_time_sec).
        """
        wav_path, gen_time = self.synthesize(text)
        play_time = self.play_wav(wav_path)
        return gen_time, play_time
