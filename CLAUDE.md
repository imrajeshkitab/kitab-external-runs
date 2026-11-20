# Kitab External Runs: overview for AI coding assistants

Read this first. It explains what this repo is, how a book flows through it, where the external normaliser and
model plug in, and what must not change. Humans: the same content, with setup steps and API examples, is in
[docs/START_HERE.md](docs/START_HERE.md).

> **Status: foundation, not final.** The pipeline, the stage engine, the checks and the UI work end to end, but the
> two external steps (speakable-text normalisation and the TTS model call) are stand-ins. The contracts below are
> a first version and will be adjusted once the real normaliser and model scripts or APIs are known. Prefer
> changing an adapter over changing the engine. If a contract must change, change it in one place
> (`extruns/adapters/`, `extruns/checks.py`) and update this file and `docs/START_HERE.md`.

## 1. What this is

Kitab publishes book summaries as audio in 10 languages. English and Hindi are made by Kitab's own pipeline. The
other 8 (Bengali, Gujarati, Kannada, Malayalam, Marathi, Odia, Tamil, Telugu) are voiced by an external partner
with their own normaliser and TTS model. This repo is the bridge: Kitab prepares the text in the right narration
format, the partner turns it into speakable text and audio, and Kitab checks what comes back. It runs locally
(server + web UI + runner on one machine); nothing is hosted.

Words used everywhere:

| Word | Meaning |
|---|---|
| book | one book in **one** language (`linear_id` + `language`) |
| narratable text | the summary's own words in the SOP narration format: fixed spoken lines and standard headings, body unchanged |
| speakable text | narratable text with numbers, symbols and emojis written as words, ready for a voice |
| stage | one step of the pipeline: `narratable`, `speakable`, `audio` |
| run until | how far a book's run goes: stop after narratable, speakable or audio |
| owner | who runs a stage: `in-house` (this server) or `external` (the runner) |
| runner | `runner/kitab_runner.py`: claims external work from the server and calls the adapters |
| adapter | the two partner functions: `normalise(job)` and `synthesise(job, out_dir)` |

## 2. The complete flow

```
              Kitab (this server)                          External (the runner + partner code)
 ┌──────────────────────────────────────────┐   ┌─────────────────────────────────────────────┐
 │ Summary source                           │   │                                             │
 │  Content API (Studio DB content.summaries│   │                                             │
 │  incl. TEST-0001/0002) or fixtures/*.json│   │                                             │
 │            │                             │   │                                             │
 │ Queued  (POST /api/runs, until=...)      │   │                                             │
 │            ▼                             │   │                                             │
 │ 1 Narratable  extruns/narratable.py      │   │                                             │
 │   SOP lines (sop_lines.json), standard   │   │                                             │
 │   headings, fixes logged, or Blocked     │   │                                             │
 │            │  (stop here if until=narr.) │   │                                             │
 │            └──────────── claim ─────────────►  2 Speakable  adapters.normalise(job)      │
 │                                          │   │      one book at a time                     │
 │ checks.speakable() ◄──────── finish ────────────┘                                          │
 │   pass → next / stop;  fail → Failed     │   │                                             │
 │            │                             │   │                                             │
 │ waiting spot (1 book) ── claim ─────────────►  3 Audio  adapters.synthesise(job, out_dir) │
 │                                          │   │      one book at a time                     │
 │ store_audio() + store_report() ◄─ upload + finish ─┘  (audio file + report JSON)          │
 │            ▼                             │   │                                             │
 │ Audio delivered                          │   │                                             │
 │  external/<provider>/<linear_id>/<lang>/ │   │                                             │
 │  attempt-<n>/<id>_<lang>_<provider>_a<n> │   │                                             │
 └──────────────────────────────────────────┘   └─────────────────────────────────────────────┘
```

Step by step:

1. **Queue.** Anyone queues books with how far to go: `POST /api/runs {items:[{linear_id, language, title}], until}`
   (the UI's *New run*). The book sits in `queued`.
2. **Narratable (in-house today).** The server's worker loop (`api._loop` → `engine.work_inhouse`) claims queued
   books, fetches the summary from the source, and calls `narratable.build()`. That function adds the SOP spoken
   lines in the book's language (title by author, "What You'll Take Away?" + tagline, "Key Idea N of M: title",
   "Conclusion"), sets every heading to the standard, logs each fix, and raises `Blocked` with a plain reason when
   the book can't be prepared (no intro heading or tagline, a missing key idea, an empty section). Numbers in the
   spoken lines stay digits; the speakable stage turns all numbers into words.
3. **Speakable (external today).** The runner's speakable lane calls `POST /api/runner/claim {stage:"speakable"}`,
   gets the book with `narratable.chapters`, calls `adapters.normalise(job)`, and sends the result with
   `POST /api/runner/{id}/finish`. The server runs `checks.speakable()` on arrival: same chapters, kinds and
   order; every heading kept; no ASCII digits or unsayable symbols; 95%+ letters in the language's script; length
   within 70–140% for chapters of 300+ characters. Pass → next stage (or *stopped* if the run ends here).
   Fail → *failed* with reasons.
4. **Waiting spot.** At most one finished speakable book may wait for audio. While it's full, `claim` for
   speakable returns nothing (except books whose run stops at speakable). This keeps the fast step from piling up
   work in front of the slow one.
5. **Audio (external today).** The audio lane claims a book with `speakable.chapters`, calls
   `adapters.synthesise(job, out_dir)`, uploads the file (`POST /api/runner/{id}/audio?ext=…`, raw bytes), then
   finishes with the report. The server names and stores the file (`extruns/naming.py`) and moves the book to
   `delivered`.
6. **Continue / run until / rerun.** A book that stopped where its run asked is continued from its card, a whole
   column, or a selection (`POST /api/books/continue`). `until` can be changed any time
   (`POST /api/books/until`). A failed stage is rerun with `POST /api/books/{id}/rerun`; an audio redo is a new
   attempt number, so files are never overwritten.
7. **Leases and the watcher.** A claimed book is held for 20 minutes, renewed by every heartbeat. With no heartbeat
   it goes back to waiting (`engine.expire_leases`). `engine.watcher()` produces the board's warnings: slow
   stages (2×/4× the median), silent runners, a book waiting for audio while audio is idle, stopped books.

Not built yet (planned, later): automatic audio checks (silence, loudness, cut-off endings), human listening and
approve/redo, mixing and publishing to the app.

## 3. Where the partner integrates (the part most likely to change)

The partner writes **only** the two adapter functions; everything else is Kitab's code.

| Option | When | How |
|---|---|---|
| A. Python functions | the normaliser/model are Python code on the same machine | copy `extruns/adapters/my_adapters_template.py`, fill in `normalise()` and `synthesise()`, run `runner/kitab_runner.py --adapters extruns.adapters.my_adapters` |
| B. HTTP endpoints | they already run as services | `NORMALISE_URL=… SYNTHESISE_URL=… runner/kitab_runner.py --adapters extruns.adapters.http_endpoints` (request/response shapes in that file's docstring) |
| C. No runner | the partner has their own scheduler or an agent | call `/api/runner/*` directly (contract in `docs/START_HERE.md` §8–9) |

Current contracts (version 1, expected to evolve):

```python
def normalise(job) -> dict:
    # in:  job["language"], job["narratable"]["chapters"] = [{"index","kind","heading","text"}, ...]
    # out: {"chapters": [{"index","kind","heading","text"}, ...]}   same chapters/kinds/order, numbers as words

def synthesise(job, out_dir) -> dict:
    # in:  job["speakable"]["chapters"], job["language"], job["linear_id"], job["attempt"]
    #      narrator + reference clip: Content API GET /generation-input/{linear_id}/{language}
    # out: {"audio_path": str, "ext": "mp3", "report": {"duration_s": float, "model": str,
    #       "chapters": [{"index","start_s","end_s"}], "sentences": [...optional...]}}
```

When the real scripts/APIs arrive, expect to adjust: the job fields passed in (e.g. adding narrator and reference
clip directly to the claim), per-chapter vs whole-book audio, the report fields (sentence timings), and the
speakable checks' thresholds. Keep those changes inside the adapters, `engine.claim()` (what a job carries) and
`checks.py`.

## 4. Code map

| Path | What it does | Touch when |
|---|---|---|
| `extruns/stages.py` | stage order, `after_finish` (next place or stop), `can_continue` | adding or renaming a stage |
| `extruns/engine.py` | **every state change**: add, continue, set_until, remove, rerun, claim, heartbeat, finish, fail, store_audio, leases, watcher, in-house worker | changing pipeline rules |
| `extruns/db.py` | SQLite schema (`books`, `events`, `owners`), one write transaction at a time | adding fields |
| `extruns/narratable.py` + `sop_lines.json` | stage 1 builder; SOP Appendix A lines for 10 languages | narration format changes |
| `extruns/parsing.py` | splits summary Markdown into intro / key ideas / conclusion (vendored from kitab-tts-engine) | summary format changes |
| `extruns/checks.py` | checks on speakable text and audio delivery | partner output changes |
| `extruns/naming.py` | storage paths and file names | naming changes |
| `extruns/sources.py` | where summaries come from: Content API or `fixtures/*.json` | new source |
| `extruns/adapters/` | `stub.py` (stand-in), `my_adapters_template.py`, `http_endpoints.py` | **partner integration** |
| `extruns/config.py` | limits, lease, owners default, provider name, env vars | tuning |
| `extruns/api.py` | FastAPI: people's API, runner API, UI, in-house worker loop | new endpoints |
| `runner/kitab_runner.py` | external runner, stdlib only: one lane per external stage, heartbeats, retries | runner behaviour |
| `web/index.html` | the UI: Runs board, card detail, New run, Narratable text, Who runs each stage | UI |
| `tests/test_pipeline.py` | 17 tests covering the rules above | always |
| `dryrun/run_dry.py` | real 8-language summaries through every stage, read-only, throwaway DB | checking stage 1 on real data |
| `fixtures/test_summaries.json` | offline copy of TEST-0001/0002 (same rows as in the Studio DB) | |

## 5. Rules that must hold (don't break these)

1. Only `engine.py` changes a book's state, always inside `db.tx()` and always with an `events` row.
2. A stage never holds more than `config.LIMITS[stage]` running books; speakable and audio are 1 each, plus one
   waiting spot before audio.
3. The external side only receives stages it owns (`owners` table), and only books whose run includes that stage.
4. The body text is never rewritten in stage 1. Only spoken lines and headings are added or set to the standard.
   A missing section is never made up: the book is blocked with a reason.
5. Speakable text keeps the narratable structure (chapters, kinds, order, headings). Checks run on every arrival,
   whoever made it.
6. Files are named by `naming.py`. A redo is a new attempt, never an overwrite.
7. Secrets only come from the environment (`EXTRUNS_RUNNER_KEY`, `KITAB_CONTENT_API_KEY`). Never commit keys or
   `.env`, never print them.
8. Kitab's English/Hindi pipeline and Audio Studio are separate; this repo doesn't write to them.

## 6. Run and test

Python 3.10+ (tested on 3.10 and 3.13).

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn extruns.api:app --port 8790          # server + UI, http://localhost:8790
.venv/bin/python runner/kitab_runner.py                # runner (stand-in adapters by default)
.venv/bin/python -m pytest -q                          # 17 tests
```

Test books: **TEST-0001** (8 languages, passes stage 1) and **TEST-0002** (Marathi, Telugu, blocked on purpose).
They're in the Studio DB, served by the Content API (set `KITAB_CONTENT_API` and `KITAB_CONTENT_API_KEY`), and
mirrored in `fixtures/test_summaries.json` for offline runs. A quick dry run: New run → TEST-0001 → Marathi →
*until speakable* → check the card → *Continue to audio*.

## 7. Good first questions to ask about this repo

- "Walk me through `engine.claim()` and how the waiting spot is enforced."
- "What does `narratable.build()` produce for TEST-0001 in Tamil, and which headings did it fix?"
- "Write `extruns/adapters/my_adapters.py` that calls our normaliser at <path or URL> and our model at <…>."
- "Which checks in `checks.speakable()` would fail for this output, and why?"
- "What would change if the model returned one audio file per chapter instead of one per book?"

## 8. Open items (decide as the integration firms up)

- The exact normaliser and model interfaces (functions, HTTP, batch) and their outputs.
- Whether audio comes per book or per chapter, and which timings the report carries.
- The "title by author" SOP line for the 8 languages (Appendix A line 11 isn't written yet), and approval of the
  8 languages' Draft lines.
- Postgres instead of SQLite, sign-in for people, and storage in a bucket instead of `storage/`.
- Audio checks, listening and approve/redo, then mixing and publishing.
