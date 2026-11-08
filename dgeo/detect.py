# SPDX-License-Identifier: AGPL-3.0-or-later
"""Automatic extraction of Doppler curves (tracks) from the spectrogram.

1. harmonic sum on a log-frequency axis (boosts tonal sources with harmonics: propellers);
2. per-frame local peaks above a threshold;
3. greedy frame-to-frame linking (tolerance in log-frequency, gaps allowed);
4. least-squares fit of the straight-line model (f0, v, d, tc) on ln f;
5. rejection: track too short, poor fit, implausible speed, sub-harmonic duplicates.

Since the shape ratio f(t)/f0 is the same for every harmonic, it does not matter
which harmonic a track follows: the search estimates f0 freely.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.optimize import least_squares
from scipy.signal import find_peaks

from .audio import Spectro
from .doppler import straight_line_freq


@dataclass
class TrackObs:
    id: int
    t: np.ndarray        # s since start of recording
    f: np.ndarray        # Hz
    strength: float = 0.0
    fit: dict = field(default_factory=dict)


def _link(g, H, times, thr, tol, max_gap, max_peaks):
    lg = np.log(g)
    active, done = [], []
    for j in range(H.shape[1]):
        col = H[:, j]
        pk, pr = find_peaks(col, height=thr, distance=3)
        if len(pk) > max_peaks:
            pk = pk[np.argsort(pr["peak_heights"])[::-1][:max_peaks]]
        used = set()
        for tr in sorted(active, key=lambda x: -x["s"]):
            if not len(pk):
                break
            gap = j - tr["j"][-1]
            pred = tr["lf"][-1]
            if len(tr["lf"]) >= 3:  # short linear extrapolation
                pred += (tr["lf"][-1] - tr["lf"][-3]) / max(tr["j"][-1] - tr["j"][-3], 1) * gap
            d = np.abs(lg[pk] - pred)
            order = np.argsort(d)
            for o in order:
                if d[o] > tol * gap or pk[o] in used:
                    break
                used.add(pk[o])
                tr["j"].append(j); tr["lf"].append(lg[pk[o]]); tr["h"].append(col[pk[o]])
                tr["s"] += col[pk[o]]
                break
        still = []
        for tr in active:
            (still if j - tr["j"][-1] <= max_gap else done).append(tr)
        active = still
        for p in pk:
            if p not in used:
                active.append({"j": [j], "lf": [lg[p]], "h": [col[p]], "s": col[p]})
    return done + active


def fit_track(t, f, c):
    fs, fe = np.median(f[: max(3, len(f) // 8)]), np.median(f[-max(3, len(f) // 8):])
    q = max(fs / fe, 1.001)
    v0 = np.clip(c * (q - 1) / (q + 1), 15, 0.55 * c)
    f00 = fs * (c - v0) / c
    tc0 = t[np.argmin(np.abs(f - f00))]
    x0 = [np.log(f00), v0, 800.0, tc0]

    def res(x):
        return np.log(straight_line_freq(t, np.exp(x[0]), x[1], x[2], x[3], c)) - np.log(f)

    lo = [np.log(f.min() * 0.5), 5.0, 20.0, t[0] - 120]
    hi = [np.log(f.max() * 2.0), 0.6 * c, 30000.0, t[-1] + 120]
    best = None
    for d0 in (200.0, 800.0, 2500.0):
        x0[2] = d0
        r = least_squares(res, x0, bounds=(lo, hi), loss="soft_l1", f_scale=0.01)
        if best is None or r.cost < best.cost:
            best = r
    x = best.x
    rms = float(np.sqrt(np.mean(res(x) ** 2)) * 100)
    return {"f0": float(np.exp(x[0])), "v": float(x[1]), "d": float(x[2]), "tc": float(x[3]),
            "t_cpa_heard": float(x[3] + x[2] / c), "rms_pct": rms}


def detect(sp: Spectro, c: float, fmin: float = 40.0, fmax: float = 1500.0, n_harm: int = 4,
           thr: float = 2.5, tol: float = 0.03, max_gap_s: float = 2.0, min_dur_s: float = 15.0,
           max_rms_pct: float = 2.5, v_range=(15.0, 250.0), max_peaks: int = 6) -> list[TrackObs]:
    g, H = sp.harmonic_log(fmin, fmax, n_harm=n_harm)
    # robust re-normalisation: the threshold ``thr`` is in standard deviations of the harmonic sum
    med = np.median(H)
    H = (H - med) / (1.4826 * np.median(np.abs(H - med)) + 1e-9)
    hop = float(np.median(np.diff(sp.t)))
    raw = _link(g, H, sp.t, thr, tol, int(round(max_gap_s / hop)), max_peaks)
    out: list[TrackObs] = []
    for tr in raw:
        j = np.array(tr["j"])
        if (j[-1] - j[0]) * hop < min_dur_s or len(j) < 0.5 * (j[-1] - j[0] + 1):
            continue
        t, f = sp.t[j], np.exp(np.array(tr["lf"]))
        if np.log(f[: len(f) // 4 + 1].mean() / f[-(len(f) // 4 + 1):].mean()) < 0.01:
            continue  # no downward frequency trend: not a Doppler pass
        fit = fit_track(t, f, c)
        if fit["rms_pct"] > max_rms_pct or not (v_range[0] <= fit["v"] <= v_range[1]):
            continue
        if fit["d"] <= 30.0:  # fit stuck at the bound: noise or a non-Doppler frequency change
            continue
        out.append(TrackObs(0, t, f, float(np.mean(tr["h"])), fit))
    out = _dedupe(out)
    out.sort(key=lambda x: x.t[0])
    for i, tr in enumerate(out):
        tr.id = i
    return out


def _dedupe(tracks: list[TrackObs]) -> list[TrackObs]:
    """Remove tracks that are the same source at another harmonic / sub-harmonic,
    or a fragment of a longer track."""
    keep = sorted(tracks, key=lambda x: -(x.strength * (x.t[-1] - x.t[0])))
    res: list[TrackObs] = []
    for tr in keep:
        dup = False
        for k in res:
            lo, hi = max(tr.t[0], k.t[0]), min(tr.t[-1], k.t[-1])
            if hi - lo < 0.5 * min(tr.t[-1] - tr.t[0], k.t[-1] - k.t[0]):
                continue
            tt = np.linspace(lo, hi, 50)
            lr = np.log(np.interp(tt, tr.t, tr.f)) - np.log(np.interp(tt, k.t, k.f))
            if np.std(lr) < 0.01:
                dup = True
                break
        if not dup:
            res.append(tr)
    return res


def to_frame(tracks: list[TrackObs]) -> pd.DataFrame:
    rows = [(tr.id, float(t), float(f)) for tr in tracks for t, f in zip(tr.t, tr.f)]
    return pd.DataFrame(rows, columns=["track", "t", "f"])


def from_frame(df: pd.DataFrame) -> list[TrackObs]:
    """Accept a (track, t, f) CSV — produced by ``detect`` or traced by hand."""
    if "track" not in df.columns:
        df = df.assign(track=0)
    out = []
    for k, g in df.sort_values(["track", "t"]).groupby("track"):
        out.append(TrackObs(int(k), g["t"].values.astype(float), g["f"].values.astype(float)))
    return out
