# Kitab External Runs: guide for the external side

This guide is for whoever runs the external stages: a person, a script, or an AI assistant working from the API.
You don't need the web UI. Everything here works with plain HTTP and JSON.

## 1. What you do and what we do

A **book** means one book in one language. Every book goes through the same stages:

```
Queued → 1 Narratable text → 2 Speakable text → 3 Audio → Audio delivered
          (Kitab, in-house)   (you, external)    (you, external)
```

| Stage | Input | Output | Who today |
|---|---|---|---|
| 1 · Narratable text | the summary as written | the same words in the narration format: spoken lines from the Kitab SOP, standard headings | Kitab |
| 2 · Speakable text | narratable text | the same text with numbers, symbols and emojis written as words | **you** |
| 3 · Audio | speakable text + narrator + reference clip | one audio file for the whole book + a report | **you** |

You never get the raw summary. You get narratable text, already in the right format. Keep the format; only make it
speakable.

Who runs each stage is a setting on Kitab's side. If Kitab moves a stage to you or back, the API below doesn't
change: you only ever receive work for stages that are yours.

## 2. The rules the system enforces

- **One book at a time per stage.** You can have one book in speakable text and one in audio at the same time,
  never two in the same stage.
- **One waiting spot before audio.** When a book has finished speakable text and is waiting for audio, the next
  book can't start speakable text until audio takes the waiting one. This stops the faster step from piling up
  work. You don't need to handle it: `claim` simply returns nothing until there is room.
- **Run until.** Each book's run goes only as far as Kitab asked: until narratable, speakable or audio. You'll only
  be given books whose run includes your stage.
- **Heartbeat and lease.** When you claim a book you hold it for 20 minutes. Every heartbeat renews that. If no
  heartbeat arrives for 20 minutes, the book goes back to waiting and anyone can claim it. Send one about every 60
  seconds while you work.
- **Kitab checks everything you send back.** If a check fails, the book shows *Failed* with the reason, and Kitab
  decides whether to run the stage again. Section 6 lists the checks.

## 3. Two ways to work

**A. Use the runner (recommended).** `runner/kitab_runner.py` already does claim, heartbeat, upload, finish and
retries. You write two Python functions:

```python
# my_adapters.py
def normalise(job):                 # stage 2
    """job["narratable"]["chapters"] -> the same chapters as speakable text."""
    return {"chapters": [{"index": c["index"], "kind": c["kind"],
                          "heading": speak(c["heading"]), "text": speak(c["text"])}
                         for c in job["narratable"]["chapters"]]}

def synthesise(job, out_dir):       # stage 3
    """job["speakable"]["chapters"] -> one audio file in out_dir, plus a report."""
    ...
    return {"audio_path": str(out_dir / "book.mp3"), "ext": "mp3",
            "report": {"duration_s": 1262.4, "model": "your-model v1",
                       "chapters": [{"index": 0, "start_s": 0.0, "end_s": 182.5}, ...]}}
```

```bash
KITAB_RUNS_URL=https://<server> EXTRUNS_RUNNER_KEY=<your key> python runner/kitab_runner.py --adapters my_adapters
```

**B. Call the API yourself** (your own scheduler, or an AI assistant driving it). Use the loop in section 7.

## 4. Connecting

| | |
|---|---|
| Base URL | given by Kitab (local development: `http://localhost:8790`) |
| Auth | header `X-Runner-Key: <your key>` on every `/api/runner/...` call |
| Format | JSON in and out, except the audio upload (raw bytes) |
| Your worker name | any stable string per process, e.g. `gpu-box-1`; send the same one on every call for a book |

Errors come back as HTTP status codes with `{"detail": "<plain sentence>"}`:

| Code | Meaning | What to do |
|---|---|---|
| 401 | missing or wrong `X-Runner-Key` | fix the key |
| 409 | not allowed right now, e.g. "This book is not running this stage for this worker" (your lease ran out, or the book was taken out of the run) | drop the book and claim the next one |
| 422 | malformed request | fix the body |
| 5xx | server problem | retry after a few seconds |

## 5. Endpoints for the external side

### Claim the next book for a stage

`POST /api/runner/claim`

```json
{"stage": "speakable", "worker": "gpu-box-1"}
```

Returns `{"book": null}` when there's nothing for you (no work, the stage is busy, the waiting spot is full, or the
stage isn't yours right now). Otherwise:

```json
{"book": {
  "id": 17, "linear_id": "SUM-199", "language": "marathi", "title": "द नाऊ हॅबिट",
  "until": "audio", "stage": "speakable", "status": "running", "attempt": 1,
  "narratable": {
    "chapters": [
      {"index": 0, "kind": "intro",      "heading": "द नाऊ हॅबिट, नील फिओरे. तुम्ही काय शिकाल? …", "text": "…"},
      {"index": 1, "kind": "key_idea",   "heading": "मुख्य कल्पना 5 पैकी 1: …", "text": "…"},
      {"index": 6, "kind": "conclusion", "heading": "निष्कर्ष", "text": "…"}],
    "text": "the whole narratable text in one block",
    "fixes": [], "warnings": []},
  "speakable": null}}
```

For `"stage": "audio"`, `speakable` holds the chapters you sent back in stage 2.

Fields you'll use: `id`, `language`, `narratable.chapters` (stage 2), `speakable.chapters` (stage 3), `attempt`.
Narrator and reference clip: read them from the Content API's `GET /generation-input/{linear_id}/{language}`, as
today.

### Heartbeat

`POST /api/runner/{id}/heartbeat` with `{"worker": "gpu-box-1", "note": "voicing chapter 4 of 7"}`. The `note` is
optional and shows in the book's history. A 409 means you no longer hold the book: stop working on it.

### Finish stage 2 (speakable text)

`POST /api/runner/{id}/finish`

```json
{"stage": "speakable", "worker": "gpu-box-1",
 "result": {"chapters": [
   {"index": 0, "kind": "intro", "heading": "…", "text": "…"},
   {"index": 1, "kind": "key_idea", "heading": "मुख्य कल्पना पाच पैकी एक: …", "text": "…"}]}}
```

Reply: `{"failed": false, "reasons": []}`, or `{"failed": true, "reasons": ["Chapter 3 lost its heading."]}`.

### Upload the audio (stage 3)

`POST /api/runner/{id}/audio?worker=gpu-box-1&ext=mp3` with the file's raw bytes as the body
(`Content-Type: application/octet-stream`). Any format is fine; `ext` says which. Kitab names and stores the file
(section 8). Reply: `{"path": "external/…"}`.

### Finish stage 3

`POST /api/runner/{id}/finish`

```json
{"stage": "audio", "worker": "gpu-box-1",
 "result": {"report": {
   "duration_s": 1262.4,
   "model": "your-model v1",
   "chapters":  [{"index": 0, "start_s": 0.0, "end_s": 182.5}, {"index": 1, "start_s": 182.5, "end_s": 360.1}],
   "sentences": [{"chapter": 0, "start_s": 0.0, "end_s": 4.2, "text": "…"}]}}}
```

Required: `duration_s` (seconds) and `model`. `chapters` is strongly wanted, one entry per chapter, in order.
`sentences` is optional. Anything else you add is kept as it is. Upload the audio **before** calling finish.

### Give up on a book

`POST /api/runner/{id}/fail` with `{"stage": "audio", "worker": "gpu-box-1", "reason": "model crashed on chapter 5"}`.
The book shows *Failed* with your reason, and Kitab decides what happens next.

## 6. What Kitab checks

**Speakable text (stage 2)**, checked as soon as you finish:

| Check | Fails with |
|---|---|
| Same number of chapters as the narratable text | "Speakable text has N chapters; the narratable text has M." |
| Same kind in the same place (intro, key_idea, conclusion) | "Chapter 3 is key_idea but should be conclusion." |
| Every chapter keeps a heading | "Chapter 3 lost its heading." |
| Every chapter has text | "Chapter 3 has no text." |
| Nothing a voice can't say: no ASCII digits, `# @ % & * _ = + < > [ ] { } \| \ / ~ ^ $ € £ ₹`, no links | "Chapter 2 still has '%', which a voice can't say." |
| At least 95% of letters in the language's own script | "Only 91.2% of the letters are in the tamil script (needs 95%)." |
| Length within 70–140% of the narratable chapter (chapters of 300+ characters) | "Chapter 4 is 62% the length of the narratable text…" |

So: keep every chapter and heading, in order. Turn **all** numbers into words, headings included ("मुख्य कल्पना 5
पैकी 1" → "मुख्य कल्पना पाच पैकी एक"). Write English words inside the text in the language's own script. Don't
summarise, add or drop sentences.

**Audio (stage 3)**: the audio file must be uploaded, the report needs `duration_s`, and `chapters`, when sent,
must have one entry per chapter. Fuller audio checks (silence, loudness, cut-off endings) and listening come later.

## 7. The loop (for your own scheduler or an AI assistant)

Run one loop per stage you own, side by side. Each loop holds at most one book.

```
loop for STAGE in {speakable, audio}:
    book = POST /api/runner/claim {stage: STAGE, worker: W}
    if book is null: wait 3 s; continue
    start a heartbeat every 60 s: POST /api/runner/{id}/heartbeat {worker: W}
    try:
        if STAGE == speakable:
            chapters = make_speakable(book.narratable.chapters)     # keep chapters, kinds, headings, order
            POST /api/runner/{id}/finish {stage, worker: W, result: {chapters}}
        else:
            file, report = make_audio(book.speakable.chapters, narrator + clip for book.language)
            POST /api/runner/{id}/audio?worker=W&ext=mp3   <bytes of file>
            POST /api/runner/{id}/finish {stage, worker: W, result: {report}}
    except anything:
        POST /api/runner/{id}/fail {stage, worker: W, reason: "<what went wrong>"}
    stop the heartbeat
```

The same in `curl`:

```bash
curl -s $BASE/api/runner/claim -H "X-Runner-Key: $KEY" -H "Content-Type: application/json" \
     -d '{"stage":"speakable","worker":"gpu-box-1"}'
curl -s $BASE/api/runner/17/heartbeat -H "X-Runner-Key: $KEY" -H "Content-Type: application/json" \
     -d '{"worker":"gpu-box-1"}'
curl -s $BASE/api/runner/17/finish -H "X-Runner-Key: $KEY" -H "Content-Type: application/json" \
     -d @speakable_result.json
curl -s "$BASE/api/runner/18/audio?worker=gpu-box-1&ext=mp3" -H "X-Runner-Key: $KEY" \
     -H "Content-Type: application/octet-stream" --data-binary @book.mp3
curl -s $BASE/api/runner/18/finish -H "X-Runner-Key: $KEY" -H "Content-Type: application/json" \
     -d '{"stage":"audio","worker":"gpu-box-1","result":{"report":{"duration_s":1262.4,"model":"v1"}}}'
```

## 8. File naming (done by Kitab)

You upload bytes; Kitab saves them as:

```
external/<provider>/<linear_id>/<language>/attempt-<n>/<linear_id>_<language>_<provider>_a<n>.<ext>
external/<provider>/<linear_id>/<language>/attempt-<n>/<linear_id>_<language>_<provider>_a<n>.report.json
```

For example `external/bala/SUM-199/marathi/attempt-1/SUM-199_marathi_bala_a1.mp3`. A redo is a new attempt
number, so earlier files are never overwritten.

## 9. For an AI assistant working from this guide

If you are an AI assistant asked to run the external stages:

1. Ask the person for the base URL and the runner key. Never print or store the key anywhere but an environment
   variable.
2. Prefer running `runner/kitab_runner.py` with an adapters module wrapping their normaliser and model (section 3A).
   Write the two functions; don't change the runner.
3. If you call the API directly, follow section 7 exactly: one book per stage at a time, a heartbeat every 60 s,
   audio uploaded before finish, `fail` with a reason on any error.
4. Before sending speakable text, check it against section 6 yourself: same chapters and headings, no ASCII digits
   or symbols, the right script. Fix and resend nothing after `finish`: a failed book waits for Kitab.
5. Never claim a stage that isn't yours, never invent text, and never edit headings beyond writing numbers as words.

## 10. People's side of the API (for reference)

These are what the web UI uses; the external side doesn't need them.

| Call | What it does |
|---|---|
| `GET /api/board?language=` | every book by place, owners, warnings |
| `GET /api/books/{id}` | one book with narratable/speakable text, report and history |
| `GET /api/eligible?language=` | books that can be queued |
| `POST /api/runs` `{items: [{linear_id, language, title}], until}` | queue books |
| `POST /api/books/continue` `{ids}` | move stopped books on one stage |
| `POST /api/books/until` `{ids, until}` | change how far books go |
| `POST /api/books/remove` `{ids}` | take books out of the run |
| `POST /api/books/{id}/rerun` | run a failed or stopped stage again |
| `GET/PUT /api/owners` | who runs each stage |
