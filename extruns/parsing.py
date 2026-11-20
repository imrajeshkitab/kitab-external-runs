# Vendored from kitab-tts-engine/content_api/parsing.py (2026-11-20) so this repo runs on its own.
"""Split a summary's final_summary Markdown into intro, key ideas and conclusion.

Every ready summary follows this format, each section under a ### heading:

    Intro       EN "What You'll Take Away? <Headline>"   HI "आप क्या सीखेंगे? <headline>"
    Key idea n  EN "Key Idea n of N: <Title>"            HI "मुख्य विचार N में से n: <title>"
    Conclusion  EN "Conclusion"                          HI "निष्कर्ष"

The other eight languages (bn, gu, kn, ml, mr, or, ta, te) use "<What will you learn?> <headline>" for the intro and
"<Key idea> n / N: <title>" for key ideas; in many of those rows the headings are plain lines without "###", so
split_sections(..., other=True) also treats such lines as headings.

Text is returned as stored (Markdown). Turning it into speakable text is the TTS engine's job.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass

# "What will you learn?" in bn, gu, kn, ml, mr, or, ta, te (followed by "?" or ":")
_INTRO_OTHER = ["আপনি কী শিখবেন", "તમે શું શીખશો", "ನೀವು ಏನು ಕಲಿಯುತ್ತೀರಿ", "നിങ്ങൾ എന്ത് പഠിക്കും", "तुम्ही काय शिकाल",
                "ଆପଣ କ['’]ଣ ଶିଖିବେ", "நீங்கள் என்ன கற்றுக்கொள்வீர்கள்", "మీరు ఏమి నేర్చుకుంటారు"]
# "Key idea" in the same languages
KEY_IDEA_WORDS_OTHER = ["মূল ভাবনা", "મુખ્ય વિચાર", "ಮುಖ್ಯ ವಿಚಾರ", "പ്രധാന ആശയം", "मुख्य विचार", "ମୁଖ୍ୟ ବିଚାର",
                        "முக்கிய கருத்து", "ముఖ్య ఆలోచన"]
# "Conclusion", and the closing "Final summary" headings some of those rows use instead
_CONCLUSION_OTHER = ["উপসংহার", "નિષ્કર્ષ", "ತೀರ್ಮಾನ", "ಉಪಸಂಹಾರ", "ഉപസംഹാരം", "निष्कर्ष", "ଉପସଂହାର", "முடிவுரை", "ముగింపు"]
_FINAL_OTHER = ["চূড়ান্ত", "અંતિમ", "ಅಂತಿಮ", "അന്തിമ", "अंतिम", "ଅନ୍ତିମ", "இறுதி", "తుది", "చివరి"]

_INTRO = re.compile(r"^(?:What You[’']ll Take Away\?|आप क्या सीखेंगे\?)\s*(?P<title>.*)$", re.I)
_KEY_IDEA_EN = re.compile(r"^Key Idea\s+(?P<n>\d+)\s+of\s+(?P<total>\d+)\s*:\s*(?P<title>.*)$", re.I)
_KEY_IDEA_HI = re.compile(r"^मुख्य विचार\s+(?P<total>\d+)\s+में से\s+(?P<n>\d+)\s*:\s*(?P<title>.*)$")
_CONCLUSION = re.compile(r"^(?:Conclusion|निष्कर्ष)\s*[:.]?\s*$", re.I)
# Other languages: used only when classify(..., other=True), so English and Hindi parse exactly as before.
_INTRO_O = re.compile(r"^(?:" + "|".join(_INTRO_OTHER) + r")\s*[?:]\s*(?P<title>.*)$")
_KEY_IDEA_O = re.compile(r"^(?:" + "|".join(KEY_IDEA_WORDS_OTHER)
                         + r")\s+(?P<n>\d+)\s*/\s*(?P<total>\d+)\s*:\s*(?P<title>.*)$")
_CONCLUSION_O = re.compile(r"^(?:(?:" + "|".join(_CONCLUSION_OTHER) + r")\s*[:.]?|(?:" + "|".join(_FINAL_OTHER)
                           + r")\s.*)\s*$")
_H3 = re.compile(r"^###\s+(?P<heading>.+?)\s*$")


@dataclass
class Section:
    index: int                 # 0-based position in the summary
    kind: str                  # intro | key_idea | conclusion | other
    heading: str               # heading text exactly as stored, without "### "
    title: str                 # headline / key idea title ("" for conclusion)
    key_idea: int | None       # n in "Key Idea n of N"
    key_idea_total: int | None # N
    text: str                  # body Markdown, heading excluded

    def to_dict(self) -> dict:
        return asdict(self)


def classify(heading: str, other: bool = False) -> tuple[str, str, int | None, int | None]:
    """other=True: the heading is in one of the eight other languages (also accepts **bold** headings)."""
    h = heading.strip()
    if other:
        h = h.strip("*").strip()
        if m := _INTRO_O.match(h):
            return "intro", m["title"].strip(), None, None
        if m := _KEY_IDEA_O.match(h):
            return "key_idea", m["title"].strip(), int(m["n"]), int(m["total"])
        if _CONCLUSION_O.match(h):
            return "conclusion", "", None, None
    if m := _INTRO.match(h):
        return "intro", m["title"].strip(), None, None
    if m := (_KEY_IDEA_EN.match(h) or _KEY_IDEA_HI.match(h)):
        return "key_idea", m["title"].strip(), int(m["n"]), int(m["total"])
    if _CONCLUSION.match(h):
        return "conclusion", "", None, None
    return "other", h, None, None


def split_sections(markdown: str, other: bool = False) -> list[Section]:
    """Split on ### headings. Text before the first heading, if any, becomes a 'preamble' section.
    other=True (the eight other languages): also split on plain lines that read as an intro, key idea or conclusion
    heading, since many of those rows have no "###"."""
    sections: list[Section] = []
    heading: str | None = None
    body: list[str] = []

    def flush() -> None:
        text = "\n".join(body).strip()
        if heading is None:
            if text:
                sections.append(Section(len(sections), "preamble", "", "", None, None, text))
            return
        kind, title, n, total = classify(heading, other)
        sections.append(Section(len(sections), kind, heading, title, n, total, text))

    for line in (markdown or "").splitlines():
        if m := _H3.match(line):
            flush()
            heading, body = m["heading"], []
        elif other and line.strip() and len(line) < 200 and classify(line, True)[0] != "other":
            flush()
            heading, body = line.strip().strip("*").strip(), []
        else:
            body.append(line)
    flush()
    return sections


def structure_issues(sections: list[Section]) -> list[str]:
    """Empty list when the summary matches the standard format."""
    issues: list[str] = []
    kinds = [s.kind for s in sections]
    if not kinds or kinds[0] != "intro":
        issues.append("does not start with the intro heading")
    if not kinds or kinds[-1] != "conclusion":
        issues.append("does not end with the conclusion heading")
    if "other" in kinds or "preamble" in kinds:
        issues.append("has text or headings outside the standard format")
    kis = [s for s in sections if s.kind == "key_idea"]
    if not kis:
        issues.append("has no key ideas")
    else:
        totals = {s.key_idea_total for s in kis}
        if len(totals) != 1 or [s.key_idea for s in kis] != list(range(1, len(kis) + 1)) or totals != {len(kis)}:
            issues.append("key idea numbering is not 1..N of N")
    if any(not s.text for s in sections):
        issues.append("has an empty section")
    return issues


def combined_text(sections: list[Section], with_headings: bool = True) -> str:
    parts = []
    for s in sections:
        if with_headings and s.heading:
            parts.append(f"### {s.heading}\n\n{s.text}")
        else:
            parts.append(s.text)
    return "\n\n".join(parts).strip() + "\n"
