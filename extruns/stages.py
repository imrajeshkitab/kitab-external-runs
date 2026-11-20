"""The pipeline's stages and the rules for moving a book between them.

A *book* is one book in one language. It sits in exactly one place:
    queued                       waiting for stage 1
    <stage> + status             waiting | running | stopped | failed, for stage in STAGES
    delivered                    audio received (checks and listening come later)

A run says how far a book goes (`until`). When a stage finishes:
    - stage == until             -> stays in that stage with status "stopped" (continue it later)
    - otherwise                  -> next stage, status "waiting"
    - audio finished             -> "delivered"
"""
from __future__ import annotations

STAGES = ("narratable", "speakable", "audio")
LABELS = {"queued": "Queued", "narratable": "1 · Narratable", "speakable": "2 · Speakable",
          "audio": "3 · Audio", "delivered": "Audio delivered"}
STATUSES = ("waiting", "running", "stopped", "failed")


def index(stage: str) -> int:
    return STAGES.index(stage)


def next_stage(stage: str) -> str:
    """The place a book goes after `stage` finishes (ignoring `until`)."""
    if stage == "queued":
        return STAGES[0]
    i = index(stage)
    return STAGES[i + 1] if i + 1 < len(STAGES) else "delivered"


def after_finish(stage: str, until: str) -> tuple[str, str]:
    """(place, status) for a book that just finished `stage` in a run that goes `until`."""
    nxt = next_stage(stage)
    if nxt == "delivered":
        return nxt, "done"
    if stage == until:
        return stage, "stopped"
    return nxt, "waiting"


def can_continue(stage: str, status: str) -> bool:
    return stage in STAGES and status == "stopped" and stage != STAGES[-1]


def valid_until(until: str) -> str:
    if until not in STAGES:
        raise ValueError(f"until must be one of {STAGES}")
    return until
