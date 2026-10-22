
import os
import wave
import sounddevice as sd
from faster_whisper import WhisperModel

AUDIO_FILE = "stt_test.wav"
MICROPHONE_DEVICE = 1
RECORD_SECONDS = 6

def main():
    print("Loading Whisper tiny model...")
    print("The first run may download model files.")

    model = WhisperModel(
        "tiny",
        device="cpu",
        compute_type="int8",
        cpu_threads=4,
    )

    device = sd.query_devices(MICROPHONE_DEVICE)
    sample_rate = int(device["default_samplerate"])

    print(f"\nMicrophone: {device['name']}")
    print(f"Recording for {RECORD_SECONDS} seconds.")
    print("Speak clearly when recording begins...")

    recording = sd.rec(
        int(RECORD_SECONDS * sample_rate),
        samplerate=sample_rate,
        channels=1,
        dtype="int16",
        device=MICROPHONE_DEVICE,
    )
    sd.wait()

    with wave.open(AUDIO_FILE, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(recording.tobytes())

    print("\nTranscribing your speech...")

    try:
        segments, info = model.transcribe(
            AUDIO_FILE,
            beam_size=1,
            vad_filter=True,
        )

        transcript = " ".join(
            segment.text.strip() for segment in segments
        ).strip()

        print(f"\nDetected language: {info.language}")
        print(f"You said: {transcript or '[No speech detected]'}")

    finally:
        if os.path.exists(AUDIO_FILE):
            os.remove(AUDIO_FILE)

    print("\nSpeech-to-text test finished.")

if __name__ == "__main__":
    main()
