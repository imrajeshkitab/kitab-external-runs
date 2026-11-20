"""Moves books through the stages. Every change goes through here, in one transaction, with an event.

Who does the work for a stage depends on its owner:
  in-house  -> the server's own worker (work_inhouse) picks it up
  external  -> the external runner claims it over the API (claim / heartbeat / finish / fail)
Either way the rules are the same: per-stage limits, one waiting spot in front of audio, run-until, leases.
"""
from __future__ import annotations

import json
import shutil
import statistics
import time
from pathlib import Path

from . import checks, config, db, naming, stages
from .narratable import Blocked, build


class Refused(Exception):
    """An action that isn't allowed right now; the message says why."""


# ---------- reading ----------

def _decode(b: dict) -> dict:
    for k in ("narratable", "speakable", "report"):
        if b.get(k):
            b[k] = json.loads(b[k])
    return b


def book(book_id: int) -> dict:
    b = db.one("select * from books where id=?", (book_id,))
    if not b:
        raise Refused(f"No book {book_id}.")
    return _decode(b)


def board(language: str | None = None) -> dict:
    sql, args = "select * from books", ()
    if language:
        sql, args = sql + " where language=?", (language,)
    books = [_decode(b) for b in db.rows(sql + " order by position", args)]
    cols = {p: [] for p in ("queued", *stages.STAGES, "delivered")}
    for b in books:
        b.pop("narratable", None), b.pop("speakable", None)
        cols.setdefault(b["stage"], []).append(b)
    return {"columns": cols, "owners": db.owners(), "warnings": watcher(), "labels": stages.LABELS}


def events(book_id: int) -> list[dict]:
    ev = db.rows("select * from events where book_id=? order by at", (book_id,))
    for e in ev:
        e["detail"] = json.loads(e["detail"]) if e["detail"] else {}
    return ev


# ---------- people's actions ----------

def add(items: list[dict], until: str, actor: str) -> list[int]:
    until = stages.valid_until(until)
    ids, now = [], time.time()
    with db.tx() as c:
        pos = (c.execute("select max(position) from books").fetchone()[0] or 0) + 1
        for it in items:
            busy = c.execute("select id from books where linear_id=? and language=? and stage!='delivered'",
                             (it["linear_id"], it["language"])).fetchone()
            if busy:
                raise Refused(f"{it['linear_id']} · {it['language']} is already in a run.")
            done = c.execute("select count(*) from books where linear_id=? and language=?",
                             (it["linear_id"], it["language"])).fetchone()[0]
            cur = c.execute("insert into books(linear_id, language, title, until, stage, status, attempt, position, "
                            "created_by, created_at, updated_at) values (?,?,?,?, 'queued','waiting', ?,?,?,?,?)",
                            (it["linear_id"], it["language"], it.get("title", ""), until, done + 1, pos, actor, now, now))
            pos += 1
            ids.append(cur.lastrowid)
            db.event(c, cur.lastrowid, actor, "queued", None, until=until, attempt=done + 1)
    return ids


def continue_(ids: list[int], actor: str) -> dict:
    """Each stopped book moves on to its next stage (its run now goes one stage further)."""
    moved, skipped = [], []
    with db.tx() as c:
        for i in ids:
            b = c.execute("select * from books where id=?", (i,)).fetchone()
            if not b or not stages.can_continue(b["stage"], b["status"]):
                skipped.append(i)
                continue
            nxt = stages.next_stage(b["stage"])
            c.execute("update books set stage=?, status='waiting', until=?, error=null, updated_at=? where id=?",
                      (nxt, nxt, time.time(), i))
            db.event(c, i, actor, "continued", nxt, until=nxt)
            moved.append(i)
    return {"moved": moved, "skipped": skipped}


def set_until(ids: list[int], until: str, actor: str) -> dict:
    until = stages.valid_until(until)
    changed = []
    with db.tx() as c:
        for i in ids:
            b = c.execute("select * from books where id=?", (i,)).fetchone()
            if not b or b["stage"] == "delivered":
                continue
            c.execute("update books set until=?, updated_at=? where id=?", (until, time.time(), i))
            here = b["stage"]
            if here in stages.STAGES and b["status"] == "stopped" and stages.index(until) > stages.index(here):
                c.execute("update books set stage=?, status='waiting' where id=?", (stages.next_stage(here), i))
            elif here in stages.STAGES and b["status"] == "waiting" and stages.index(here) > stages.index(until):
                c.execute("update books set stage=?, status='stopped' where id=?", (until, i))
            db.event(c, i, actor, "until_changed", None, until=until)
            changed.append(i)
    return {"changed": changed}


def remove(ids: list[int], actor: str) -> dict:
    with db.tx() as c:
        for i in ids:
            db.event(c, i, actor, "removed")
            c.execute("delete from books where id=? and status!='running'", (i,))
    return {"removed": ids}


def rerun(book_id: int, actor: str) -> None:
    with db.tx() as c:
        b = c.execute("select * from books where id=?", (book_id,)).fetchone()
        if not b or b["status"] not in ("failed", "stopped") or b["stage"] not in stages.STAGES:
            raise Refused("Only a failed or stopped book can run its stage again.")
        place = "queued" if b["stage"] == stages.STAGES[0] else b["stage"]
        bump = 1 if b["stage"] == "audio" else 0
        c.execute("update books set stage=?, status='waiting', error=null, attempt=attempt+?, updated_at=? where id=?",
                  (place, bump, time.time(), book_id))
        db.event(c, book_id, actor, "rerun", b["stage"])


def set_owner(stage: str, owner: str, actor: str) -> None:
    if stage not in stages.STAGES or owner not in ("in-house", "external"):
        raise Refused("Unknown stage or owner.")
    with db.tx() as c:
        c.execute("update owners set owner=? where stage=?", (owner, stage))


# ---------- doing the work (shared by in-house worker and external runner) ----------

def _waiting_sql(stage: str) -> str:
    return "stage='queued'" if stage == stages.STAGES[0] else f"stage='{stage}' and status='waiting'"


def claim(stage: str, worker: str, owner: str) -> dict | None:
    """Hand out the next book for `stage`, or None. Enforces the owner, the stage limit and the waiting spot."""
    if stage not in stages.STAGES:
        raise Refused("Unknown stage.")
    with db.tx() as c:
        if dict(c.execute("select owner from owners where stage=?", (stage,)).fetchone())["owner"] != owner:
            return None
        running = c.execute("select count(*) from books where stage=? and status='running'", (stage,)).fetchone()[0]
        if running >= config.LIMITS[stage]:
            return None
        cands = c.execute(f"select * from books where {_waiting_sql(stage)} order by position").fetchall()
        if stage == "speakable":
            waiting_audio = c.execute("select count(*) from books where stage='audio' and status='waiting'").fetchone()[0]
            if waiting_audio >= config.AUDIO_WAITING_SPOTS:   # only books that stop at speakable may go ahead
                cands = [b for b in cands if b["until"] == "speakable"]
        if not cands:
            return None
        b = cands[0]
        now = time.time()
        c.execute("update books set stage=?, status='running', claimed_by=?, lease_until=?, heartbeat_at=?, "
                  "stage_started=?, updated_at=? where id=?",
                  (stage, worker, now + config.LEASE_SECONDS, now, now, now, b["id"]))
        db.event(c, b["id"], owner, "started", stage, worker=worker)
    return book(b["id"])


def heartbeat(book_id: int, worker: str, note: str | None = None) -> None:
    with db.tx() as c:
        b = c.execute("select * from books where id=?", (book_id,)).fetchone()
        if not b or b["status"] != "running" or b["claimed_by"] != worker:
            raise Refused("This book is not running for this worker (its lease may have run out).")
        now = time.time()
        c.execute("update books set heartbeat_at=?, lease_until=? where id=?", (now, now + config.LEASE_SECONDS, book_id))
        if note:
            db.event(c, book_id, "external", "progress", b["stage"], note=note)


def _check_running(c, book_id: int, stage: str, worker: str):
    b = c.execute("select * from books where id=?", (book_id,)).fetchone()
    if not b or b["status"] != "running" or b["stage"] != stage or b["claimed_by"] != worker:
        raise Refused("This book is not running this stage for this worker.")
    return b


def finish(book_id: int, stage: str, worker: str, actor: str, result: dict) -> dict:
    """A stage finished. Narratable: result = narratable dict. Speakable: result = {"chapters": [...]}.
    Audio: result = {"report": {...}} after store_audio() saved the file."""
    with db.tx() as c:
        b = dict(_check_running(c, book_id, stage, worker))
        fails: list[str] = []
        sets: dict = {}
        if stage == "narratable":
            sets["narratable"] = json.dumps(result, ensure_ascii=False)
        elif stage == "speakable":
            fails = checks.speakable(json.loads(b["narratable"] or "{}"), result, b["language"])
            sets["speakable"] = json.dumps(result, ensure_ascii=False)
        elif stage == "audio":
            if not b["audio_path"]:
                fails = ["No audio file was uploaded before finishing."]
            fails += checks.audio(result.get("report", {}), len(json.loads(b["narratable"] or "{}").get("chapters", [])))
            sets["report"] = json.dumps(result.get("report", {}), ensure_ascii=False)
        now = time.time()
        if fails:
            sets.update(status="failed", error=" ".join(fails))
            db.event(c, book_id, actor, "failed", stage, reasons=fails, seconds=round(now - (b["stage_started"] or now), 1))
        else:
            place, status = stages.after_finish(stage, b["until"])
            sets.update(stage=place, status=status, error=None)
            db.event(c, book_id, actor, "finished", stage, seconds=round(now - (b["stage_started"] or now), 1),
                     **({"fixes": result.get("fixes", []), "warnings": result.get("warnings", [])} if stage == "narratable" else {}))
        sets.update(claimed_by=None, lease_until=None, updated_at=now)
        c.execute("update books set " + ", ".join(f"{k}=?" for k in sets) + " where id=?", (*sets.values(), book_id))
    return {"failed": bool(fails), "reasons": fails}


def fail(book_id: int, stage: str, worker: str, actor: str, reason: str) -> None:
    with db.tx() as c:
        _check_running(c, book_id, stage, worker)
        c.execute("update books set status='failed', error=?, claimed_by=null, lease_until=null, updated_at=? where id=?",
                  (reason, time.time(), book_id))
        db.event(c, book_id, actor, "failed", stage, reasons=[reason])


def store_audio(book_id: int, worker: str, data: bytes, ext: str) -> str:
    """Save the audio under the standard name; returns the storage path."""
    b = book(book_id)
    if b["status"] != "running" or b["stage"] != "audio" or b["claimed_by"] != worker:
        raise Refused("This book is not running the audio stage for this worker.")
    rel = naming.audio_path(config.PROVIDER, b["linear_id"], b["language"], b["attempt"], ext)
    dest = config.STORAGE / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    with db.tx() as c:
        c.execute("update books set audio_path=? where id=?", (rel, book_id))
        db.event(c, book_id, "external", "audio_saved", "audio", path=rel, bytes=len(data))
    return rel


def store_report(book_id: int, report: dict) -> None:
    b = book(book_id)
    p = config.STORAGE / naming.report_path(config.PROVIDER, b["linear_id"], b["language"], b["attempt"])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------- housekeeping and the watcher ----------

def expire_leases() -> list[int]:
    now, back = time.time(), []
    with db.tx() as c:
        for b in c.execute("select * from books where status='running' and lease_until < ?", (now,)).fetchall():
            place = "queued" if b["stage"] == stages.STAGES[0] else b["stage"]
            c.execute("update books set stage=?, status='waiting', claimed_by=null, lease_until=null where id=?",
                      (place, b["id"]))
            db.event(c, b["id"], "in-house", "lease_expired", b["stage"])
            back.append(b["id"])
    return back


def usual_seconds() -> dict[str, float]:
    out = {}
    for s in stages.STAGES:
        secs = [json.loads(e["detail"])["seconds"] for e in
                db.rows("select detail from events where kind='finished' and stage=? order by at desc limit 50", (s,))
                if e["detail"] and "seconds" in json.loads(e["detail"])]
        if secs:
            out[s] = statistics.median(secs)
    return out


def watcher() -> list[dict]:
    """Plain-language warnings for the Runs board."""
    now, usual, out = time.time(), usual_seconds(), []
    for b in db.rows("select * from books where status='running'"):
        took = now - (b["stage_started"] or now)
        u = usual.get(b["stage"])
        silent = now - (b["heartbeat_at"] or now)
        name = f"{b['title'] or b['linear_id']} · {b['language']}"
        if silent > config.HEARTBEAT_RED_SECONDS:
            out.append({"level": "red", "book_id": b["id"], "text": f"No heartbeat from {name} for {int(silent // 60)} min."})
        elif u and took > config.SLOW_RED * u:
            out.append({"level": "red", "book_id": b["id"], "text": f"{stages.LABELS[b['stage']]} for {name} at {took / u:.1f}× the usual time."})
        elif u and took > config.SLOW_AMBER * u:
            out.append({"level": "amber", "book_id": b["id"], "text": f"{stages.LABELS[b['stage']]} for {name} at {took / u:.1f}× the usual time."})
    waiting_audio = db.one("select count(*) n from books where stage='audio' and status='waiting'")["n"]
    audio_running = db.one("select count(*) n from books where stage='audio' and status='running'")["n"]
    if waiting_audio and not audio_running and db.owners()["audio"] == "external":
        out.append({"level": "red", "text": "A book is waiting for audio but nothing is running it: is the external runner up?"})
    stopped = db.one("select count(*) n from books where status='stopped'")["n"]
    if stopped:
        out.append({"level": "info", "text": f"{stopped} book{'s' if stopped != 1 else ''} stopped where the run asked; continue when ready."})
    return out


# ---------- the in-house worker ----------

def work_inhouse(source, adapters, worker: str = "in-house") -> int:
    """Run every in-house stage that has work, up to its limit. Returns how many books it moved."""
    done = 0
    owners = db.owners()
    for stage in stages.STAGES:
        if owners[stage] != "in-house":
            continue
        while (b := claim(stage, worker, "in-house")):
            try:
                if stage == "narratable":
                    summary = source.get(b["linear_id"], b["language"])
                    if not summary:
                        raise Blocked("The summary was not found in this language.")
                    finish(b["id"], stage, worker, "in-house", build(summary))
                elif stage == "speakable":
                    finish(b["id"], stage, worker, "in-house", adapters.normalise(b))
                else:
                    out = adapters.synthesise(b, config.STORAGE / "tmp" / str(b["id"]))
                    store_audio(b["id"], worker, Path(out["audio_path"]).read_bytes(), out["ext"])
                    store_report(b["id"], out["report"])
                    finish(b["id"], stage, worker, "in-house", {"report": out["report"]})
                    shutil.rmtree(config.STORAGE / "tmp" / str(b["id"]), ignore_errors=True)
            except Blocked as exc:
                fail(b["id"], stage, worker, "in-house", str(exc))
            except Exception as exc:  # noqa: BLE001 - any error marks the book failed with its message
                fail(b["id"], stage, worker, "in-house", f"{type(exc).__name__}: {exc}")
            done += 1
    return done
