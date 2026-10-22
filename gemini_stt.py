from stt_engine import STT 

stt = STT(
    target_lang='en',
    silence_threshold=1.5,
    flush=True
)

stt.connect()

while True:
    text = stt.listen()

    if 'quit' in text.lower():
        stt.close()
    else:
        print(text)