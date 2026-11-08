# SPDX-License-Identifier: AGPL-3.0-or-later
"""Two ways of comparing a predicted curve with the recording.

- ``track_cost``  : against an extracted track (t, f). f0 is estimated in closed form
                    (mean of ln f - ln ratio); the cost is the RMS residual in % of frequency.
                    Very fast -> used for the grid search.
- ``energy_fit``  : against the raw spectrogram (log-frequency harmonic sum), sweeping
                    f0 by vertical translation. Used for the overlay (phase 1) and for checks.
"""
from __future__ import annotations

import numpy as np

from .audio import Spectro


def track_cost(ratio: np.ndarray, valid: np.ndarray, f_obs: np.ndarray, min_coverage: float = 0.8):
    """ratio, valid: (..., K); f_obs: (K,). Returns (RMS cost in %, ln f0), each of shape (...)."""
    lf = np.log(f_obs)
    lr = np.where(valid, lf - np.log(np.where(valid, ratio, 1.0)), 0.0)
    n = valid.sum(axis=-1)
    lnf0 = lr.sum(axis=-1) / np.maximum(n, 1)
    res = np.where(valid, lr - lnf0[..., None], 0.0)
    rms = np.sqrt((res ** 2).sum(axis=-1) / np.maximum(n, 1)) * 100.0
    cov = n / f_obs.shape[-1]
    return np.where(cov >= min_coverage, rms, np.inf), lnf0


def energy_fit(sp: Spectro, ratio: np.ndarray, valid: np.ndarray, fmin: float = 40.0, fmax: float = 1500.0,
               n_harm: int = 4, bpo: int = 144, cache: dict | None = None):
    """ratio, valid: (T,) over the frames of ``sp``. Returns (score, f0) — score = mean z along
    the curve (and its harmonics), at the best f0."""
    key = (fmin, fmax, n_harm, bpo)
    if cache is not None and key in cache:
        g, H = cache[key]
    else:
        g, H = sp.harmonic_log(fmin, fmax, n_harm=n_harm, bins_per_octave=bpo)
        if cache is not None:
            cache[key] = (g, H)
    j = np.where(valid & np.isfinite(ratio) & (ratio > 0))[0]
    if len(j) < 10:
        return -np.inf, np.nan
    off = np.round(np.log2(ratio[j]) * bpo).astype(int)
    b = np.arange(len(g))
    idx = b[:, None] + off[None, :]
    ok = (idx >= 0) & (idx < len(g))
    vals = np.where(ok, H[np.clip(idx, 0, len(g) - 1), j[None, :]], 0.0)
    cnt = ok.sum(axis=1)
    on = np.where(cnt >= 0.8 * len(j), vals.sum(axis=1) / np.maximum(cnt, 1), np.nan)
    # Tonal contrast: energy along the curve minus that of the parallel curves at +/-6 %.
    # Broadband noise (jets, wind, traffic) lights up all frequencies, so its contrast ≈ 0;
    # only a spectral line that actually follows the predicted curve stands out.
    s = int(round(np.log2(1.06) * bpo))
    side = np.full_like(on, np.nan)
    side[s:-s] = 0.5 * (on[:-2 * s] + on[2 * s:])
    sc = np.where(np.isfinite(on) & np.isfinite(side), on - side, -np.inf)
    if not np.isfinite(sc).any():
        return -np.inf, np.nan
    k = int(np.argmax(sc))
    return float(sc[k]), float(g[k])
