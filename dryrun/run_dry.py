"""Dry run on real summaries, read-only: pulls the 8-language rows from the Studio content table, runs stage 1 for
real and stages 2-3 with the stand-in adapters, in a throwaway database. Writes dryrun/report.json.

    kitab-external-runs/.venv/bin/python kitab-external-runs/dryrun/run_dry.py
"""
import collections, json, sys, tempfile
from pathlib import Path
HERE = Path(__file__).resolve().parent; ROOT = HERE.parents[1]
sys.path[:0] = [str(ROOT / "audio-studio/scripts"), str(ROOT / "kitab-tts-engine"), str(HERE.parent)]
from sb import sql
from content_api.parsing import split_sections
from extruns import config, db, engine
from extruns.adapters import stub
from extruns.sources import FixtureSource

rows = sql("select linear_id, language, title, author, final_summary from content.summaries "
           "where language not in ('english','hindi') order by language, linear_id", "tjassbykrroxxhxdynrk")
summaries = [{"linear_id": r["linear_id"], "language": r["language"], "title": r["title"], "author": r["author"] or "",
              "sections": [s.to_dict() for s in split_sections(r["final_summary"] or "", True)]} for r in rows]
tmp = Path(tempfile.mkdtemp())
(tmp / "s.json").write_text(json.dumps(summaries, ensure_ascii=False))
db.reset_for_tests(tmp / "dry.sqlite3"); config.STORAGE = tmp / "files"; stub.STEP_SECONDS = 0
src = FixtureSource(tmp / "s.json")
engine.add([{"linear_id": s["linear_id"], "language": s["language"], "title": s["title"]} for s in summaries], "audio", "dry-run")
engine.work_inhouse(src, stub)
def one(stage):
    b = engine.claim(stage, "dry", "external")
    if not b:
        return False
    if stage == "speakable":
        engine.finish(b["id"], stage, "dry", "external", stub.normalise(b))
    else:
        out = stub.synthesise(b, config.STORAGE / "t")
        engine.store_audio(b["id"], "dry", Path(out["audio_path"]).read_bytes(), out["ext"])
        engine.finish(b["id"], stage, "dry", "external", {"report": out["report"]})
    return True
while one("audio") | one("speakable"):   # both lanes, one book at a time each, until nothing is left
    pass
books = db.rows("select linear_id, language, stage, status, error, audio_path from books")
report = {"books": books, "by_language": {}}
for b in books:
    report["by_language"].setdefault(b["language"], collections.Counter())[f"{b['stage']}/{b['status']}"] += 1
(HERE / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1, default=dict))
print(len(books), "books")
for l, c in sorted(report["by_language"].items()): print(f"  {l:<10} {dict(c)}")
print("reasons:", collections.Counter(b["error"] for b in books if b["error"]).most_common())
