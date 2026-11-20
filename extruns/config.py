"""Settings, all overridable by environment variables (never put keys in this file)."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_env(path: Path) -> None:
    """KEY=value lines from .env into the environment (without overriding what is already set). Values are never
    printed or logged."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            v = v.strip().strip('"').strip("'")
            if v:                                   # an empty value in .env means "not set"
                os.environ.setdefault(k.strip(), v)


_load_env(ROOT / ".env")
DB_PATH = Path(os.environ.get("EXTRUNS_DB", ROOT / "storage" / "extruns.sqlite3"))
STORAGE = Path(os.environ.get("EXTRUNS_STORAGE", ROOT / "storage" / "files"))
RUNNER_KEY = os.environ.get("EXTRUNS_RUNNER_KEY", "dev-runner-key")   # set a real one outside local dev

# Who runs each stage today; changeable at runtime (Who runs each stage). in-house | external
DEFAULT_OWNERS = {"narratable": "in-house", "speakable": "external", "audio": "external"}

# How many books a stage may hold "running" at once, and how many finished books may wait for audio.
LIMITS = {"narratable": 4, "speakable": 1, "audio": 1}
AUDIO_WAITING_SPOTS = 1

LEASE_SECONDS = 20 * 60          # no heartbeat for this long -> back to waiting
HEARTBEAT_RED_SECONDS = 5 * 60   # watcher: red when a running book has been silent this long
SLOW_AMBER, SLOW_RED = 2.0, 4.0  # watcher: times the usual (median) duration

PROVIDER = os.environ.get("EXTRUNS_PROVIDER", "bala")   # recorded in paths and file names
