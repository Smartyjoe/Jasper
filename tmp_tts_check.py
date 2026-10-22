"""Verify the dedicated Gemini TTS model returns playable audio."""
import os
import wave
import io

from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()
client = genai.Client(http_options={"api_version": "v1beta"},
                       api_key=os.environ.get("GEMINI_API_KEY"))

config = types.GenerateContentConfig(
    response_modalities=["AUDIO"],
    speech_config=types.SpeechConfig(
        voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Zephyr")
        )
    ),
)

resp = client.models.generate_content(
    model="gemini-2.5-flash-preview-tts",
    contents="Hello, I am Jasper, your personal assistant.",
    config=config,
)

cand = resp.candidates[0]
part = cand.content.parts[0]
data = part.inline_data.data
print("mime:", part.inline_data.mime_type, "bytes:", len(data))

w = wave.open(io.BytesIO(data), "rb")
print("channels:", w.getnchannels(), "rate:", w.getframerate(),
      "width:", w.getsampwidth(), "frames:", w.getnframes(),
      "dur:", round(w.getnframes() / w.getframerate(), 2), "s")
