"""File naming: every file says which book, which language, who made it and which attempt.

    external/<provider>/<linear_id>/<language>/attempt-<n>/<linear_id>_<language>_<provider>_a<n>.<ext>
    ...and the report beside it:                         <linear_id>_<language>_<provider>_a<n>.report.json
"""
from __future__ import annotations

import re

_SAFE = re.compile(r"[^A-Za-z0-9._-]")


def _safe(s: str) -> str:
    return _SAFE.sub("-", s.strip())


def folder(provider: str, linear_id: str, language: str, attempt: int) -> str:
    return f"external/{_safe(provider)}/{_safe(linear_id)}/{_safe(language.lower())}/attempt-{int(attempt)}"


def stem(provider: str, linear_id: str, language: str, attempt: int) -> str:
    return f"{_safe(linear_id)}_{_safe(language.lower())}_{_safe(provider)}_a{int(attempt)}"


def audio_path(provider: str, linear_id: str, language: str, attempt: int, ext: str) -> str:
    ext = _safe(ext.lstrip(".").lower()) or "mp3"
    return f"{folder(provider, linear_id, language, attempt)}/{stem(provider, linear_id, language, attempt)}.{ext}"


def report_path(provider: str, linear_id: str, language: str, attempt: int) -> str:
    return f"{folder(provider, linear_id, language, attempt)}/{stem(provider, linear_id, language, attempt)}.report.json"
