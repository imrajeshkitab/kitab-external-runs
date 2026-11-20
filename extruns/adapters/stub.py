"""Stand-in adapters so the whole loop runs before the real normaliser and model are plugged in.

normalise: copies the narratable text, writes ASCII digits in the language's own digit characters (English: as
words, 0-99) and drops symbols. synthesise: writes silence, about one second per 150 characters (so local runs are
quick), and a report with chapter times. Neither is a real result; both are clearly marked in their output.
"""
from __future__ import annotations

import re
import time
import wave
from pathlib import Path

ZERO = {"hindi": 0x966, "marathi": 0x966, "bengali": 0x9E6, "gujarati": 0xAE6, "odia": 0xB66, "tamil": 0xBE6,
        "telugu": 0xC66, "kannada": 0xCE6, "malayalam": 0xD66}
ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()
STEP_SECONDS = float(__import__("os").environ.get("EXTRUNS_STUB_SECONDS", "2"))


def _en(n: int) -> str:
    if n < 20:
        return ONES[n]
    if n < 100:
        return TENS[n // 10] + ("" if n % 10 == 0 else "-" + ONES[n % 10])
    return " ".join(ONES[int(d)] for d in str(n))


def _spoken(text: str, lang: str) -> str:
    if lang in ZERO:
        text = "".join(chr(ZERO[lang] + int(ch)) if ch.isascii() and ch.isdigit() else ch for ch in text)
    else:
        text = re.sub(r"\d+", lambda m: _en(int(m.group())), text)
    text = text.replace("%", " percent" if lang == "english" else "")
    return re.sub(r"[#@&*_=+<>\[\]{}|\\/~^$€£₹]", "", text)


def normalise(job: dict) -> dict:
    time.sleep(STEP_SECONDS)
    lang = job["language"]
    return {"stub": True, "chapters": [{"index": c["index"], "kind": c["kind"], "heading": _spoken(c["heading"], lang),
                                        "text": _spoken(c["text"], lang)} for c in job["narratable"]["chapters"]]}


def synthesise(job: dict, out_dir: Path) -> dict:
    time.sleep(STEP_SECONDS)
    chapters, t, rate = [], 0.0, 8000
    for c in job["speakable"]["chapters"]:
        d = max(1.0, (len(c["heading"]) + len(c["text"])) / 150)
        chapters.append({"index": c["index"], "start_s": round(t, 2), "end_s": round(t + d, 2)})
        t += d
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "audio.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(1); w.setframerate(rate)
        w.writeframes(bytes([128]) * int(t * rate))
    return {"audio_path": str(path), "ext": "wav",
            "report": {"duration_s": round(t, 2), "model": "stub-silence", "stub": True, "chapters": chapters}}
