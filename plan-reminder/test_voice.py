import os
import asyncio
from groq import Groq
from dotenv import load_dotenv

load_dotenv("c:\\Users\\HP\\OneDrive\\Рабочий стол\\remonder bptAI bot\\.env")

async def test_transcription():
    try:
        from pathlib import Path
        import urllib.request
        # download a small sample ogg file
        urllib.request.urlretrieve("https://upload.wikimedia.org/wikipedia/commons/c/c8/Example.ogg", "test.ogg")
        groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))
        with open("test.ogg", "rb") as f:
            transcription = groq_client.audio.transcriptions.create(
                file=("test.ogg", f.read()),
                model="whisper-large-v3",
                response_format="json"
            )
        print("Success:", transcription.text)
    except Exception as e:
        print("Error:", e)

if __name__ == "__main__":
    asyncio.run(test_transcription())
