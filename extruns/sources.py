"""Where summaries come from. Both return the Content API feed's shape (see narratable.py).

FixtureSource reads fixtures/summaries.json (local runs and tests). ContentApiSource reads the live Content API
(GET /summaries/feed); its base URL and key come from the environment, never from this file.
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from pathlib import Path

from .config import ROOT
from .parsing import split_sections


class FixtureSource:
    """Every *.json in fixtures/ (or one file). Rows may be in the feed's shape (with "sections") or exactly as
    stored in the content table (with "final_summary"), like fixtures/test_summaries.json."""

    def __init__(self, path: Path | None = None):
        self.paths = [path] if path else sorted((ROOT / "fixtures").glob("*.json"))

    def _all(self) -> list[dict]:
        rows = []
        for p in self.paths:
            for s in json.loads(p.read_text(encoding="utf-8")):
                if "sections" not in s:
                    other = s["language"] not in ("english", "hindi")
                    s = {**s, "sections": [x.to_dict() for x in split_sections(s.get("final_summary") or "", other)]}
                rows.append(s)
        return rows

    def books(self, language: str) -> list[dict]:
        return [{"linear_id": s["linear_id"], "language": s["language"], "title": s["title"]}
                for s in self._all() if s["language"] == language]

    def get(self, linear_id: str, language: str) -> dict | None:
        return next((s for s in self._all() if s["linear_id"] == linear_id and s["language"] == language), None)


class ContentApiSource:
    def __init__(self):
        self.base = os.environ["KITAB_CONTENT_API"].rstrip("/")
        self.key = os.environ["KITAB_CONTENT_API_KEY"]

    def _get(self, path: str, **params) -> bytes:
        url = f"{self.base}{path}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"X-API-Key": self.key})
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read()

    def books(self, language: str) -> list[dict]:
        data = json.loads(self._get(f"/languages/{language}/summaries", limit=1000))
        items = data.get("items", data) if isinstance(data, dict) else data
        return [{"linear_id": b["linear_id"], "language": language, "title": b.get("title", "")} for b in items]

    def get(self, linear_id: str, language: str) -> dict | None:
        raw = self._get("/summaries/feed", language=language, linear_ids=linear_id, text="false", ready="false")
        for line in raw.decode("utf-8").splitlines():
            if line.strip():
                s = json.loads(line)
                s.setdefault("language", language)
                return s
        return None


def default_source():
    return ContentApiSource() if os.environ.get("KITAB_CONTENT_API") else FixtureSource()
