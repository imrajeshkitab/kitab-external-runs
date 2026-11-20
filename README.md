# Kitab External Runs

A separate app (own repo, own UI) that takes books from summary text to audio through three stages, any of which
can run in-house or on an external machine:

```
Queued → 1 Narratable text → 2 Speakable text → 3 Audio → Audio delivered
```

- **Narratable text** (in-house today): the summary's own words in the SOP narration format: Appendix A spoken
  lines in the book's language, headings set to the standard, fixes logged, a book with a missing section or
  tagline blocked with the reason. Numbers in the spoken lines stay digits; stage 2 turns all numbers into words.
- **Speakable text** (external today): numbers, symbols and emojis as words. Checked in-house on arrival: same
  chapters and order as the narratable text, a heading on every chapter, nothing a voice can't say, the right
  script (95%+), similar length.
- **Audio** (external today): one whole-book file in any format plus a report (duration, model, chapter times).
  Saved as `external/<provider>/<linear_id>/<language>/attempt-<n>/<linear_id>_<language>_<provider>_a<n>.<ext>`
  with `.report.json` beside it.

Each run says how far it goes (until narratable, speakable or audio). A stopped book is continued from its card, a
whole column, or any selection. Speakable and audio each take one book at a time, with one waiting spot between
them. A book with no heartbeat for 20 minutes goes back to waiting. Checks on audio and listening come later.

Design: [pipeline design](https://claude.ai/artifact/VkNcH8tBZW1x1daFympQ5b) ·
[runner proposal](https://claude.ai/artifact/AbuiDZ5Z7CfzeZwHsZLMJE) ·
[screens](https://claude.ai/artifact/T7fcP6nbL28EmDtEyiL936).

**Using an AI coding tool?** It should read [CLAUDE.md](CLAUDE.md) first (foundation overview: flow, integration points, rules; not final).

**External side, start here:** [docs/START_HERE.md](docs/START_HERE.md): setup, the flow, where to plug in your two steps (functions or HTTP endpoints), test summaries, dry run, API reference.

## Run it locally

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn extruns.api:app --port 8790          # server + UI at http://localhost:8790
.venv/bin/python runner/kitab_runner.py                # external stages, stand-in adapters
.venv/bin/python -m pytest -q                          # tests
```

With no settings it reads sample summaries from `fixtures/summaries.json` and keeps everything in
`storage/` (SQLite + files). Environment:

| Variable | Meaning |
|---|---|
| `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` | read summaries, narrators and reference clips straight from the Studio Supabase (put them in `.env`; see `.env.example`) |
| `KITAB_CONTENT_API`, `KITAB_CONTENT_API_KEY` | optional: read via the hosted Content API instead (only when `SUPABASE_URL` is empty) |
| `EXTRUNS_RUNNER_KEY` | shared key the runner sends as `X-Runner-Key` (default `dev-runner-key`, local only) |
| `KITAB_RUNS_URL` | where the runner finds the server |
| `EXTRUNS_PROVIDER` | provider name in paths and file names (default `bala`) |
| `EXTRUNS_STUB_SECONDS` | how long each stand-in step pretends to work |

## Plugging in the real external steps

Write a module with two functions and start the runner with `--adapters your_module`:

```python
def normalise(job):            # job["narratable"]["chapters"] -> speakable text
    return {"chapters": [{"index": 0, "kind": "intro", "heading": "...", "text": "..."}, ...]}

def synthesise(job, out_dir):  # job["speakable"]["chapters"] -> one audio file + report
    return {"audio_path": "...", "ext": "mp3",
            "report": {"duration_s": 1262.4, "model": "...", "chapters": [{"index": 0, "start_s": 0, "end_s": 182.5}]}}
```

`extruns/adapters/stub.py` is the stand-in: it spells numbers in the script's own digits and writes silence.

## Dry run on real summaries

`dryrun/run_dry.py` reads the 52 eight-language summaries from the Studio content table (read-only), runs stage 1
for real and stages 2–3 with the stand-ins, in a throwaway database. Result on 2026-11-20: 28 reached audio
delivered; 24 were blocked in stage 1 with a reason: 19 have no intro heading, so no tagline (4 books × their
languages), and 5 say "8 key ideas" but have 7 (one book in 5 languages).

## Layout

| Path | What |
|---|---|
| `extruns/stages.py` | stages, run-until and continue rules |
| `extruns/engine.py` | every state change (claim, heartbeat, finish, fail, continue, run until, leases, watcher) |
| `extruns/narratable.py`, `sop_lines.json` | stage 1 builder and the SOP Appendix A lines for 10 languages |
| `extruns/checks.py` | in-house checks on speakable text and audio delivery |
| `extruns/naming.py` | file naming |
| `extruns/api.py` | HTTP API, the in-house worker loop, the UI |
| `runner/kitab_runner.py` | the external runner (stdlib only) |
| `web/index.html` | Runs board, card detail, New run, Narratable text, Who runs each stage |

## Not yet

Real Content API run, Postgres instead of SQLite, sign-in for people, audio checks and listening, the 8-language
"title by author" SOP line, and moving storage to a bucket.
