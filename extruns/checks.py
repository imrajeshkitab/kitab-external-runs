"""In-house checks on what comes back from a stage run elsewhere. Each returns a list of failures (empty = pass),
each one a plain sentence a reader can act on."""
from __future__ import annotations

import re
import unicodedata

SCRIPTS = {  # unicode script name prefix for each language's letters
    "hindi": "DEVANAGARI", "marathi": "DEVANAGARI", "bengali": "BENGALI", "gujarati": "GUJARATI",
    "odia": "ORIYA", "tamil": "TAMIL", "telugu": "TELUGU", "kannada": "KANNADA", "malayalam": "MALAYALAM",
    "english": "LATIN",
}
LEFTOVER = re.compile(r"[0-9#@%&*_=+<>\[\]{}|\\/~^$€£₹]|https?://")


def script_share(text: str, language: str) -> float:
    want = SCRIPTS.get(language)
    letters = [ch for ch in text if ch.isalpha()]
    if not want or not letters:
        return 1.0
    ok = sum(1 for ch in letters if unicodedata.name(ch, "").startswith(want))
    return ok / len(letters)


def speakable(narr: dict, speak: dict, language: str) -> list[str]:
    """Speakable text must keep the narratable structure: same chapters in the same order, a heading on every
    chapter, nothing left that a voice can't say, the right script, and about the same length."""
    fails: list[str] = []
    n_ch, s_ch = narr.get("chapters", []), speak.get("chapters", [])
    if len(n_ch) != len(s_ch):
        return [f"Speakable text has {len(s_ch)} chapters; the narratable text has {len(n_ch)}."]
    for n, s in zip(n_ch, s_ch):
        i = n["index"]
        if s.get("kind") and s["kind"] != n["kind"]:
            fails.append(f"Chapter {i} is {s['kind']} but should be {n['kind']}.")
        if not (s.get("heading") or "").strip():
            fails.append(f"Chapter {i} lost its heading.")
        body = (s.get("text") or "").strip()
        if not body:
            fails.append(f"Chapter {i} has no text.")
            continue
        ratio = len(body) / max(1, len(n["text"]))
        if len(n["text"]) >= 300 and not 0.7 <= ratio <= 1.4:   # short chapters swing too much when numbers become words
            fails.append(f"Chapter {i} is {ratio:.0%} the length of the narratable text (allowed 70–140%): text may be missing or added.")
        if m := LEFTOVER.search(s.get("heading", "") + " " + body):
            fails.append(f"Chapter {i} still has '{m.group()}', which a voice can't say.")
    share = script_share(" ".join(c.get("text", "") for c in s_ch), language)
    if share < 0.95:
        fails.append(f"Only {share:.1%} of the letters are in the {language} script (needs 95%).")
    return fails


def audio(report: dict, chapters: int) -> list[str]:
    """Light checks on delivery; the full audio checks come later."""
    fails: list[str] = []
    d = report.get("duration_s")
    if not isinstance(d, (int, float)) or d <= 0:
        fails.append("The report has no duration.")
    ch = report.get("chapters")
    if ch is not None and len(ch) != chapters:
        fails.append(f"The report lists {len(ch)} chapters; the text has {chapters}.")
    return fails
