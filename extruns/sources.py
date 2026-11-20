"""Where summaries (and, for the audio stage, the narrator and reference clip) come from.

SupabaseSource   the Studio Supabase directly, over HTTPS (default when SUPABASE_URL is set). No Vercel involved.
ContentApiSource the hosted Content API (optional; only when KITAB_CONTENT_API is set and SUPABASE_URL is not).
FixtureSource    fixtures/*.json, offline.
All return the Content API feed's shape (see narratable.py). Keys come from the environment / .env, never from code.
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


class SupabaseSource:
    """Reads content.summaries, studio.book_voices, studio.voices and studio.voice_references through Supabase's
    REST API, and signs reference clips from the Storage bucket "studio". Read-only."""

    BUCKET = "studio"

    def __init__(self):
        self.url = os.environ["SUPABASE_URL"].rstrip("/")
        self.key = os.environ["SUPABASE_SERVICE_KEY"]

    def _rest(self, schema: str, table: str, **params) -> list[dict]:
        url = f"{self.url}/rest/v1/{table}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"apikey": self.key, "Authorization": f"Bearer {self.key}",
                                                   "Accept-Profile": schema})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())

    def books(self, language: str) -> list[dict]:
        rows = self._rest("content", "summaries", select="linear_id,language,title", language=f"eq.{language}",
                          order="linear_id")
        return [{"linear_id": r["linear_id"], "language": r["language"], "title": r["title"] or ""} for r in rows]

    def get(self, linear_id: str, language: str) -> dict | None:
        rows = self._rest("content", "summaries", select="linear_id,language,title,author,category,final_summary",
                          linear_id=f"eq.{linear_id}", language=f"eq.{language}")
        if not rows:
            return None
        s = rows[0]
        other = language not in ("english", "hindi")
        return {**s, "author": s.get("author") or "",
                "sections": [x.to_dict() for x in split_sections(s.get("final_summary") or "", other)]}

    def _sign(self, path: str, seconds: int = 24 * 3600) -> str:
        req = urllib.request.Request(f"{self.url}/storage/v1/object/sign/{self.BUCKET}/{path}",
                                     data=json.dumps({"expiresIn": seconds}).encode(), method="POST",
                                     headers={"apikey": self.key, "Authorization": f"Bearer {self.key}",
                                              "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return f"{self.url}/storage/v1{json.loads(r.read())['signedURL']}"

    def voice_for(self, linear_id: str, language: str) -> dict | None:
        """The book's narrator and their reference clip in this language (Hindi as fallback), as the Content API's
        generation-input gives them. None when no narrator is assigned."""
        bv = self._rest("studio", "book_voices", select="voice_id,reason", linear_id=f"eq.{linear_id}")
        if not bv:
            return None
        vid = bv[0]["voice_id"]
        v = (self._rest("studio", "voices", select="voice_id,name,gender,age,pace,status", voice_id=f"eq.{vid}") or [{}])[0]
        refs = self._rest("studio", "voice_references", select="language,text,audio_path,seconds,sample_rate,is_default",
                          voice_id=f"eq.{vid}", language=f"in.({language},hindi)", order="is_default.desc")
        ref = next((r for r in refs if r["language"] == language), None) or next(iter(refs), None)
        out = {"voice_id": vid, "name": v.get("name"), "gender": v.get("gender"), "age": v.get("age"),
               "pace": v.get("pace"), "why_this_voice": bv[0].get("reason")}
        if ref:
            out["reference"] = {"language": ref["language"], "text": ref["text"], "seconds": ref["seconds"],
                                "sample_rate": ref["sample_rate"], "audio_url": self._sign(ref["audio_path"])}
        return out


def default_source():
    if os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SERVICE_KEY"):
        return SupabaseSource()
    if os.environ.get("KITAB_CONTENT_API"):
        return ContentApiSource()
    return FixtureSource()
