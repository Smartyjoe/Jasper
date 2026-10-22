# Jasper V1 — Full Implementation Plan

> **Hand this document to the implementing agent verbatim. It contains every fact,
> path, command, and constraint needed to complete V1 without guessing.**

---

## 0. Agent Prompt — How to Execute This Plan

You are implementing **Jasper V1**, a fully local, Tony-Stark-standard voice
assistant running on Windows. Before writing a single line of code, read this
entire file. Your job is to implement the two outstanding milestones (Hermes
brain + Kokoro voice) by following the step-by-step guide in Section 6.

### Non-negotiable rules

| Rule | Rationale |
|------|-----------|
| Do **not** reinstall Python, Vosk, Faster-Whisper, Ollama, Hermes, or Kokoro. | Everything is already installed and working. |
| Do **not** change Hermes providers, Gemini Rotator config, or any global Hermes settings. | These are shared production settings. |
| Do **not** add computer-control logic triggered by transcript text. | Transcript intelligence is metadata-only, never an executor. |
| Keep Windows PowerShell compatibility throughout. | The user's shell is PowerShell; bash-only scripts will not run. |
| Commit each milestone to Git before starting the next. | Rollback safety net. |
| Add explicit `[ERROR]` / `[FATAL]` log lines to every exception path. | No silent failures ever. |
| Test each integration point in isolation before wiring it into the main loop. | Prevents compound debugging sessions. |

### How to verify your work before each commit

1. Run the module's own test (if one exists) from the project root using the `.venv-stt` interpreter.
2. Run `jasper_voice_loop.py` end-to-end and say "Hey Jasper, what time is it?"
3. Confirm Hermes answers and Kokoro speaks the answer aloud.
4. Confirm Jasper returns to listening mode and the microphone is not blocked.

---

## 1. Project State Snapshot

### 1.1 Directory layout (as of last session)

```
C:\Users\USER\kenny\
├── .git/
├── .gitignore                        <- comprehensive, committed
├── .venv-stt/                        <- Python virtual env (DO NOT MODIFY)
├── models/
│   └── vosk-model-small-en-us-0.15/ <- Vosk wake-word model
├── jasper_voice_loop.py              <- MAIN entry point  (see S2)
├── transcript_enhancer.py            <- Qwen intelligence layer (see S2)
├── test_transcript_enhancer.py       <- automated unit tests
├── stt_test.py                       <- legacy microphone test (ignore)
├── wake_word.py                      <- legacy wake-word prototype (ignore)
└── jasper_last_command.wav           <- auto-saved last command (gitignored)
```

### 1.2 External assets (outside the project root)

| Asset | Path | Notes |
|-------|------|-------|
| Kokoro ONNX model | `C:\Users\USER\kokoro-demo\kokoro-v1.0.onnx` | 155 MB |
| Kokoro voice data | `C:\Users\USER\kokoro-demo\voices-v1.0.bin` | 27 MB |
| Kokoro voice packs | `C:\Users\USER\kokoro-demo\voices\am_fenrir.bin` | Male voice |
| Kokoro voice packs | `C:\Users\USER\kokoro-demo\voices\af_heart.bin` | Female voice |
| Kokoro Python env | `C:\Users\USER\kokoro-demo\venv\Scripts\python.exe` | Has kokoro_onnx, soundfile |
| Kokoro entry point | `C:\Users\USER\kokoro-demo\hello.py` | Reference implementation |
| Hermes binary | `hermes` (on system PATH) | Global CLI agent |

---

## 2. Architecture — The Complete V1 Pipeline

```
Microphone  -->  Vosk        -->  Faster-Whisper  -->  Qwen (Ollama)
(16 kHz)         wake-word        base.en/small.en     transcript intel
                 "Hey Jasper"     raw transcript        corrected + intent
                                       |
                                       v
                                  Hermes (CLI)       -->  Kokoro TTS
                                  reasoning engine        am_fenrir voice
                                  returns plain text      plays WAV aloud
                                       |
                                       v
                               Jasper returns to
                               wake-word listener
```

### 2.1 Data flow between modules (what each module returns)

| Stage | Input | Output |
|-------|-------|--------|
| `capture_command()` | mic audio | `np.float32` array |
| `transcribe_command()` | float32 array | `str` raw transcript |
| `TranscriptEnhancer.enhance()` | raw str | dict `{corrected_transcript, intent_summary}` |
| `HermesAdapter.ask()` | corrected str | `str` plain-text answer |
| `KokoroVoice.speak()` | answer str | plays audio, returns when done |

### 2.2 `is_processing` flag — critical coordination

`is_processing = True` **must** be set in `jasper_voice_loop.py` for the entire
duration from the moment Whisper starts until Kokoro finishes speaking. This
suppresses the microphone callback so that:
- PortAudio buffer overflow warnings disappear.
- Jasper cannot hear its own synthesized speech and trigger a wake-word loop.

Current code already does this for Whisper + Qwen. The flag scope must be
**extended** to cover the Hermes call and Kokoro playback.

---

## 3. Existing Code Reference

### 3.1 `jasper_voice_loop.py` — key constants

```python
WHISPER_MODEL_NAME  = os.getenv("JASPER_WHISPER_MODEL", "small.en")
VOICE_THRESHOLD     = 350      # RMS
SILENCE_SECONDS     = 1.2
COMMAND_TIMEOUT     = 5.0
MAX_COMMAND_SECONDS = 15.0
PREROLL_BLOCKS      = 8        # ~400 ms
OLLAMA_ENDPOINT     = "http://localhost:11434"
ENHANCER_TIMEOUT    = 6.0
is_processing       = False    # global coordination flag
```

### 3.2 `jasper_voice_loop.py` — main loop structure (simplified)

```python
if wake_detected:
    command_audio = capture_command(initial_chunks=recent_chunks)
    if command_audio is not None:
        is_processing = True
        try:
            transcribe_command(command_audio)   # <- Hermes + Kokoro go HERE
        finally:
            clear_audio_queue()
            recognizer.Reset()
            is_processing = False
```

All new code (Hermes query, Kokoro speak) belongs **inside** `transcribe_command()`
after the Qwen enhancement block, within the existing `is_processing = True` guard.

### 3.3 `transcript_enhancer.py` — what it returns

```python
{
    "raw_transcript":       "what time is it",
    "corrected_transcript": "What time is it?",
    "intent_summary":       "Asking for the current time",
    "uncertainties":        []
}
```

The **`corrected_transcript`** value is what goes to Hermes. Fall back to
`raw_transcript` if `corrected_transcript` is empty.

### 3.4 Kokoro `hello.py` reference — exact integration pattern

```python
import numpy as np

# CRITICAL: Must apply before importing kokoro_onnx
_orig_load = np.load
np.load = lambda *args, **kwargs: _orig_load(*args, **dict(kwargs, allow_pickle=True))

from kokoro_onnx import Kokoro
import soundfile as sf

kokoro = Kokoro("kokoro-v1.0.onnx", "voices-v1.0.bin")
samples, sample_rate = kokoro.create(
    "Hello! The Kokoro text-to-speech model is working perfectly.",
    voice="am_fenrir",
    speed=1.0,
    lang="en-us"
)
sf.write("output.wav", samples, sample_rate)
```

---

## 4. Hermes CLI Interface Reference

### 4.1 Verified CLI signature

```
hermes chat --query-file - -Q --source tool
```

| Flag | Effect |
|------|--------|
| `--query-file -` | Reads the query from **stdin** (safe for any text, no shell quoting issues) |
| `-Q` | Quiet mode — answer and exit (non-interactive, no ANSI prompts) |
| `--source tool` | Marks this turn as a tool call in Hermes session logs |

### 4.2 Output format

Hermes writes to **stdout**. The format is:

```
session_id: <timestamp_based_id>
<blank line or not>
<Final Assistant Response — plain text, possibly multi-line>
```

Strip the `session_id:` header line and any leading blank lines before passing
the answer to Kokoro.

### 4.3 Subprocess invocation pattern (Python)

```python
import subprocess

result = subprocess.run(
    ["hermes", "chat", "--query-file", "-", "-Q", "--source", "tool"],
    input=query_text,
    capture_output=True,
    text=True,
    encoding="utf-8",
    timeout=30,
)
```

- Check `result.returncode == 0` before using `result.stdout`.
- Log `result.stderr` at `[WARN]` if non-empty.
- Raise/log `[ERROR]` on non-zero returncode.

---

## 5. Implementation Constraints & Guard Rails

| Constraint | Detail |
|------------|--------|
| Python interpreter for Jasper | `.venv-stt\Scripts\python.exe` |
| Python interpreter for Kokoro | `C:\Users\USER\kokoro-demo\venv\Scripts\python.exe` |
| Kokoro **must not** run in `.venv-stt` | It has its own isolated environment. |
| Hermes runs as a subprocess | Not imported as a Python library. |
| Ollama endpoint | `http://localhost:11434` (local, no auth) |
| Hermes default provider | **Do not specify `-m` or `--provider`**. Use Hermes's own configured default. |
| Audio playback library | Use `sounddevice.play()` + `sounddevice.wait()`. `sounddevice` is already in `.venv-stt`. |
| Kokoro output path | Write to `jasper_response.wav` in project root (gitignored). |
| Max Hermes timeout | 30 seconds. If exceeded: log `[WARN]`, speak fallback phrase, return to listening. |
| Kokoro subprocess timeout | 20 seconds. Same fallback pattern. |

---

## 6. Step-by-Step Implementation Guide

> Work through these steps **in order**. Do not skip steps. Commit after each milestone.

---

### STEP 1 — Verify environment integrity

Run these checks first. Do not continue if any fails.

```powershell
# 1a. Verify .venv-stt Python
.\.venv-stt\Scripts\python.exe -c "import sounddevice, faster_whisper, vosk, numpy; print('venv-stt OK')"

# 1b. Verify Kokoro Python env
C:\Users\USER\kokoro-demo\venv\Scripts\python.exe -c "from kokoro_onnx import Kokoro; print('Kokoro OK')"

# 1c. Verify Hermes is on PATH
hermes --version

# 1d. Verify Ollama + Qwen are running
Invoke-RestMethod -Uri http://localhost:11434/api/tags | ConvertTo-Json
```

**Expected outcomes:**
- 1a prints `venv-stt OK`.
- 1b prints `Kokoro OK`.
- 1c prints a version string.
- 1d shows JSON with a `models` array containing an entry with `qwen` in its name.

---

### STEP 2 — Create `hermes_adapter.py`

Create `C:\Users\USER\kenny\hermes_adapter.py`.

**Required public API:**

```python
class HermesError(Exception):
    """Raised when Hermes subprocess fails or times out."""

class HermesAdapter:
    def __init__(self, timeout: float = 30.0): ...

    def ask(self, query: str) -> str:
        """
        Send `query` to Hermes and return the plain-text answer.
        Raises HermesError on failure.
        """
```

**Full implementation specification:**

1. Call `subprocess.run(["hermes", "chat", "--query-file", "-", "-Q", "--source", "tool"], input=query, capture_output=True, text=True, encoding="utf-8", timeout=self.timeout)`.
2. If `returncode != 0`: log `[ERROR] Hermes exited with code {returncode}: {result.stderr}` then raise `HermesError`.
3. If `result.stderr` is non-empty and returncode is 0: log `[WARN] Hermes stderr: {result.stderr}`.
4. Parse stdout: split into lines, drop any line starting with `session_id:`, join remaining non-empty lines with `\n`.
5. Return the cleaned answer string.
6. Catch `subprocess.TimeoutExpired`: log `[WARN] Hermes timed out after {self.timeout}s`, raise `HermesError("Hermes timed out")`.
7. Catch `FileNotFoundError` / `OSError`: log `[ERROR] hermes binary not found on PATH`, raise `HermesError("hermes not found")`.

**Manual smoke test (run before moving on):**

```powershell
.\.venv-stt\Scripts\python.exe -c "
from hermes_adapter import HermesAdapter
h = HermesAdapter()
answer = h.ask('What is 2 + 2?')
print('Answer:', answer)
"
```

Expected: Hermes returns an answer containing "4".

---

### STEP 3 — Create `kokoro_voice.py`

Create `C:\Users\USER\kenny\kokoro_voice.py`.

**Required public API:**

```python
class KokoroVoiceError(Exception):
    """Raised when TTS generation or playback fails."""

class KokoroVoice:
    KOKORO_PYTHON = r"C:\Users\USER\kokoro-demo\venv\Scripts\python.exe"
    KOKORO_DIR    = r"C:\Users\USER\kokoro-demo"
    OUTPUT_WAV    = r"C:\Users\USER\kenny\jasper_response.wav"
    DEFAULT_VOICE = "am_fenrir"

    def __init__(self, voice: str = DEFAULT_VOICE, speed: float = 1.0): ...

    def speak(self, text: str) -> None:
        """
        Generate speech for `text` and play it to the default audio device.
        Blocks until playback is complete.
        Raises KokoroVoiceError on generation or playback failure.
        """
```

**Why subprocess for Kokoro?**
`kokoro_onnx` applies a global `numpy.load` monkey-patch that would contaminate
`.venv-stt`. A child process keeps the two environments completely isolated.

**Inline generation script** — pass via `python.exe -c <script> <json_args>`:

```python
KOKORO_GEN_SCRIPT = r"""
import sys, numpy as np, json
_orig = np.load
np.load = lambda *a, **kw: _orig(*a, **dict(kw, allow_pickle=True))
from kokoro_onnx import Kokoro
import soundfile as sf
args = json.loads(sys.argv[1])
kokoro = Kokoro(args['model'], args['voices'])
samples, sr = kokoro.create(
    args['text'], voice=args['voice'], speed=args['speed'], lang='en-us'
)
sf.write(args['output'], samples, sr)
print('OK')
"""
```

**Generation call:**

```python
import json, subprocess
args_json = json.dumps({
    "model":  r"C:\Users\USER\kokoro-demo\kokoro-v1.0.onnx",
    "voices": r"C:\Users\USER\kokoro-demo\voices-v1.0.bin",
    "text":   text,
    "voice":  self.voice,
    "speed":  self.speed,
    "output": self.OUTPUT_WAV,
})
result = subprocess.run(
    [self.KOKORO_PYTHON, "-c", KOKORO_GEN_SCRIPT, args_json],
    capture_output=True, text=True, encoding="utf-8", timeout=20,
)
if result.returncode != 0:
    raise KokoroVoiceError(f"Kokoro generation failed: {result.stderr}")
```

**Playback** (in `.venv-stt` after generation):

```python
import sounddevice as sd
import soundfile as sf

data, samplerate = sf.read(self.OUTPUT_WAV, dtype="float32")
sd.play(data, samplerate)
sd.wait()
```

**Error handling:**
- Catch `subprocess.TimeoutExpired`: raise `KokoroVoiceError("Kokoro timed out")`.
- Catch `FileNotFoundError`: raise `KokoroVoiceError("Kokoro Python env not found")`.
- Catch any `sf.SoundFileError` or `sd` exception during playback: raise `KokoroVoiceError`.

**Manual smoke test:**

```powershell
.\.venv-stt\Scripts\python.exe -c "
from kokoro_voice import KokoroVoice
kv = KokoroVoice()
kv.speak('Hello! I am Jasper, your personal assistant.')
print('Playback complete.')
"
```

Expected: Jasper speaks the phrase aloud through speakers.

---

### STEP 4 — Wire Hermes + Kokoro into `jasper_voice_loop.py`

**4a. Add imports** at the top after existing imports:

```python
from hermes_adapter import HermesAdapter, HermesError
from kokoro_voice import KokoroVoice, KokoroVoiceError
```

**4b. Add globals** after `transcript_enhancer = None`:

```python
hermes_adapter = None
kokoro_voice = None
```

**4c. Initialize in `main()`** after the transcript_enhancer block:

```python
print("Initializing Hermes brain adapter...")
try:
    hermes_adapter = HermesAdapter(timeout=30.0)
    print("Hermes adapter ready.")
except Exception as exc:
    print(f"[WARN] Hermes adapter could not be initialized: {exc}", file=sys.stderr)

print("Initializing Kokoro voice...")
try:
    kokoro_voice = KokoroVoice(voice="am_fenrir", speed=1.0)
    print("Kokoro voice ready.")
except Exception as exc:
    print(f"[WARN] Kokoro voice could not be initialized: {exc}", file=sys.stderr)
```

**4d. Fix variable scoping in `transcribe_command()`** — before the Qwen try block,
initialise `corrected` so it is always defined:

```python
corrected = command_text   # <- add this line before the if block
intent = ""

if command_text and ENABLE_TRANSCRIPT_INTELLIGENCE and transcript_enhancer:
    try:
        ...
        corrected = enhanced.get("corrected_transcript") or command_text
        ...
```

**4e. Add Hermes + Kokoro block** immediately after the intelligence block (after
the `[DEBUG] Intelligence latency` print):

```python
# -- Hermes reasoning --
final_query = corrected if corrected else command_text
hermes_answer = None

if hermes_adapter and final_query:
    try:
        print(f"[DEBUG] Sending to Hermes: {final_query!r}")
        hermes_start = time.monotonic()
        hermes_answer = hermes_adapter.ask(final_query)
        print(f"Jasper: {hermes_answer}")
        print(f"[DEBUG] Hermes latency: {time.monotonic() - hermes_start:.2f}s")
    except HermesError as herr:
        print(f"[WARN] Hermes failed: {herr}", file=sys.stderr)
        hermes_answer = "Sorry, I couldn't reach my brain right now."
    except Exception as exc:
        print(f"[ERROR] Unexpected Hermes error: {exc}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        hermes_answer = "Sorry, something went wrong."

# -- Kokoro TTS --
if kokoro_voice and hermes_answer:
    try:
        tts_start = time.monotonic()
        kokoro_voice.speak(hermes_answer)
        print(f"[DEBUG] TTS latency: {time.monotonic() - tts_start:.2f}s")
    except KokoroVoiceError as kve:
        print(f"[WARN] Kokoro TTS failed: {kve}", file=sys.stderr)
    except Exception as exc:
        print(f"[ERROR] Unexpected TTS error: {exc}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
```

---

### STEP 5 — Update `.gitignore`

Verify `jasper_response.wav` is covered. The existing rule `*.wav` covers it.
If for any reason it is missing, add:

```
jasper_response.wav
```

---

### STEP 6 — End-to-end test

```powershell
.\.venv-stt\Scripts\python.exe .\jasper_voice_loop.py
```

Say: **"Hey Jasper, what is the capital of France?"**

**Expected console sequence:**

```
Loading local Faster-Whisper small.en model...
Transcript intelligence ready (Model: qwen3-0.6b:latest)
Hermes adapter ready.
Kokoro voice ready.
Loading Vosk wake-word model...
Microphone: Microphone Array (Realtek Audio)

JASPER VOICE LOOP
Say 'Hey Jasper' to activate.

Heard (partial): hey jasper

Wake phrase detected!

Jasper is listening. Speak your command now.
[DEBUG] Captured 2.5s of audio ...
Transcribing locally...

Jasper heard (raw): What is the capital of France?
[DEBUG] STT latency: 1.8s
Intent summary:     Asking for the capital of France
[DEBUG] Intelligence latency: 3.2s
[DEBUG] Sending to Hermes: 'What is the capital of France?'
Jasper: Paris is the capital of France.
[DEBUG] Hermes latency: 4.5s
[DEBUG] TTS latency: 6.1s
Ready for the next wake phrase.
```

Jasper's voice must speak the answer aloud.

---

### STEP 7 — Git commit

```powershell
cd C:\Users\USER\kenny
git add hermes_adapter.py kokoro_voice.py jasper_voice_loop.py
git commit -m "feat: connect Hermes brain and Kokoro voice (V1 complete)"
git push
```

---

## 7. Error Handling Matrix

| Failure | Log level | Fallback behaviour |
|---------|-----------|-------------------|
| Hermes binary not found | `[ERROR]` | Warn at startup; skip Hermes calls silently |
| Hermes timeout >30s | `[WARN]` | Speak: "Sorry, I couldn't reach my brain." |
| Hermes non-zero exit | `[ERROR]` | Speak: "Sorry, something went wrong." |
| Kokoro Python not found | `[ERROR]` | Warn at startup; skip TTS |
| Kokoro timeout >20s | `[WARN]` | Log and return to listening |
| Kokoro WAV write failure | `[ERROR]` | Log and return to listening |
| `sd.play()` failure | `[ERROR]` | Log and return to listening |
| Ollama timeout >6s | `[WARNING]` | Use raw STT transcript (existing) |
| Whisper failure | `[ERROR]` | Return to wake-word loop (existing) |

---

## 8. Performance Expectations

| Stage | Typical latency |
|-------|----------------|
| Faster-Whisper `small.en` (CPU int8) | 1.5–2.5 s |
| Qwen transcript enhancement | 3–6 s |
| Hermes reasoning | 4–15 s |
| Kokoro TTS generation | 4–8 s |
| Kokoro WAV playback | real-time |
| **Total wake-to-speech** | **~15–35 s** |

---

## 9. File Checklist — V1 Complete State

| File | Status |
|------|--------|
| `jasper_voice_loop.py` | Existing — extend in Step 4 |
| `transcript_enhancer.py` | Complete — do not modify |
| `hermes_adapter.py` | **Create in Step 2** |
| `kokoro_voice.py` | **Create in Step 3** |
| `test_transcript_enhancer.py` | Existing tests must still pass |
| `.gitignore` | Verify `*.wav` is listed |
| `v1-plan.md` | This document |

---

## 10. Out of Scope for V1

- Streaming TTS.
- GPU-accelerated Whisper.
- Persistent Hermes session memory across invocations.
- Web or REST API for Jasper.
- Home-automation or computer-control tool calls.
- Windows tray icon or system service wrapper.
- Wake-word sensitivity tuning.

---

## 11. Glossary

| Term | Definition |
|------|-----------|
| `.venv-stt` | Project Python virtual env. Contains Vosk, Faster-Whisper, sounddevice, numpy. |
| Hermes | CLI-based AI agent. Runs as a subprocess. Existing providers must not be changed. |
| Kokoro | ONNX-based local TTS. Runs inside `C:\Users\USER\kokoro-demo\venv`. |
| Qwen / Transcript Intelligence | Small local LLM (qwen3-0.6b) via Ollama. Metadata-only — never executes commands. |
| `is_processing` | Global boolean in `jasper_voice_loop.py`. Suppresses the microphone callback during all AI inference and audio playback. |
| PREROLL | ~400 ms of audio before wake-word trigger, prepended to command capture to preserve first syllable. |
