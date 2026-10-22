
import json
import queue
import sys

import sounddevice as sd
from vosk import Model, KaldiRecognizer

MODEL_PATH = r"models\vosk-model-small-en-us-0.15"
MICROPHONE_DEVICE = 1
SAMPLE_RATE = 16000

audio_queue = queue.Queue()


def audio_callback(indata, frames, time_info, status):
    if status:
        print(f"Audio status: {status}", file=sys.stderr)

    audio_queue.put(bytes(indata))


def main():
    print("Loading local wake-word model...")
    model = Model(MODEL_PATH)
    recognizer = KaldiRecognizer(model, SAMPLE_RATE)

    # Limit recognition to likely wake-phrase words.
    recognizer.SetGrammar(json.dumps([
        "hey",
        "jasper",
        "hey jasper",
        "hey, jasper",
        "[unk]"
    ]))

    device = sd.query_devices(MICROPHONE_DEVICE)
    print(f"Microphone: {device['name']}")

    print("\nHey Jasper wake-word test is running.")
    print("Say 'Hey Jasper' clearly.")
    print("Press Ctrl+C to stop.\n")

    try:
        with sd.RawInputStream(
            samplerate=SAMPLE_RATE,
            blocksize=8000,
            device=MICROPHONE_DEVICE,
            dtype="int16",
            channels=1,
            callback=audio_callback,
        ):
            while True:
                audio_data = audio_queue.get()

                if recognizer.AcceptWaveform(audio_data):
                    result = json.loads(recognizer.Result())
                    text = result.get("text", "").lower().strip()

                    if text:
                        print(f"Heard: {text}")

                    if text == "hey jasper":
                        print("\nWake phrase detected! Hey Jasper is activated.\n")
                        recognizer.Reset()

    except KeyboardInterrupt:
        print("\nWake-word listener stopped.")


if __name__ == "__main__":
    main()
