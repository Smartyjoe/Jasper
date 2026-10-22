import json, subprocess, sys, os

# Test Kokoro generation via subprocess
args_json = json.dumps({
    "model": r"C:\Users\USER\kokoro-demo\kokoro-v1.0.onnx",
    "voices": r"C:\Users\USER\kokoro-demo\voices-v1.0.bin",
    "text": "Hello! I am Jasper, your personal assistant.",
    "voice": "am_fenrir",
    "speed": 1.0,
    "output": r"C:\Users\USER\kenny\test_output.wav",
})

result = subprocess.run(
    [r"C:\Users\USER\kokoro-demo\venv\Scripts\python.exe",
     r"C:\Users\USER\kokoro-demo\gen_script.py", args_json],
    capture_output=True, text=True, encoding="utf-8", timeout=30,
)
print("returncode:", result.returncode)
print("stdout:", result.stdout)
print("stderr:", result.stderr)