
"""Gemini Live STT - text-only console output."""

import queue
from stt_engine import STT


def main():
    stt = STT(
        target_lang="en",
        silence_threshold=1.5,
        flush=True,
        verbose=False,
    )

    try:
        stt.connect()

        if not stt.wait_until_ready(timeout=30.0):
            print("Could not connect to Gemini Live.", flush=True)
            return

        while True:
            try:
                text = stt.listen(timeout=1.0)
            except queue.Empty:
                continue

            if text:
                print(text, flush=True)

    except KeyboardInterrupt:
        pass

    finally:
        stt.close()


if __name__ == "__main__":
    main()
