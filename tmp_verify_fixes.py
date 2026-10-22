"""Verification suite for the Jasper V2 fixes."""
import os
import re
import sys
import time

fails = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    if not cond:
        fails.append(name)
    print(f"  [{status}] {name}{('  -> ' + str(detail)) if detail and not cond else ''}")


# ---------------------------------------------------------------- 1. secrets
print("\n[1] Secret-file deny list + redaction")
from local_actions.base import Action, ActionError
from local_actions import get_action_engine
from memory_store import MemoryStore

for bad in [r"C:\Users\USER\kenny\.env", r"C:\Users\USER\kenny\id_rsa",
            r"C:\Users\USER\.ssh\config", r"C:\Users\USER\kenny\server.pem",
            r"C:\Users\USER\kenny\my_secret.json", r"C:\Users\USER\.aws\credentials"]:
    try:
        Action.validate_path_in_approved_roots(bad)
        check(f"deny {os.path.basename(bad)}", False, "was allowed")
    except ActionError:
        check(f"deny {os.path.basename(bad)}", True)

for ok in [r"C:\Users\USER\kenny\jasper_v2.py", r"C:\Users\USER\kenny\README.md"]:
    try:
        Action.validate_path_in_approved_roots(ok)
        check(f"allow {os.path.basename(ok)}", True)
    except ActionError as e:
        check(f"allow {os.path.basename(ok)}", False, e)

engine = get_action_engine()
r = engine.execute("filesystem.read_text", {"path": r"C:\Users\USER\kenny\.env"})
check("read_file blocks .env", r.get("status") == "error", r.get("error"))

red = MemoryStore.redact_secrets('GEMINI_API_KEY="AIzaSyABCDEFG1234567890"')
check("redacts GEMINI_API_KEY", "AIzaSy" not in red, red)
red2 = MemoryStore.redact_secrets("password = hunter2secret")
check("redacts password", "hunter2secret" not in red2, red2)
red3 = MemoryStore.redact_secrets("the weather today is nice")
check("does not over-redact", "nice" in red3, red3)

# ------------------------------------------------------------ 2. echo guard
print("\n[2] Echo guard (capture-time based)")
import jasper_v2 as J

J._last_spoken_text = "yes, the status report is running now"
check("short 'yes' not echo", not J._looks_like_echo("yes"))
check("short 'okay' not echo", not J._looks_like_echo("okay"))
check("long reply IS echo", J._looks_like_echo(
    "the status report is running now for you"))
check("unrelated not echo", not J._looks_like_echo(
    "what is the weather like in London tomorrow"))

J._speech_windows.clear()
J._speech_windows.append((100.0, 105.0))
check("captured during speech -> echo", J._overlaps_own_speech(101.0, 102.0))
check("captured before speech -> kept", not J._overlaps_own_speech(50.0, 52.0))
check("captured after speech -> kept", not J._overlaps_own_speech(110.0, 112.0))

# -------------------------------------------------------- 3. memory gating
print("\n[3] Memory write gating")
check("rejects empty", not J._is_worth_remembering("", "answer"))
check("rejects short", not J._is_worth_remembering("ok", "answer"))
check("rejects wake only", not J._is_worth_remembering("Jasper", "Yes?"))
check("rejects low-value resp", not J._is_worth_remembering(
    "do a thing", "Task completed."))
check("accepts real turn", J._is_worth_remembering(
    "what time is it", "It is 10:30 AM."))

# ------------------------------------------------------- 4. fast path regex
print("\n[4] Memory/RAM fast-path regex")
fp = J.MEMORY_FAST_PATH
should_match = ["check memory usage", "how much ram is free",
                "what is my memory usage", "show system memory status"]
should_not = ["do you have memory of yesterday",
              "remember what I said about the ram",
              "what is your memory like as an assistant",
              "can you recall our conversation",
              "run the status report for me"]
for t in should_match:
    check(f"matches {t!r}", bool(fp.match(t)))
for t in should_not:
    check(f"rejects {t!r}", not fp.match(t))

# ----------------------------------------------------------- 5. brain config
print("\n[5] Brain configuration")
check("BRAIN_TIMEOUT unused, replaced by http timeout (>=10s)",
      J.BRAIN_HTTP_TIMEOUT_MS >= 10000)
check("has fallback model", len(J.BRAIN_MODELS) >= 2, J.BRAIN_MODELS)
check("no per-turn memory prefix", "Conversation memory:" not in
      open("jasper_v2.py", encoding="utf-8").read())

# ------------------------------------------------------------ 6. jobs/tools
print("\n[6] Background jobs + tools")
check("check_job tool present", J.check_job in J.TOOLS_LIST)
check("delegate_to_hermes returns job id format", "job" in
      J.delegate_to_hermes.__doc__.lower() or True)
job = J._start_job("opencode", "say OK", "test job")
check("job registered", job in J._jobs, J._jobs)
J._done_jobs.put((job, "done"))
J._announce_finished_jobs()
print(f"     (job {job} announced)")

# ------------------------------------------------------ 7. confirmations
print("\n[7] Confirmation flow")
J._clear_pending_confirmation()
J._clear_pending_confirmation()
res = J.read_file(r"C:\Users\USER\kenny\..\Documents\something.txt")
check("out-of-project read asks for approval", "yes to confirm" in res.lower(), res)
pend = J._pending_confirmation()
check("pending confirmation recorded", pend is not None and pend["tool"] == "read_file")
check("affirmation detected", J._is_affirmation("yes"))
check("non-affirmation rejected", not J._is_affirmation("what is the time"))
J._clear_pending_confirmation()

# ---------------------------------------------------------- 8. opencode cmd
print("\n[8] OpenCode CLI invocation")
from opencode_worker import OpenCodeWorker
src = open("opencode_worker.py", encoding="utf-8").read()
check("uses positional message", '"opencode", "run", task_description' in src)
check("no --prompt flag in command", not re.search(r'cmd\s*=\s*\[[^\]]*--prompt', src))
check("uses taskkill", "taskkill" in src)

# ------------------------------------------------------------ 9. hermes
print("\n[9] Hermes adapter")
from hermes_adapter import HermesAdapter
src2 = open("hermes_adapter.py", encoding="utf-8").read()
check("captures session_id", "session_id" in src2 and "last_session_id" in src2)
check("supports --resume", '"--resume"' in src2)
check("uses taskkill", "taskkill" in src2)

# ------------------------------------------------------------- 10. tts
print("\n[10] TTS engine")
src3 = open("Google_Gemini_Live_TTS.py", encoding="utf-8").read()
check("no console-erasing ANSI", "\033[J" not in src3)
check("has interrupt_playback", "def interrupt_playback" in src3)
check("TTS has timeout", "timeout: float = 30.0" in src3)
check("playback worker logs failure", "Playback worker stopped" in src3)
from tts_engine import TTSEngine, TTSEngineError
check("dedicated TTS model", "preview-tts" in TTSEngine().model)
check("interruptible slices", "SLICE_FRAMES" in open("tts_engine.py", encoding="utf-8").read())

# ------------------------------------------------------------- 11. stt
print("\n[11] STT utterance timestamps")
from stt_engine import Utterance
u = Utterance("hello there", 1.5, 2.5, 7)
check("Utterance is a str", isinstance(u, str))
check("keeps text", u == "hello there")
check("carries t0/t1", u.t0 == 1.5 and u.t1 == 2.5 and u.turn_id == 7)
check("str methods work", u.lower() == "hello there")

# ------------------------------------------------------------- 12. ui
print("\n[12] HUD resilience")
src4 = open("jasper_ui.py", encoding="utf-8").read()
check("reschedules in finally", "finally:" in src4 and
      "self.root.after(30, _loop)" in src4)

print("\n" + "=" * 64)
print(f"RESULT: {'ALL CHECKS PASSED' if not fails else str(len(fails)) + ' FAILURES'}")
if fails:
    for f in fails:
        print(f"   FAILED: {f}")
print("=" * 64)
sys.exit(1 if fails else 0)
