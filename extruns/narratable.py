"""Stage 1: raw summary -> narratable text.

Narratable text = the summary's own words in the SOP narration format (Appendix A spoken lines, in the book's
language), with every heading set to the standard. Body text is never changed here. Numbers in the spoken lines
stay as digits ("Key Idea 2 of 5"): turning numbers into words is the speakable stage's job, for headings and body
alike, so it happens in one place.

Input: one summary as the Content API feed returns it:
    {"linear_id", "language", "title", "author", "sections": [{"kind", "heading", "title", "key_idea",
     "key_idea_total", "text"}, ...]}
Output: {"chapters": [{"index", "kind", "heading", "text"}], "text", "fixes", "warnings"}; or raises Blocked.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

SOP = json.loads((Path(__file__).with_name("sop_lines.json")).read_text(encoding="utf-8"))["lines"]


class Blocked(Exception):
    """The book can't be prepared; the message says why in one plain sentence."""


def _line(language: str, key: str) -> dict | None:
    return SOP.get(language, {}).get(key)


def build(summary: dict) -> dict:
    lang = summary["language"].lower()
    if lang not in SOP:
        raise Blocked(f"No SOP spoken lines for {lang}.")
    title, author = (summary.get("title") or "").strip(), (summary.get("author") or "").strip()
    sections = summary.get("sections") or []
    fixes: list[dict] = []
    warnings: list[str] = []

    kinds = [s.get("kind") for s in sections]
    if not sections or kinds[0] != "intro":
        raise Blocked("The summary has no intro section.")
    if kinds[-1] != "conclusion":
        raise Blocked("The summary has no conclusion section.")
    if any(k not in ("intro", "key_idea", "conclusion") for k in kinds):
        raise Blocked("The summary has a section that is not intro, key idea or conclusion.")
    for s in sections:
        if not (s.get("text") or "").strip():
            raise Blocked(f"Section {s.get('index', '?')} ({s.get('kind')}) has no text. A missing section is never made up.")
    if not title:
        raise Blocked("The book has no title.")

    key_ideas = [s for s in sections if s["kind"] == "key_idea"]
    total = len(key_ideas)
    for n, s in enumerate(key_ideas, 1):
        if s.get("key_idea") not in (None, n) or s.get("key_idea_total") not in (None, total):
            fixes.append({"chapter": s.get("index"), "what": "numbering",
                          "before": s.get("heading", ""), "after": f"{n} of {total}"})
        if not (s.get("title") or "").strip():
            raise Blocked(f"Key idea {n} has no title.")

    chapters = []
    for i, s in enumerate(sections):
        if s["kind"] == "intro":
            tagline = (s.get("title") or "").strip()
            if not tagline:
                raise Blocked("Tagline missing: the intro heading has no headline.")
            tba = _line(lang, "title_by_author")
            if tba:
                opener = tba["text"].replace("<title>", title).replace("<author>", author)
            else:
                opener = f"{title}, {author}." if author else f"{title}."
                warnings.append("SOP line 11 (title by author) is not written for this language yet; used '<title>, <author>.'")
            heading = f"{opener} {_line(lang, 'what_you_take_away')['text']} {tagline}".strip()
            if not tagline.endswith((".", "?", "!", "।", "॥")):
                heading += "."
        elif s["kind"] == "key_idea":
            n = key_ideas.index(s) + 1
            heading = (_line(lang, "key_idea")["text"].replace("N", str(n), 1).replace("M", str(total), 1)
                       .replace("<title>", s["title"].strip()))
        else:
            heading = _line(lang, "conclusion")["text"]
            written = (s.get("heading") or "").strip().strip("#*").strip()
            if written and written != heading:
                fixes.append({"chapter": i, "what": "closing heading", "before": written, "after": heading})
        chapters.append({"index": i, "kind": s["kind"], "heading": heading, "text": _clean(s["text"])})

    for k in ("what_you_take_away", "key_idea", "conclusion"):
        if (_line(lang, k) or {}).get("status") != "Approved":
            warnings.append(f"SOP line '{k}' for {lang} is still Draft in the SOP.")
    text = "\n\n".join(f"{c['heading']}\n{c['text']}" for c in chapters)
    return {"chapters": chapters, "text": text, "fixes": fixes, "warnings": sorted(set(warnings))}


_MD = [(re.compile(r"\*\*(.+?)\*\*"), r"\1"), (re.compile(r"(?<!\w)\*(.+?)\*(?!\w)"), r"\1"),
       (re.compile(r"^#{1,6}\s*", re.M), ""), (re.compile(r"^\s*[-*•]\s+", re.M), ""),
       (re.compile(r"\[(.+?)\]\((?:https?://)[^)]*\)"), r"\1")]


def _clean(text: str) -> str:
    """Remove Markdown marks only; the words stay exactly as written."""
    t = text.strip()
    for rx, rep in _MD:
        t = rx.sub(rep, t)
    return re.sub(r"\n{3,}", "\n\n", t)
