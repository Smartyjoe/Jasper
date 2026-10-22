import json
import os
import queue
import re
import sys
import time
import traceback
from collections import deque
import wave

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel
from vosk import Model, KaldiRecognizer

from transcript_enhancer import TranscriptEnhancer, discover_model_id


# ---------------- CONFIGURATION ----------------

VOSK_MODEL_PATH = r"models\vosk-model-small-en-us-0.15"
MICROPHONE_DEVICE = 1
SAMPLE_RATE = 16000
BLOCK_SIZE = 800                   # 50 ms per chunk at 16kHz
VOICE_THRESHOLD = 350              # RMS energy threshold for speech start/continuation
SILENCE_SECONDS = 1.2              # Silence pause after speech to end command
COMMAND_TIMEOUT = 5.0              # Max seconds to wait for initial speech
MAX_COMMAND_SECONDS = 15.0         # Max command length
PREROLL_BLOCKS = 8                 # ~400ms pre-roll to prevent cutting the first consonant
# `small.en` is a better CPU accuracy/speed tradeoff than `base.en`.
# Use `medium.en` or `large-v3` when the machine can afford the latency/RAM.
WHISPER_MODEL_NAME = os.getenv("JASPER_WHISPER_MODEL", "small.en")

# Local Transcript Intelligence (Qwen via Ollama)
ENABLE_TRANSCRIPT_INTELLIGENCE = True
OLLAMA_ENDPOINT = "http://localhost:11434"
ENHANCER_TIMEOUT = 6.0

audio_queue = queue.Queue(maxsize=300)
whisper_model = None
transcript_enhancer = None
is_processing = False


# ---------------- MICROPHONE ----------------

def audio_callback(indata, frames, time_info, status):
    global is_processing
    if is_processing:
        # Ignore microphone input while Whisper & Ollama are processing to avoid buffer overflow
        return

    if status:
        print(f"[WARN] Microphone buffer status: {status}", file=sys.stderr)

    chunk = bytes(indata)

    try:
        audio_queue.put_nowait(chunk)
    except queue.Full:
        # Drop oldest block if queue overflows
        try:
            audio_queue.get_nowait()
        except queue.Empty:
            pass

        try:
            audio_queue.put_nowait(chunk)
        except queue.Full:
            pass


def clear_audio_queue():
    """Flush pending audio blocks."""
    while True:
        try:
            audio_queue.get_nowait()
        except queue.Empty:
            break


def audio_level(chunk: bytes) -> float:
    """Calculate RMS energy of raw 16-bit audio bytes."""
    samples = np.frombuffer(chunk, dtype=np.int16).astype(np.float32)
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(samples * samples)))


# ---------------- COMMAND CAPTURE ----------------

def capture_command(initial_chunks=None):
    """
    Capture the user's spoken command with continuous pre-roll and duration protection.
    MIN_SPEECH_DURATION ensures natural pauses cannot prematurely truncate speech.
    """
    print("\nJasper is listening. Speak your command now.")

    preroll_buffer = deque(maxlen=PREROLL_BLOCKS)
    if initial_chunks:
        for ch in initial_chunks:
            preroll_buffer.append(ch)

    chunks = []
    start_time = time.monotonic()
    speech_started = False
    last_voice_time = None
    MIN_SPEECH_DURATION = 1.0          # Must capture at least 1.0s of speech before silence cutoff

    while True:
        elapsed = time.monotonic() - start_time

        if not speech_started and elapsed >= COMMAND_TIMEOUT:
            print("[INFO] No speech detected within timeout. Returning to wake-word listener.")
            return None

        if elapsed >= MAX_COMMAND_SECONDS:
            print("[INFO] Maximum command duration reached.")
            break

        try:
            chunk = audio_queue.get(timeout=0.5)
        except queue.Empty:
            continue

        level = audio_level(chunk)
        now = time.monotonic()

        if not speech_started:
            preroll_buffer.append(chunk)
            if level >= VOICE_THRESHOLD:
                speech_started = True
                last_voice_time = now
                # Include pre-roll chunks to guarantee the first syllable is intact
                chunks.extend(list(preroll_buffer))
                preroll_buffer.clear()
        else:
            chunks.append(chunk)
            if level >= VOICE_THRESHOLD:
                last_voice_time = now
            elif last_voice_time is not None:
                current_duration = (len(chunks) * BLOCK_SIZE) / SAMPLE_RATE
                if current_duration >= MIN_SPEECH_DURATION and (now - last_voice_time >= SILENCE_SECONDS):
                    # Natural pause after a complete phrase
                    break

    if not speech_started or not chunks:
        print("[INFO] No command audio captured.")
        return None

    raw_audio = b"".join(chunks)

    # Save command audio to WAV file for verification
    try:
        import wave
        with wave.open("jasper_last_command.wav", "wb") as wav_out:
            wav_out.setnchannels(1)
            wav_out.setsampwidth(2)
            wav_out.setframerate(SAMPLE_RATE)
            wav_out.writeframes(raw_audio)
    except Exception as wav_err:
        print(f"[WARN] Failed to write jasper_last_command.wav: {wav_err}", file=sys.stderr)

    audio = np.frombuffer(raw_audio, dtype=np.int16).astype(np.float32) / 32768.0
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0

    # Remove DC offset and apply only conservative gain. Peak-normalizing a
    # quiet microphone also amplifies its room noise and hurts word accuracy.
    if audio.size:
        audio = audio - float(np.mean(audio))
        rms = float(np.sqrt(np.mean(audio * audio)))
        if 0.005 < rms < 0.08:
            audio *= min(3.0, 0.08 / rms)
        audio = np.clip(audio, -1.0, 1.0)

    duration = len(audio) / SAMPLE_RATE
    print(f"[DEBUG] Captured {duration:.2f}s of audio (peak {peak:.2f}) for transcription.")
    return audio


# ---------------- TRANSCRIPTION ----------------

def strip_wake_phrase(text: str) -> str:
    """Strip leading 'hey jasper' or 'jasper' if spoken continuously."""
    cleaned = re.sub(r"^(?:hey\s+)?jasper[,\s!.]*", "", text, flags=re.IGNORECASE).strip()
    return cleaned if cleaned else text


def transcribe_command(audio):
    """Transcribe captured command using the configured local Whisper model."""
    global whisper_model

    try:
        if whisper_model is None:
            print(f"\nLoading local Faster-Whisper {WHISPER_MODEL_NAME} model...")
            whisper_model = WhisperModel(
                WHISPER_MODEL_NAME,
                device="cpu",
                compute_type="int8",
                cpu_threads=4,
            )

        print("Transcribing locally...")
        start_t = time.monotonic()

        # Keep decoding independent between commands to prevent stale context
        # from changing short utterances. VAD removes trailing room noise.
        segments, info = whisper_model.transcribe(
            audio,
            language="en",
            beam_size=5,
            temperature=0.0,
            condition_on_previous_text=False,
            initial_prompt="Jasper, Ollama, speech-to-text, transcription.",
            vad_filter=True,
            vad_parameters={
                "min_silence_duration_ms": 500,
                "speech_pad_ms": 300,
            },
            no_speech_threshold=0.6,
            log_prob_threshold=-1.0,
        )

        raw_transcript = " ".join(
            segment.text.strip()
            for segment in segments
        ).strip()

        latency = time.monotonic() - start_t
        command_text = strip_wake_phrase(raw_transcript)

        print(f"\nJasper heard (raw): {command_text or '[No speech recognized]'}")
        print(f"[DEBUG] STT latency: {latency:.2f}s")

        if command_text and ENABLE_TRANSCRIPT_INTELLIGENCE and transcript_enhancer:
            try:
                enh_start = time.monotonic()
                enhanced = transcript_enhancer.enhance(command_text)
                enh_latency = time.monotonic() - enh_start

                corrected = enhanced.get("corrected_transcript") or command_text
                intent = enhanced.get("intent_summary")
                uncertainties = enhanced.get("uncertainties", [])

                if corrected != command_text:
                    print(f"Jasper understood:  {corrected}")
                if intent:
                    print(f"Intent summary:     {intent}")
                clean_uncertainties = [u for u in uncertainties if not u.startswith("Fallback:")]
                if clean_uncertainties:
                    print(f"Uncertainties:      {', '.join(clean_uncertainties)}")
                print(f"[DEBUG] Intelligence latency: {enh_latency:.2f}s")
            except Exception as enh_err:
                print(f"[WARN] Transcript intelligence error: {enh_err}", file=sys.stderr)

        print("Ready for the next wake phrase.\n")

    except Exception as exc:
        print(f"[ERROR] Failed during transcription: {exc}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)


# ---------------- MAIN LOOP ----------------

def main():
    print(f"Loading local Faster-Whisper {WHISPER_MODEL_NAME} model...")
    global whisper_model, transcript_enhancer
    try:
        whisper_model = WhisperModel(
            WHISPER_MODEL_NAME,
            device="cpu",
            compute_type="int8",
            cpu_threads=4,
        )
    except Exception as exc:
        print(f"[FATAL] Failed to initialize Whisper model: {exc}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)

    if ENABLE_TRANSCRIPT_INTELLIGENCE:
        print("Discovering local Ollama Qwen model for transcript intelligence...")
        try:
            model_id = discover_model_id(base_url=OLLAMA_ENDPOINT, timeout=3.0)
            if model_id:
                transcript_enhancer = TranscriptEnhancer(
                    base_url=OLLAMA_ENDPOINT,
                    model_id=model_id,
                    timeout=ENHANCER_TIMEOUT,
                )
                print(f"Transcript intelligence ready (Model: {model_id})")
            else:
                print("[INFO] No Qwen model found on Ollama; enhancement will fall back to raw STT.")
        except Exception as exc:
            print(f"[WARN] Could not connect to Ollama: {exc}", file=sys.stderr)

    print("Loading Vosk wake-word model...")
    try:
        vosk_model = Model(VOSK_MODEL_PATH)
        recognizer = KaldiRecognizer(vosk_model, SAMPLE_RATE)
        recognizer.SetGrammar(json.dumps([
            "hey",
            "jasper",
            "hey jasper",
            "[unk]",
        ]))
    except Exception as exc:
        print(f"[FATAL] Failed to load Vosk model at '{VOSK_MODEL_PATH}': {exc}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)

    try:
        device = sd.query_devices(MICROPHONE_DEVICE)
        print(f"Microphone: {device['name']}")
    except Exception as exc:
        print(f"[FATAL] Failed to query microphone device {MICROPHONE_DEVICE}: {exc}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)

    print("\nJASPER VOICE LOOP")
    print("Say 'Hey Jasper' to activate.")
    print("Audio is processed locally.")
    print("Press Ctrl+C to stop.\n")

    # Rolling window of recent chunks so speech right after wake phrase is never dropped
    wake_preroll = deque(maxlen=PREROLL_BLOCKS)

    try:
        with sd.RawInputStream(
            samplerate=SAMPLE_RATE,
            blocksize=BLOCK_SIZE,
            device=MICROPHONE_DEVICE,
            dtype="int16",
            channels=1,
            callback=audio_callback,
        ):
            while True:
                try:
                    chunk = audio_queue.get(timeout=0.5)
                except queue.Empty:
                    continue

                wake_preroll.append(chunk)
                wake_detected = False

                # Check both full and partial results so wake word triggers immediately mid-utterance
                if recognizer.AcceptWaveform(chunk):
                    result = json.loads(recognizer.Result())
                    phrase = result.get("text", "").lower().strip()
                    if phrase:
                        print(f"Heard: {phrase}")
                    if "hey jasper" in phrase:
                        wake_detected = True
                else:
                    partial_result = json.loads(recognizer.PartialResult())
                    partial_phrase = partial_result.get("partial", "").lower().strip()
                    if "hey jasper" in partial_phrase:
                        print(f"Heard (partial): {partial_phrase}")
                        wake_detected = True

                if wake_detected:
                    print("\nWake phrase detected!")
                    recognizer.Reset()
                    recent_chunks = list(wake_preroll)
                    wake_preroll.clear()

                    command_audio = capture_command(initial_chunks=recent_chunks)

                    if command_audio is not None:
                        is_processing = True
                        try:
                            transcribe_command(command_audio)
                        finally:
                            clear_audio_queue()
                            wake_preroll.clear()
                            recognizer.Reset()
                            is_processing = False
                    else:
                        clear_audio_queue()
                        wake_preroll.clear()
                        recognizer.Reset()

    except KeyboardInterrupt:
        print("\nJasper stopped safely.")
    except Exception as exc:
        print(f"[FATAL] Audio loop encountered an unhandled exception: {exc}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)


if __name__ == "__main__":
    main()
