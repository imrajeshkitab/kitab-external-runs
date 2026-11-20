"""Adapter for steps that already run as HTTP services. No code to write: point it at your two endpoints.

    NORMALISE_URL=http://localhost:9001/normalise SYNTHESISE_URL=http://localhost:9002/synthesise \\
    .venv/bin/python runner/kitab_runner.py --adapters extruns.adapters.http_endpoints

Your normalise endpoint receives (POST, JSON):
    {"book_id": 17, "linear_id": "TEST-0001", "language": "marathi",
     "chapters": [{"index": 0, "kind": "intro", "heading": "...", "text": "..."}, ...]}
and returns:
    {"chapters": [{"index": 0, "kind": "intro", "heading": "...", "text": "..."}, ...], "normaliser": "name v1"}

Your synthesise endpoint receives (POST, JSON):
    {"book_id": 18, "linear_id": "TEST-0001", "language": "marathi", "attempt": 1,
     "chapters": [{"index": 0, "kind": "intro", "heading": "...", "text": "..."}, ...]}
and returns ONE of:
    {"audio_base64": "<the file, base64>", "ext": "mp3", "report": {...}}
    {"audio_path": "/path/on/this/machine/book.mp3", "ext": "mp3", "report": {...}}   (same machine only)
where report is {"duration_s": 1262.4, "model": "name v1", "chapters": [{"index", "start_s", "end_s"}], ...}.

Optional: NORMALISE_TIMEOUT / SYNTHESISE_TIMEOUT in seconds (defaults 600 / 7200), ENDPOINT_TOKEN sent as
"Authorization: Bearer <token>" if your services need one.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import urllib.request
from pathlib import Path


def _post(url: str, body: dict, timeout: float) -> dict:
    headers = {"Content-Type": "application/json"}
    if os.environ.get("ENDPOINT_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['ENDPOINT_TOKEN']}"
    req = urllib.request.Request(url, data=json.dumps(body, ensure_ascii=False).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def normalise(job: dict) -> dict:
    out = _post(os.environ["NORMALISE_URL"],
                {"book_id": job["id"], "linear_id": job["linear_id"], "language": job["language"],
                 "chapters": job["narratable"]["chapters"]},
                float(os.environ.get("NORMALISE_TIMEOUT", "600")))
    return {"chapters": out["chapters"], "normaliser": out.get("normaliser", "")}


def synthesise(job: dict, out_dir: Path) -> dict:
    out = _post(os.environ["SYNTHESISE_URL"],
                {"book_id": job["id"], "linear_id": job["linear_id"], "language": job["language"],
                 "attempt": job["attempt"], "chapters": job["speakable"]["chapters"]},
                float(os.environ.get("SYNTHESISE_TIMEOUT", "7200")))
    ext = out.get("ext", "mp3")
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"book.{ext}"
    if out.get("audio_base64"):
        dest.write_bytes(base64.b64decode(out["audio_base64"]))
    elif out.get("audio_path"):
        shutil.copyfile(out["audio_path"], dest)
    else:
        raise ValueError("synthesise endpoint returned neither audio_base64 nor audio_path")
    return {"audio_path": str(dest), "ext": ext, "report": out["report"]}
