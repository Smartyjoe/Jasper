"""
Requirements: pip install google-genai pyaudio python-dotenv
"""

import os
import asyncio
import queue
import time
import sys
import threading

import pyaudio
from google import genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv()

# Global low-latency audio structures
FORMAT = pyaudio.paInt16
CHANNELS = 1
RECEIVE_SAMPLE_RATE = 24000
MODEL = "models/gemini-3.1-flash-live-preview"

_main_loop = None
_input_queue = None
_output_queue = queue.Queue()
_playback_queue = queue.Queue()
_bg_thread = None

# Set while playback should be aborted (barge-in / interrupt).
_stop_playback = threading.Event()


def interrupt_playback() -> None:
    """
    Stop audio playback immediately and discard anything still queued.

    The playback worker writes in small slices and checks this flag between
    slices, so playback stops within ~20 ms instead of after the whole buffer.
    """
    _stop_playback.set()
    while not _playback_queue.empty():
        try:
            _playback_queue.get_nowait()
            _playback_queue.task_done()
        except queue.Empty:
            break
    _stop_playback.clear()


def _global_playback_worker():
    """
    Background Audio Worker: Continually reads raw audio chunks from the queue
    and sends them to PyAudio to play sound through your speakers with zero delay.

    Audio is written in 20 ms slices so that `interrupt_playback()` can take
    effect almost immediately. The stream-open failure is logged rather than
    swallowed, so a dead worker can never make `_playback_queue.join()` hang.
    """
    pya = pyaudio.PyAudio()
    SLICE = 480  # 20 ms @ 24 kHz, 16-bit mono
    try:
        stream = pya.open(
            format=FORMAT,
            channels=CHANNELS,
            rate=RECEIVE_SAMPLE_RATE,
            output=True,
            frames_per_buffer=1024
        )
        while True:
            try:
                chunk = _playback_queue.get(timeout=0.05)
            except queue.Empty:
                continue

            for i in range(0, len(chunk), SLICE):
                if _stop_playback.is_set():
                    break
                stream.write(chunk[i:i + SLICE])
            _playback_queue.task_done()
    except Exception as exc:
        # Log rather than silently die: a dead worker turns every
        # `_playback_queue.join()` into an infinite hang.
        print(f"[ERROR] Playback worker stopped: {exc!r}", file=sys.stderr, flush=True)
    finally:
        try:
            pya.terminate()
        except Exception:
            pass


class AudioLoop:
    def __init__(self):
        """
        Constructor: Sets up session variables, completion signals, 
        and text buffers needed to track Gemini's response.
        """
        self.session = None
        self.gemini_finished = asyncio.Event()
        self.all_responses = []
        self.current_turn_text = ""

    async def receive_audio(self):
        """
        Live Stream Listener: Asynchronously processes data coming back from Gemini. 
        It streams text to the terminal live and pushes raw audio straight to the playback queue.
        """
        while True:
            if self.session is not None:
                async for response in self.session.receive():
                    if response.server_content:
                        # Output text streaming
                        if response.server_content.output_transcription:
                            chunk_text = response.server_content.output_transcription.text
                            # Do not print streamed TTS transcription into the
                            # STT/status console; it makes diagnostics look like
                            # microphone input and obscures the state machine.
                            self.current_turn_text += chunk_text
                        
                        # Handle turn completion
                        if response.server_content.turn_complete:
                            if self.current_turn_text:
                                self.all_responses.append(self.current_turn_text)
                                self.current_turn_text = ""
                            self.gemini_finished.set()
                        
                        # Audio chunk streaming to PyAudio queue
                        if response.server_content.model_turn:
                            for part in response.server_content.model_turn.parts:
                                if part.inline_data and part.inline_data.data:
                                    _playback_queue.put(part.inline_data.data)


async def background_async_loop():
    """
    Async Engine Manager: Runs the background event loop, maintains the active 
    Gemini WebSocket connection, manages input prompts, and clears leftover audio.
    """
    global _input_queue, _main_loop
    _main_loop = asyncio.get_running_loop()
    _input_queue = asyncio.Queue()
    
    client = genai.Client(
        http_options={"api_version": "v1beta"},
        api_key=os.environ.get("GEMINI_API_KEY"),
    )
    
    current_voice = "Zephyr"
    threading.Thread(target=_global_playback_worker, daemon=True).start()
    
    while True:
        CONFIG = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            output_audio_transcription=types.AudioTranscriptionConfig(),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=current_voice)
                )
            ),
        )
        
        loop_instance = AudioLoop()
        
        try:
            async with (
                client.aio.live.connect(model=MODEL, config=CONFIG) as session,
                asyncio.TaskGroup() as tg,
            ):
                loop_instance.session = session
                tg.create_task(loop_instance.receive_audio())
                
                while True:
                    item = await _input_queue.get()
                    next_query, next_voice = item
                    
                    if next_voice != current_voice:
                        current_voice = next_voice
                        await _input_queue.put(item)
                        raise Exception("Reconnecting to update voice config")
                        
                    # Flush lingering audio before new request
                    while not _playback_queue.empty():
                        try:
                            _playback_queue.get_nowait()
                            _playback_queue.task_done()
                        except queue.Empty:
                            break
                            
                    loop_instance.gemini_finished.clear()
                    await loop_instance.session.send_realtime_input(text=next_query or ".")
                    await loop_instance.gemini_finished.wait()
                    
                    _output_queue.put(loop_instance.all_responses[-1] if loop_instance.all_responses else "")
                    _input_queue.task_done()
                    
        except Exception:
            await asyncio.sleep(0.1)


def start_background_thread():
    """
    Thread Launcher: Initializes a separate thread dedicated to running 
    the asyncio event loop so the main program doesn't freeze or lock up.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(background_async_loop())


def TTS(query, voice="Zephyr", timeout: float = 30.0):
    """
    Text-To-Speech Interface: Prepares the prompt for repetition and sends it to
    the background thread.

    The previous version ended by emitting ANSI escape sequences to erase the
    streamed transcription from the terminal. That transcription is no longer
    printed, so those escapes were wiping unrelated console output (the
    "[JASPER]" lines, the "Jasper:" replies and everything below the cursor).
    They have been removed.

    A timeout is applied to the result wait: if the Live session drops, the
    call used to block forever.
    """
    formatted_query = f"Repeat the following text exactly. Output nothing else:\n{query}"

    global _bg_thread, _main_loop, _input_queue
    if _bg_thread is None:
        _bg_thread = threading.Thread(target=start_background_thread, daemon=True)
        _bg_thread.start()
        while _main_loop is None or _input_queue is None:
            time.sleep(0.01)

    _main_loop.call_soon_threadsafe(_input_queue.put_nowait, (formatted_query, voice))
    try:
        final_response = _output_queue.get(timeout=timeout)
    except queue.Empty:
        print(f"[ERROR] TTS timed out after {timeout}s", file=sys.stderr, flush=True)
        interrupt_playback()
        return ""

    return final_response


if __name__ == '__main__':
    while True:
        query = input("\nTTS: ")
        output = TTS(query, "Zephyr")
        print(f"Final Return: {output}")
