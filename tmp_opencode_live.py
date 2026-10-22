"""Live end-to-end test of the fixed OpenCode worker."""
from opencode_worker import OpenCodeWorker

w = OpenCodeWorker(timeout=180)
res = w.run_task("Reply with exactly the word: PONG")
print("status  :", res["status"])
print("duration:", res["duration_sec"], "s")
print("error   :", res.get("error"))
print("result  :", str(res["result"])[:400])
