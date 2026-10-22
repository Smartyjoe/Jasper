import json
import os
import queue
import re
import sys
import time
import traceback
from collections import deque
import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel
from vosk import Model, KaldiRecognizer

from timing_tracker import RequestMetrics
from gemma_controller import GemmaController
from local_actions import get_action_engine
from memory_store import get_memory_store
from hermes_worker import HermesWorker
from opencode_worker import OpenCodeWorker
from kokoro_voice import KokoroVoice
from response_coordinator import ResponseCoordinator
from jasper_ui import start_ui_thread


# ---------------- CONFIGURATION ----------------

VOSK_MODEL_PATH = r"models\vosk-model-small-en-us-0.15"
MICROPHONE_DEVICE = 1
SAMPLE_RATE = 16000
BLOCK_SIZE = 800                   # 50 ms per chunk at 16kHz
VOICE_THRESHOLD = 350              # RMS energy threshold
SILENCE_SECONDS = 1.2              # Silence pause after speech
COMMAND_TIMEOUT = 5.0              # Max seconds to wait for initial speech
MAX_COMMAND_SECONDS = 15.0         # Max command length
PREROLL_BLOCKS = 8                 # ~400ms pre-roll to preserve first syllable
WHISPER_MODEL_NAME = os.getenv("JASPER_WHISPER_MODEL", "small.en")

audio_queue = queue.Queue(maxsize=300)
ui_queue = queue.Queue()

whisper_model = None
gemma_controller = None
action_engine = None
memory_store = None
hermes_worker = None
opencode_worker = None
kokoro_voice = None
response_coordinator = None

is_processing = False


# ---------------- MICROPHONE ----------------

def audio_callback(indata, frames, time_info, status):
    global is_processing
    if is_processing:
        # Ignore mic input while processing or playing audio to prevent buffer overflow & self-trigger
        return

    if status:
        print(f"[WARN] Microphone buffer status: {status}", file=sys.stderr)

    chunk = bytes(indata)
    try:
        audio_queue.put_nowait(chunk)
    except queue.Full:
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
    """Capture spoken command with pre-roll and silence detection."""
    print("\nJasper is listening. Speak your command now.")
    if ui_queue:
        ui_queue.put(("listening", "Listening for command..."))

    preroll_buffer = deque(maxlen=PREROLL_BLOCKS)
    if initial_chunks:
        for ch in initial_chunks:
            preroll_buffer.append(ch)

    chunks = []
    start_time = time.monotonic()
    speech_started = False
    last_voice_time = None
    MIN_SPEECH_DURATION = 1.0

    while True:
        elapsed = time.monotonic() - start_time

        if not speech_started and elapsed >= COMMAND_TIMEOUT:
            print("[INFO] No speech detected within timeout. Returning to wake-word listener.")
            if ui_queue:
                ui_queue.put(("idle", "Say 'Hey Jasper'"))
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
                chunks.extend(list(preroll_buffer))
                preroll_buffer.clear()
        else:
            chunks.append(chunk)
            if level >= VOICE_THRESHOLD:
                last_voice_time = now
            elif last_voice_time is not None:
                current_duration = (len(chunks) * BLOCK_SIZE) / SAMPLE_RATE
                if current_duration >= MIN_SPEECH_DURATION and (now - last_voice_time >= SILENCE_SECONDS):
                    break

    if not speech_started or not chunks:
        print("[INFO] No command audio captured.")
        if ui_queue:
            ui_queue.put(("idle", "Say 'Hey Jasper'"))
        return None

    raw_audio = b"".join(chunks)

    # Save last command WAV for verification
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
    if audio.size:
        audio = audio - float(np.mean(audio))
        rms = float(np.sqrt(np.mean(audio * audio)))
        if 0.005 < rms < 0.08:
            audio *= min(3.0, 0.08 / rms)
        audio = np.clip(audio, -1.0, 1.0)

    duration = len(audio) / SAMPLE_RATE
    print(f"[DEBUG] Captured {duration:.2f}s of command audio for transcription.")
    return audio


# ---------------- TRANSCRIPTION & ROUTING ----------------

def strip_wake_phrase(text: str) -> str:
    """Strip leading 'hey jasper' or 'jasper' if spoken continuously."""
    cleaned = re.sub(r"^(?:hey\s+)?jasper[,\s!.]*", "", text, flags=re.IGNORECASE).strip()
    return cleaned if cleaned else text


def process_voice_request(audio):
    """V1.2 Request Processor: STT -> Gemma Routing -> Action Engine / Hermes -> Response Coordinator -> UI."""
    global whisper_model, gemma_controller, action_engine, memory_store
    global hermes_worker, opencode_worker, response_coordinator, ui_queue

    metrics = RequestMetrics()
    metrics.mark("stt_start")

    if ui_queue:
        ui_queue.put(("processing", "Transcribing audio..."))

    try:
        if whisper_model is None:
            whisper_model = WhisperModel(WHISPER_MODEL_NAME, device="cpu", compute_type="int8", cpu_threads=4)

        print("Transcribing locally...")
        segments, info = whisper_model.transcribe(
            audio,
            language="en",
            beam_size=5,
            temperature=0.0,
            condition_on_previous_text=False,
            initial_prompt="Jasper, Ollama, speech-to-text, transcription.",
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500, "speech_pad_ms": 300},
            no_speech_threshold=0.6,
            log_prob_threshold=-1.0,
        )

        raw_transcript = " ".join(segment.text.strip() for segment in segments).strip()
        metrics.mark("stt_end")
        metrics.measure("stt_start", "stt_end", "stt_transcription")

        command_text = strip_wake_phrase(raw_transcript)
        print(f"\nJasper heard: {command_text or '[No speech recognized]'}")

        if not command_text:
            print("[INFO] Empty command text. Returning to listening.")
            if ui_queue:
                ui_queue.put(("idle", "Say 'Hey Jasper'"))
            metrics.mark("request_complete")
            metrics.print_summary()
            return

        if ui_queue:
            ui_queue.put(("processing", f"Heard: {command_text[:28]}"))

        # Fast Intent Classification & Routing via Gemma Controller
        metrics.mark("routing_start")
        recent_turns = memory_store.get_recent_turns(3) if memory_store else []
        route_decision = gemma_controller.route_request(command_text, history=recent_turns)
        metrics.mark("routing_end")
        metrics.measure("routing_start", "routing_end", "intent_routing")

        route = route_decision.get("route", "hermes")
        action_name = route_decision.get("action")
        spoken_ack = route_decision.get("spoken_ack") or "Working on that."
        final_answer = ""

        print(f"[ROUTE] Selected Route: '{route}' | Action: '{action_name}' | Source: '{route_decision.get('classified_by')}'")

        # 1. Speak immediate acknowledgement
        if spoken_ack and response_coordinator:
            response_coordinator.speak_ack(spoken_ack, metrics=metrics)

        # 2. Execute route logic
        if route == "answer":
            final_answer = route_decision.get("answer_text") or "I am ready to help you."

        elif route == "local_action" and action_name:
            metrics.mark("action_start")
            args = route_decision.get("arguments", {})
            action_res = action_engine.execute(action_name, args)
            metrics.mark("action_end")
            metrics.measure("action_start", "action_end", f"action_{action_name}")

            if action_res.get("status") == "success":
                final_answer = response_coordinator.format_action_result(action_name, action_res.get("data", {}))
                if memory_store:
                    memory_store.log_task_run(
                        request_id=metrics.request_id,
                        action_name=action_name,
                        arguments=args,
                        status="success",
                        result=action_res.get("data"),
                        duration_sec=action_res.get("duration_sec", 0.0)
                    )
            else:
                final_answer = f"Sorry, I encountered an error running {action_name}: {action_res.get('error')}"

        elif route == "opencode":
            metrics.mark("action_start")
            task = command_text
            res = opencode_worker.run_task(task)
            metrics.mark("action_end")
            metrics.measure("action_start", "action_end", "opencode_task")
            final_answer = res.get("result", "OpenCode completed the task.")

        else: # Default: Hermes delegation
            metrics.mark("action_start")
            res = hermes_worker.run_task(command_text)
            metrics.mark("action_end")
            metrics.measure("action_start", "action_end", "hermes_task")
            final_answer = res.get("result") or "Hermes finished processing."

        # 3. Speak final response
        print(f"Jasper: {final_answer}")
        if response_coordinator and final_answer:
            response_coordinator.speak_final(final_answer, metrics=metrics)

        # 4. Save to Memory Store
        if memory_store:
            memory_store.add_turn(
                request_id=metrics.request_id,
                raw_transcript=command_text,
                route=route,
                action_name=action_name,
                response=final_answer
            )

        metrics.mark("request_complete")
        metrics.print_summary()

    except Exception as exc:
        print(f"[ERROR] Request processing failed: {exc}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        if ui_queue:
            ui_queue.put(("idle", "Say 'Hey Jasper'"))


# ---------------- MAIN LOOP ----------------

def main():
    global ui_queue, whisper_model, gemma_controller, action_engine, memory_store
    global hermes_worker, opencode_worker, kokoro_voice, response_coordinator

    print("Initializing Jasper UI Overlay Thread...")
    start_ui_thread(ui_queue)
    if ui_queue:
        ui_queue.put(("idle", "Say 'Hey Jasper'"))

    print(f"Loading local Faster-Whisper {WHISPER_MODEL_NAME} model...")
    try:
        whisper_model = WhisperModel(WHISPER_MODEL_NAME, device="cpu", compute_type="int8", cpu_threads=4)
    except Exception as exc:
        print(f"[FATAL] Failed to initialize Whisper model: {exc}", file=sys.stderr)
        sys.exit(1)

    print("Initializing Gemma local brain controller...")
    gemma_controller = GemmaController()

    print("Initializing Local Action Engine...")
    action_engine = get_action_engine()

    print("Initializing Memory Store...")
    memory_store = get_memory_store()

    print("Initializing Hermes worker...")
    hermes_worker = HermesWorker(timeout=30.0)

    print("Initializing OpenCode worker...")
    opencode_worker = OpenCodeWorker()

    print("Initializing Kokoro voice & Response Coordinator...")
    try:
        kokoro_voice = KokoroVoice(voice="am_fenrir", speed=1.0)
        response_coordinator = ResponseCoordinator(kokoro_voice=kokoro_voice, ui_queue=ui_queue)
        print("Response Coordinator ready.")
    except Exception as exc:
        print(f"[WARN] Kokoro voice could not be initialized: {exc}", file=sys.stderr)

    print("Loading Vosk wake-word model...")
    try:
        vosk_model = Model(VOSK_MODEL_PATH)
        recognizer = KaldiRecognizer(vosk_model, SAMPLE_RATE)
        recognizer.SetGrammar(json.dumps(["hey", "jasper", "hey jasper", "[unk]"]))
    except Exception as exc:
        print(f"[FATAL] Failed to load Vosk model at '{VOSK_MODEL_PATH}': {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        device = sd.query_devices(MICROPHONE_DEVICE)
        print(f"Microphone: {device['name']}")
    except Exception as exc:
        print(f"[FATAL] Failed to query microphone device {MICROPHONE_DEVICE}: {exc}", file=sys.stderr)
        sys.exit(1)

    print("\n==========================================")
    print("      JASPER V1.2 VOICE ASSISTANT          ")
    print("==========================================")
    print("Say 'Hey Jasper' to activate.")
    print("Press Ctrl+C to stop.\n")

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
                    if ui_queue:
                        ui_queue.put(("listening", "Listening for command..."))

                    recognizer.Reset()
                    recent_chunks = list(wake_preroll)
                    wake_preroll.clear()

                    command_audio = capture_command(initial_chunks=recent_chunks)

                    if command_audio is not None:
                        global is_processing
                        is_processing = True
                        try:
                            process_voice_request(command_audio)
                        finally:
                            clear_audio_queue()
                            wake_preroll.clear()
                            recognizer.Reset()
                            is_processing = False
                            if ui_queue:
                                ui_queue.put(("idle", "Say 'Hey Jasper'"))
                    else:
                        clear_audio_queue()
                        wake_preroll.clear()
                        recognizer.Reset()
                        if ui_queue:
                            ui_queue.put(("idle", "Say 'Hey Jasper'"))

    except KeyboardInterrupt:
        print("\nJasper stopped safely.")
    except Exception as exc:
        print(f"[FATAL] Audio loop encountered an unhandled exception: {exc}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)


if __name__ == "__main__":
    main()
