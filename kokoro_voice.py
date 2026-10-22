import json
import subprocess
import wave
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

    def speak(self, text: str) -> None:
        """
        Generate speech for `text` and play it to the default audio device.
        Blocks until playback is complete.
        
        Args:
            text: The text to convert to speech.
            
        Raises:
            KokoroVoiceError: On generation or playback failure.
        """
        try:
            # Prepare arguments for the Kokoro generation script
            args_json = json.dumps({
                "model": self.KOKORO_DIR + "\\kokoro-v1.0.onnx",
                "voices": self.KOKORO_DIR + "\\voices-v1.0.bin",
                "text": text,
                "voice": self.voice,
                "speed": self.speed,
                "output": self.OUTPUT_WAV,
            })
            
            # Run the generation script in a subprocess
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
                
        except subprocess.TimeoutExpired:
            raise KokoroVoiceError("Kokoro timed out")
        except FileNotFoundError:
            raise KokoroVoiceError("Kokoro Python environment not found")
        except Exception as exc:
            raise KokoroVoiceError(f"Kokoro generation error: {exc}")

        # Playback in the main process (after generation)
        try:
            import sounddevice as sd
            
            # Read WAV using stdlib wave module
            with wave.open(self.OUTPUT_WAV, 'rb') as wav_file:
                n_channels = wav_file.getnchannels()
                sample_width = wav_file.getsampwidth()
                sample_rate = wav_file.getframerate()
                n_frames = wav_file.getnframes()
                
                raw_data = wav_file.readframes(n_frames)
                
                # Convert to float32 numpy array
                if sample_width == 2:
                    dtype = np.int16
                elif sample_width == 4:
                    dtype = np.int32
                else:
                    raise KokoroVoiceError(f"Unsupported sample width: {sample_width}")
                
                data = np.frombuffer(raw_data, dtype=dtype).astype(np.float32) / 32768.0 if sample_width == 2 else np.frombuffer(raw_data, dtype=dtype).astype(np.float32) / np.iinfo(dtype).max
                
                if n_channels == 2:
                    data = data.reshape(-1, 2)
            
            sd.play(data, sample_rate)
            sd.wait()
            
        except Exception as exc:
            raise KokoroVoiceError(f"Kokoro playback failed: {exc}")