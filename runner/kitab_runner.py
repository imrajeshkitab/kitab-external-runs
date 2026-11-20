"""Kitab Runner: runs the external stages on the external machine.

    KITAB_RUNS_URL=http://localhost:8790 EXTRUNS_RUNNER_KEY=... python runner/kitab_runner.py [--adapters module]

It asks the server for work on each external stage (speakable text and audio), one book at a time per stage, calls
the two adapter functions, sends heartbeats every 60 s, and hands the result back. The server decides what is
allowed (owner, limits, the waiting spot in front of audio, run-until); the runner only asks. Stdlib only.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import socket
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

URL = os.environ.get("KITAB_RUNS_URL", "http://localhost:8790").rstrip("/")
KEY = os.environ.get("EXTRUNS_RUNNER_KEY", "dev-runner-key")
WORKER = f"{socket.gethostname()}-{os.getpid()}"


def call(path: str, body: dict | bytes | None = None, query: str = "") -> dict:
    data, ctype = (body, "application/octet-stream") if isinstance(body, bytes) else (json.dumps(body or {}).encode(), "application/json")
    req = urllib.request.Request(f"{URL}{path}{query}", data=data, method="POST",
                                 headers={"Content-Type": ctype, "X-Runner-Key": KEY})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as exc:
            if exc.code < 500:
                raise RuntimeError(f"{exc.code}: {exc.read().decode()[:200]}") from exc
        except urllib.error.URLError:
            pass
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"server unreachable: {path}")


def beat(book_id: int, stop: threading.Event) -> None:
    while not stop.wait(60):
        try:
            call(f"/api/runner/{book_id}/heartbeat", {"worker": WORKER})
        except RuntimeError as exc:
            print(f"  heartbeat {book_id}: {exc}", flush=True)


def run_one(stage: str, adapters) -> bool:
    got = call("/api/runner/claim", {"stage": stage, "worker": WORKER}).get("book")
    if not got:
        return False
    bid, name = got["id"], f"{got['linear_id']} · {got['language']}"
    print(f"{stage:<10} start  {name}", flush=True)
    stop = threading.Event()
    threading.Thread(target=beat, args=(bid, stop), daemon=True).start()
    try:
        if stage == "speakable":
            res = call(f"/api/runner/{bid}/finish", {"stage": stage, "worker": WORKER, "result": adapters.normalise(got)})
        else:
            with tempfile.TemporaryDirectory() as td:
                out = adapters.synthesise(got, Path(td))
                call(f"/api/runner/{bid}/audio", Path(out["audio_path"]).read_bytes(), f"?worker={WORKER}&ext={out['ext']}")
                res = call(f"/api/runner/{bid}/finish", {"stage": stage, "worker": WORKER, "result": {"report": out["report"]}})
        print(f"{stage:<10} {'FAILED' if res.get('failed') else 'done  '} {name} {' '.join(res.get('reasons', []))}", flush=True)
    except Exception as exc:  # noqa: BLE001 - report any adapter error back so the book shows it
        print(f"{stage:<10} error  {name}: {exc}", flush=True)
        try:
            call(f"/api/runner/{bid}/fail", {"stage": stage, "worker": WORKER, "reason": f"{type(exc).__name__}: {exc}"})
        except RuntimeError:
            pass
    finally:
        stop.set()
    return True


def lane(stage: str, adapters, pause: threading.Event) -> None:
    """One lane per stage = one book at a time in that stage."""
    while True:
        if pause.is_set() or not run_one(stage, adapters):
            time.sleep(3)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--adapters", default="extruns.adapters.stub", help="module with normalise() and synthesise()")
    a = ap.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    adapters = importlib.import_module(a.adapters)
    pause = threading.Event()
    print(f"kitab-runner {WORKER} → {URL} · adapters {a.adapters}", flush=True)
    for stage in ("speakable", "audio"):
        threading.Thread(target=lane, args=(stage, adapters, pause), daemon=True, name=stage).start()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("stopping: books in progress go back to the queue when their lease runs out", flush=True)


if __name__ == "__main__":
    main()
