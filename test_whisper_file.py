
from faster_whisper import WhisperModel
from pathlib import Path
import time

audio_file = Path("how-are-you.wav")

if not audio_file.exists():
    raise SystemExit(
        "Recording not found. Run Jasper once first to create "
        "how-are-you.wav."
    )

print("Loading base.en...")
model = WhisperModel(
    "base.en",
    device="cpu",
    compute_type="int8",
    cpu_threads=4,
)

print("Testing the existing recording...")
start = time.monotonic()

segments, info = model.transcribe(
    str(audio_file),
    language="en",
    beam_size=5,
    temperature=0.0,
    vad_filter=False,
    condition_on_previous_text=False,
)

segments = list(segments)

print("\nTranscript:")
print(" ".join(segment.text.strip() for segment in segments))

print(f"\nAudio duration: {info.duration:.2f} seconds")
print(f"Test time: {time.monotonic() - start:.2f} seconds")

for segment in segments:
    print(
        f"[{segment.start:.2f}s - {segment.end:.2f}s] "
        f"{segment.text.strip()}"
    )
