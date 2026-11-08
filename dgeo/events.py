# SPDX-License-Identifier: AGPL-3.0-or-later
"""Loudness events: broadband aircraft passes (mostly jets) that produce no usable tone.

A jet passing a few km away shows up as a broad hump of energy in the 150–1500 Hz band. Its peak is
close to the heard CPA, but not exactly on it (directivity: fan noise radiates forward, jet noise aft;
atmospheric absorption). The search therefore matches peak times to predicted heard-CPA times with a
single unknown lag shared by all events, fitted per grid point, and a tolerance ``event_sigma_s``.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.ndimage import uniform_filter1d
from scipy.signal import find_peaks

from .audio import Spectro


@dataclass
class LoudnessEvent:
    t: float            # time of the loudness peak, s from the start of the recording
    prominence_db: float
    width_s: float


def detect_events(sp: Spectro, band=(150.0, 1500.0), smooth_s: float = 6.0, prominence_db: float = 4.0,
                  min_distance_s: float = 40.0, min_width_s: float = 10.0, max_width_s: float = 240.0,
                  exclude: list[tuple[float, float]] | None = None, exclude_margin_s: float = 20.0
                  ) -> list[LoudnessEvent]:
    """Peaks of the band-limited level above the per-frequency background (dB).

    ``exclude``: time spans (e.g. tonal tracks) whose passes are already constrained by a track.
    """
    hop = float(np.median(np.diff(sp.t)))
    m = (sp.f >= band[0]) & (sp.f <= band[1])
    level = sp.db[m] - np.median(sp.db[m], axis=1, keepdims=True)      # above background, per frequency
    env = uniform_filter1d(level.mean(axis=0), max(1, int(round(smooth_s / hop))))
    pk, pr = find_peaks(env, prominence=prominence_db, distance=max(1, int(min_distance_s / hop)),
                        width=(min_width_s / hop, max_width_s / hop))
    out = []
    for i, p in enumerate(pk):
        t = float(sp.t[p])
        if exclude and any(a - exclude_margin_s <= t <= b + exclude_margin_s for a, b in exclude):
            continue
        out.append(LoudnessEvent(t, float(pr["prominences"][i]), float(pr["widths"][i] * hop)))
    return out


def to_frame(events: list[LoudnessEvent]) -> pd.DataFrame:
    return pd.DataFrame([vars(e) for e in events], columns=["t", "prominence_db", "width_s"])


def from_frame(df: pd.DataFrame) -> list[LoudnessEvent]:
    df = df.assign(**{c: df.get(c, np.nan) for c in ("prominence_db", "width_s") if c not in df})
    return [LoudnessEvent(float(r.t), float(r.prominence_db), float(r.width_s)) for r in df.itertuples()]
