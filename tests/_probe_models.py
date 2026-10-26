"""Probe several Gemini model IDs with a tiny request to see which respond quickly."""
import os
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
key = os.getenv("GEMINI_API_KEY")

CANDIDATES = [
    "gemini-flash-latest",
    "gemini-flash-lite-latest",
    "gemini-pro-latest",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.6-flash",
    "gemini-3.7-flash",
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash-lite",
]

for mdl in CANDIDATES:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{mdl}:generateContent?key={key}"
    payload = {
        "contents": [{"role": "user", "parts": [{"text": "Reply with just: ok"}]}],
        "generationConfig": {"maxOutputTokens": 20, "temperature": 0},
    }
    try:
        r = requests.post(url, json=payload, timeout=20)
        if r.status_code == 200:
            text = ""
            try:
                text = r.json()["candidates"][0]["content"]["parts"][0]["text"][:40]
            except Exception:
                text = str(r.json())[:60]
            print(f"OK   {mdl}: {text!r}")
        else:
            print(f"HTTP {r.status_code} {mdl}: {r.text[:140]}")
    except Exception as e:
        print(f"TOUT {mdl}: {e.__class__.__name__}")
