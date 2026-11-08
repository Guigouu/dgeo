# SPDX-License-Identifier: AGPL-3.0-or-later
"""Estimate the start time of a recording from its ``creation_time`` metadata.

Depending on the app, ``creation_time`` (UTC) is written at the START or at the END of the recording
(many Android apps write it on stop, when the file is finalised). Two hypotheses are therefore kept,
then eliminated / ranked:

1. impossible: a recording that would end after ``now`` (time of import);
2. tie-break: overlap with the time range of the ADS-B capture, if available;
3. otherwise: "fin" (end; the most common case).

Hypothesis keys (kept in French for compatibility with app/app.py):
  - ``"fin"``   : the metadata was written at the END of the recording -> start = creation_time - duration;
  - ``"debut"`` : the metadata was written at the START of the recording -> start = creation_time.

The metadata is rounded to the second: keep +/-5 s of clock uncertainty downstream.
"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime


def probe(path: str) -> tuple[float | None, str | None]:
    """(duration in s, raw creation_time) via ffprobe; (None, None) if unavailable."""
    try:
        r = subprocess.run(["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", path],
                           capture_output=True, text=True, timeout=60)
        fmt = json.loads(r.stdout or "{}").get("format", {})
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None, None
    dur = float(fmt["duration"]) if fmt.get("duration") else None
    tags = {k.lower(): v for k, v in (fmt.get("tags") or {}).items()}
    return dur, tags.get("creation_time") or tags.get("date")


def parse_creation(s: str | None) -> float | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None  # no time zone: cannot be used safely
    return dt.timestamp()


def guess_start(creation_ts: float | None, duration: float | None, now: float,
                flights_range: tuple[float, float] | None = None, tol: float = 120.0):
    """Return {"hypotheses": {"fin": t0, "debut": t0}, "choice": key|None, "reason": str}.

    ``"fin"`` = metadata written at the END of the recording, ``"debut"`` = written at the START;
    each value is the corresponding estimated start time (unix s).
    """
    if creation_ts is None or not duration:
        return {"hypotheses": {}, "choice": None, "reason": "no usable metadata"}
    hyp = {"fin": creation_ts - duration, "debut": creation_ts}
    possible = {k: v for k, v in hyp.items() if v + duration <= now + tol}
    if not possible:
        return {"hypotheses": hyp, "choice": None,
                "reason": "metadata lies in the future: inconsistent phone clock or file"}
    if len(possible) == 1:
        k = next(iter(possible))
        return {"hypotheses": hyp, "choice": k,
                "reason": "the other hypothesis would place the end of the recording after its import"}
    if flights_range:
        a, b = flights_range
        ov = {k: max(0.0, min(v + duration, b) - max(v, a)) for k, v in possible.items()}
        if max(ov.values()) > 0 and ov["fin"] != ov["debut"]:
            k = max(ov, key=ov.get)
            return {"hypotheses": hyp, "choice": k,
                    "reason": f"best overlap with the ADS-B capture ({ov[k] / 60:.0f} min)"}
    return {"hypotheses": hyp, "choice": "fin", "reason": "default: most apps write the date when recording stops"}
