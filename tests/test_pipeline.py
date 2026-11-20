import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from extruns import config, db, engine, naming, narratable  # noqa: E402
from extruns.adapters import stub  # noqa: E402
from extruns.sources import FixtureSource  # noqa: E402

SRC = FixtureSource(ROOT / "tests" / "fixtures" / "sample_summaries.json")
stub.STEP_SECONDS = 0


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    db.reset_for_tests(tmp_path / "t.sqlite3")
    monkeypatch.setattr(config, "STORAGE", tmp_path / "files")
    yield


def add(lid="SUM-199", lang="marathi", until="audio"):
    return engine.add([{"linear_id": lid, "language": lang, "title": lid}], until, "test")[0]


def ext_run(stage):
    """Act as the external runner for one book of `stage`."""
    b = engine.claim(stage, "ext-1", "external")
    if not b:
        return None
    if stage == "speakable":
        return engine.finish(b["id"], stage, "ext-1", "external", stub.normalise(b))
    out = stub.synthesise(b, config.STORAGE / "tmp")
    engine.store_audio(b["id"], "ext-1", Path(out["audio_path"]).read_bytes(), out["ext"])
    return engine.finish(b["id"], stage, "ext-1", "external", {"report": out["report"]})


# ---------- narratable text ----------

def test_narratable_marathi_uses_sop_lines_and_fixes_closing_heading():
    n = narratable.build(SRC.get("SUM-199", "marathi"))
    heads = [c["heading"] for c in n["chapters"]]
    assert "तुम्ही काय शिकाल?" in heads[0]
    assert heads[1].startswith("मुख्य कल्पना 3 पैकी 1:")
    assert heads[-1] == "निष्कर्ष"
    assert n["fixes"] == [{"chapter": 4, "what": "closing heading", "before": "अंतिम विचार", "after": "निष्कर्ष"}]
    assert any("line 11" in w for w in n["warnings"])          # title-by-author not written for Marathi yet


def test_narratable_english_heading_format():
    n = narratable.build(SRC.get("SUM-199", "english"))
    assert n["chapters"][0]["heading"].startswith("The Now Habit by Neil Fiore. What You'll Take Away? ")
    assert n["chapters"][1]["heading"] == "Key Idea 1 of 3: Idea 1"
    assert n["chapters"][-1]["heading"] == "Conclusion"


def test_missing_tagline_blocks():
    with pytest.raises(narratable.Blocked, match="Tagline"):
        narratable.build(SRC.get("SUM-900", "marathi"))


# ---------- stages, run-until, continue ----------

def test_run_until_narratable_stops_and_continue_moves_on():
    bid = add(until="narratable")
    engine.work_inhouse(SRC, stub)
    b = engine.book(bid)
    assert (b["stage"], b["status"]) == ("narratable", "stopped")
    engine.continue_([bid], "test")
    b = engine.book(bid)
    assert (b["stage"], b["status"], b["until"]) == ("speakable", "waiting", "speakable")


def test_full_run_to_delivered_with_naming():
    bid = add(until="audio")
    engine.work_inhouse(SRC, stub)                         # stage 1 in-house
    assert ext_run("speakable") == {"failed": False, "reasons": []}
    assert ext_run("audio") == {"failed": False, "reasons": []}
    b = engine.book(bid)
    assert b["stage"] == "delivered"
    assert b["audio_path"] == naming.audio_path("bala", "SUM-199", "marathi", 1, "wav")
    assert (config.STORAGE / b["audio_path"]).exists()


def test_failed_book_is_blocked_with_reason():
    bid = add("SUM-900")
    engine.work_inhouse(SRC, stub)
    b = engine.book(bid)
    assert b["status"] == "failed" and "Tagline" in b["error"]


def test_one_at_a_time_and_waiting_spot():
    a, b2 = add("SUM-199"), add("SUM-511")
    engine.work_inhouse(SRC, stub)
    first = engine.claim("speakable", "w", "external")
    assert first and engine.claim("speakable", "w", "external") is None      # 1 at a time
    engine.finish(first["id"], "speakable", "w", "external", stub.normalise(first))
    # first now waits for audio: the waiting spot is full, so speakable may not start the second book
    assert engine.claim("speakable", "w", "external") is None
    assert ext_run("audio")["failed"] is False                               # audio takes it, spot frees
    assert engine.claim("speakable", "w", "external")["id"] == b2


def test_speakable_check_catches_dropped_chapter():
    bid = add()
    engine.work_inhouse(SRC, stub)
    b = engine.claim("speakable", "w", "external")
    res = stub.normalise(b)
    res["chapters"].pop()
    out = engine.finish(bid, "speakable", "w", "external", res)
    assert out["failed"] and "chapters" in out["reasons"][0]


def test_lease_expiry_puts_book_back():
    bid = add()
    engine.work_inhouse(SRC, stub)
    engine.claim("speakable", "w", "external")
    with db.tx() as c:
        c.execute("update books set lease_until=0 where id=?", (bid,))
    assert engine.expire_leases() == [bid]
    assert (engine.book(bid)["stage"], engine.book(bid)["status"]) == ("speakable", "waiting")


def test_bulk_until_and_owner_switch():
    ids = [add("SUM-199"), add("SUM-511")]
    engine.set_until(ids, "narratable", "test")
    engine.work_inhouse(SRC, stub)
    assert {engine.book(i)["status"] for i in ids} == {"stopped"}
    engine.set_owner("speakable", "in-house", "test")
    engine.continue_(ids, "test")
    engine.work_inhouse(SRC, stub)                          # speakable now runs in-house
    assert {engine.book(i)["status"] for i in ids} == {"stopped"}
    assert {engine.book(i)["stage"] for i in ids} == {"speakable"}


def test_adding_same_book_twice_is_refused():
    add()
    with pytest.raises(engine.Refused):
        add()


def test_api_board_and_runner_endpoints():
    from fastapi.testclient import TestClient
    from extruns import api
    api.SOURCE = SRC
    c = TestClient(api.app)
    r = c.post("/api/runs", json={"items": [{"linear_id": "SUM-511", "language": "marathi"}], "until": "speakable"})
    bid = r.json()["ids"][0]
    engine.work_inhouse(SRC, stub)
    assert c.post("/api/runner/claim", json={"stage": "speakable", "worker": "w"}).status_code == 401
    h = {"X-Runner-Key": config.RUNNER_KEY}
    got = c.post("/api/runner/claim", json={"stage": "speakable", "worker": "w"}, headers=h).json()["book"]
    fin = c.post(f"/api/runner/{bid}/finish", headers=h,
                 json={"stage": "speakable", "worker": "w", "result": stub.normalise(got)}).json()
    assert fin["failed"] is False
    board = c.get("/api/board").json()
    assert [b["id"] for b in board["columns"]["speakable"]] == [bid]
    assert c.get(f"/api/books/{bid}").json()["events"][-1]["kind"] == "finished"


def test_http_endpoint_adapter_end_to_end(monkeypatch):
    """The HTTP adapter against two tiny local services, on a real test summary (TEST-0001, Tamil)."""
    import base64, http.server, threading
    from extruns.adapters import http_endpoints

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/normalise":
                out = stub.normalise({"language": body["language"], "narratable": {"chapters": body["chapters"]}})
            else:
                res = stub.synthesise({"speakable": {"chapters": body["chapters"]}}, config.STORAGE / "svc")
                out = {"audio_base64": base64.b64encode(Path(res["audio_path"]).read_bytes()).decode(),
                       "ext": "wav", "report": res["report"]}
            data = json.dumps(out).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    monkeypatch.setenv("NORMALISE_URL", base + "/normalise")
    monkeypatch.setenv("SYNTHESISE_URL", base + "/synthesise")
    bid = engine.add([{"linear_id": "TEST-0001", "language": "tamil", "title": "test"}], "audio", "test")[0]
    engine.work_inhouse(FixtureSource(), stub)
    b = engine.claim("speakable", "w", "external")
    assert engine.finish(bid, "speakable", "w", "external", http_endpoints.normalise(b))["failed"] is False
    b = engine.claim("audio", "w", "external")
    out = http_endpoints.synthesise(b, config.STORAGE / "tmp")
    engine.store_audio(bid, "w", Path(out["audio_path"]).read_bytes(), out["ext"])
    assert engine.finish(bid, "audio", "w", "external", {"report": out["report"]})["failed"] is False
    assert engine.book(bid)["stage"] == "delivered"
    srv.shutdown()


def test_test_summaries_cover_all_columns_and_languages():
    rows = json.loads((ROOT / "fixtures" / "test_summaries.json").read_text())
    langs = {r["language"] for r in rows if r["linear_id"] == "TEST-0001"}
    assert langs == {"bengali", "gujarati", "kannada", "malayalam", "marathi", "odia", "tamil", "telugu"}
    for col in ("id", "linear_id", "language", "title", "author", "category", "final_summary", "is_ready", "speakable_text"):
        assert all(col in r for r in rows)
    for lang in langs:
        narratable.build(FixtureSource().get("TEST-0001", lang))          # every language passes stage 1
    with pytest.raises(narratable.Blocked):
        narratable.build(FixtureSource().get("TEST-0002", "marathi"))     # blocked on purpose


def test_removed_book_number_is_never_reused():
    a = add("SUM-199")
    engine.remove([a], "test")
    b = add("SUM-511")
    assert b != a and not [e for e in engine.events(b) if e["kind"] == "removed"]


def test_test_book_passes_speakable_checks_in_all_languages():
    """A correct normaliser (here the stand-in) must pass our checks on TEST-0001 in every language."""
    from extruns import checks
    for lang in ("bengali", "gujarati", "kannada", "malayalam", "marathi", "odia", "tamil", "telugu"):
        n = narratable.build(FixtureSource().get("TEST-0001", lang))
        s = stub.normalise({"language": lang, "narratable": n})
        assert checks.speakable(n, s, lang) == [], lang
