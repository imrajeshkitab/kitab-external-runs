"""HTTP API and the web UI.

    uvicorn extruns.api:app --port 8790

People (the Runs board):  /api/board  /api/books/{id}  /api/eligible  /api/runs  /api/books/continue
                          /api/books/until  /api/books/remove  /api/books/{id}/rerun  /api/owners
External runner:          /api/runner/claim  /api/runner/{id}/heartbeat  /api/runner/{id}/audio
                          /api/runner/{id}/finish  /api/runner/{id}/fail   (header X-Runner-Key)
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import config, db, engine, stages
from .adapters import stub
from .sources import default_source

app = FastAPI(title="Kitab External Runs")
SOURCE = default_source()
WEB = config.ROOT / "web"


def _ok(fn, *a, **k):
    try:
        return fn(*a, **k)
    except engine.Refused as exc:
        raise HTTPException(409, str(exc))
    except ValueError as exc:
        raise HTTPException(422, str(exc))


# ---------- people ----------

class Item(BaseModel):
    linear_id: str
    language: str
    title: str = ""


class NewRun(BaseModel):
    items: list[Item]
    until: str
    by: str = "someone"


class Ids(BaseModel):
    ids: list[int]
    by: str = "someone"


class Until(Ids):
    until: str


class Owner(BaseModel):
    stage: str
    owner: str
    by: str = "someone"


@app.get("/api/board")
def get_board(language: str | None = None):
    return engine.board(language)


@app.get("/api/books/{book_id}")
def get_book(book_id: int):
    b = _ok(engine.book, book_id)
    return {"book": b, "events": engine.events(book_id), "labels": stages.LABELS, "stages": stages.STAGES}


@app.get("/api/eligible")
def eligible(language: str):
    active = {(b["linear_id"]) for b in db.rows("select linear_id from books where language=? and stage!='delivered'", (language,))}
    return [{**b, "in_run": b["linear_id"] in active} for b in SOURCE.books(language)]


@app.post("/api/runs")
def new_run(body: NewRun):
    return {"ids": _ok(engine.add, [i.model_dump() for i in body.items], body.until, body.by)}


@app.post("/api/books/continue")
def cont(body: Ids):
    return _ok(engine.continue_, body.ids, body.by)


@app.post("/api/books/until")
def until(body: Until):
    return _ok(engine.set_until, body.ids, body.until, body.by)


@app.post("/api/books/remove")
def remove(body: Ids):
    return _ok(engine.remove, body.ids, body.by)


@app.post("/api/books/{book_id}/rerun")
def rerun(book_id: int, by: str = "someone"):
    _ok(engine.rerun, book_id, by)
    return {"ok": True}


@app.get("/api/owners")
def get_owners():
    return {"owners": db.owners(), "limits": config.LIMITS, "usual_seconds": engine.usual_seconds()}


@app.put("/api/owners")
def put_owner(body: Owner):
    _ok(engine.set_owner, body.stage, body.owner, body.by)
    return db.owners()


# ---------- external runner ----------

def _runner(key: str | None) -> None:
    if key != config.RUNNER_KEY:
        raise HTTPException(401, "Bad or missing X-Runner-Key.")


class Claim(BaseModel):
    stage: str
    worker: str


class Beat(BaseModel):
    worker: str
    note: str | None = None


class Finish(BaseModel):
    stage: str
    worker: str
    result: dict


class Fail(BaseModel):
    stage: str
    worker: str
    reason: str


@app.post("/api/runner/claim")
def r_claim(body: Claim, x_runner_key: str | None = Header(None)):
    _runner(x_runner_key)
    book = _ok(engine.claim, body.stage, body.worker, "external")
    if book and body.stage == "audio" and hasattr(SOURCE, "voice_for"):
        try:
            book["voice"] = SOURCE.voice_for(book["linear_id"], book["language"])
        except Exception as exc:  # noqa: BLE001 - the job still goes out; the adapter sees voice=None and the reason
            book["voice"], book["voice_error"] = None, f"{type(exc).__name__}: {exc}"
    return {"book": book}


@app.post("/api/runner/{book_id}/heartbeat")
def r_beat(book_id: int, body: Beat, x_runner_key: str | None = Header(None)):
    _runner(x_runner_key)
    _ok(engine.heartbeat, book_id, body.worker, body.note)
    return {"ok": True}


@app.post("/api/runner/{book_id}/audio")
async def r_audio(book_id: int, request: Request, worker: str, ext: str = "mp3", x_runner_key: str | None = Header(None)):
    _runner(x_runner_key)
    data = await request.body()
    if not data:
        raise HTTPException(422, "Empty audio.")
    return {"path": _ok(engine.store_audio, book_id, worker, data, ext)}


@app.post("/api/runner/{book_id}/finish")
def r_finish(book_id: int, body: Finish, x_runner_key: str | None = Header(None)):
    _runner(x_runner_key)
    if body.stage == "audio":
        engine.store_report(book_id, body.result.get("report", {}))
    return _ok(engine.finish, book_id, body.stage, body.worker, "external", body.result)


@app.post("/api/runner/{book_id}/fail")
def r_fail(book_id: int, body: Fail, x_runner_key: str | None = Header(None)):
    _runner(x_runner_key)
    _ok(engine.fail, book_id, body.stage, body.worker, "external", body.reason)
    return {"ok": True}


# ---------- UI and the in-house worker ----------

@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


@app.get("/files/{path:path}")
def files(path: str):
    p = (config.STORAGE / path).resolve()
    if config.STORAGE.resolve() not in p.parents or not p.is_file():
        raise HTTPException(404)
    return FileResponse(p)


def _loop() -> None:
    while True:
        try:
            engine.expire_leases()
            engine.work_inhouse(SOURCE, stub)
        except Exception as exc:  # noqa: BLE001 - the loop must keep going; the error shows in the server log
            print("in-house worker:", exc, flush=True)
        time.sleep(1.0)


@app.on_event("startup")
def start_worker() -> None:
    db.conn()
    threading.Thread(target=_loop, daemon=True, name="in-house-worker").start()
