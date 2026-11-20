"""Template: your two steps as Python functions. Copy this file (for example to my_adapters.py), fill in the two
TODOs, and start the runner with it:

    .venv/bin/python runner/kitab_runner.py --adapters extruns.adapters.my_adapters

Leave everything else to the runner: it claims books, sends heartbeats, uploads, reports failures, and only ever
gives you a book whose stage is yours.
"""
from __future__ import annotations

from pathlib import Path


def normalise(job: dict) -> dict:
    """Stage 2: narratable text -> speakable text.

    job["language"]                 e.g. "marathi"
    job["narratable"]["chapters"]   [{"index", "kind", "heading", "text"}, ...] in order (intro, key ideas, conclusion)

    Return the same chapters, same order, same kinds, every heading kept, with numbers, symbols and emojis written
    as words in the language (headings included: "मुख्य कल्पना 5 पैकी 1" -> "मुख्य कल्पना पाच पैकी एक").
    Don't add, drop or reword sentences. Kitab checks the result (see docs/START_HERE.md, section "What Kitab checks").
    """
    chapters = job["narratable"]["chapters"]
    out = []
    for c in chapters:
        heading = c["heading"]   # TODO: your normaliser on the heading
        text = c["text"]         # TODO: your normaliser on the text
        out.append({"index": c["index"], "kind": c["kind"], "heading": heading, "text": text})
    return {"chapters": out, "normaliser": "your-normaliser v1"}


def synthesise(job: dict, out_dir: Path) -> dict:
    """Stage 3: speakable text -> one audio file for the whole book, plus a report.

    job["speakable"]["chapters"]    what normalise() returned (after Kitab's checks passed)
    job["language"], job["linear_id"], job["attempt"]
    job["voice"]                    narrator {voice_id, name, gender, age, pace} and reference {language, text,
                                    seconds, sample_rate, audio_url (signed, 24 h)}: clone the voice from this clip

    Write the file inside out_dir (the runner uploads it and Kitab names it). Any format; say which in "ext".
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    audio = out_dir / "book.mp3"
    chapters_times = []
    # TODO: call your model chapter by chapter (or in one go), write `audio`, and record where each chapter
    #       starts and ends in the final file, in seconds.
    raise NotImplementedError("Fill in synthesise() with your model call")
    return {"audio_path": str(audio), "ext": "mp3",
            "report": {"duration_s": 0.0,               # total length in seconds (required)
                       "model": "your-model v1",          # model name and version (required)
                       "chapters": chapters_times,        # [{"index", "start_s", "end_s"}] one per chapter
                       "sentences": []}}                  # optional: [{"chapter", "start_s", "end_s", "text"}]
