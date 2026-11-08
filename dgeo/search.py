# SPDX-License-Identifier: AGPL-3.0-or-later
"""Phase 1 (overlay at a known position) and phase 2 (grid search at a known time)."""
from __future__ import annotations

import sys
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .audio import Spectro
from .detect import TrackObs
from .doppler import Trajectory, cpa, doppler_ratio
from .geo import Dem, LocalFrame
from .score import energy_fit, track_cost


@dataclass
class SearchConfig:
    radius_km: float = 10.0       # radius of the search area around the centre
    coarse_m: float = 250.0       # coarse grid step
    fine_m: float = 25.0          # fine grid step
    refine_top: int = 15          # number of coarse zones to refine
    refine_radius_m: float = 500.0
    dt_max: float = 3.0           # uncertainty on the recording clock (s)
    dt_step: float = 0.25
    max_range_m: float = 8000.0   # max slant range for an aircraft to be a candidate
    audible_m: float = 2000.0     # an aircraft passing closer MUST appear (silence filter)
    silence_penalty: float = 1.0  # cost added per audible pass that was not observed
    miss_cost: float = 5.0        # capped cost of a track with no compatible flight (%)
    obs_agl: float = 1.5          # microphone height above ground (m)
    max_samples: int = 120        # points per track used for scoring
    min_coverage: float = 0.8


def observers(frame: LocalFrame, dem: Dem, e: np.ndarray, n: np.ndarray, agl: float):
    lat, lon, _ = frame.to_geodetic(e, n, np.zeros_like(e))
    h = dem.hae(lat, lon) + agl
    return lat, lon, frame.to_enu(lat, lon, h)


def _sub(tr: TrackObs, k: int):
    if len(tr.t) <= k:
        return tr.t, tr.f
    i = np.linspace(0, len(tr.t) - 1, k).round().astype(int)
    return tr.t[i], tr.f[i]


def score_points(obs: np.ndarray, tracks: list[TrackObs], trajs: list[Trajectory], t0: float, dur: float,
                 c: float, cfg: SearchConfig, chunk: int = 400):
    """Cost of each point (M,) + details. The clock offset dt is shared by all tracks ("locked")."""
    M = len(obs)
    dts = np.arange(-cfg.dt_max, cfg.dt_max + 1e-9, cfg.dt_step)
    D = len(dts)
    nT = len(tracks)
    cost = np.full((nT, M, D), cfg.miss_cost)
    fl = np.full((nT, M, D), -1, dtype=int)
    lf0 = np.full((nT, M, D), np.nan)
    rmin_all = np.full((len(trajs), M), np.inf)
    tcpa_all = np.full((len(trajs), M), np.nan)

    # CPA of each flight for each point (used for the pre-filter and the silence filter)
    for k, tj in enumerate(trajs):
        if tj.t[-1] < t0 - 90 or tj.t[0] > t0 + dur + 10:
            continue
        rmin_all[k], tcpa_all[k] = cpa(tj, obs, c)

    for i, tr in enumerate(tracks):
        tk, fk = _sub(tr, cfg.max_samples)
        # The physics (retarded time) is computed once on a regular grid u covering
        # [track start - dt_max, track end + dt_max]; each (dt, sample) pair is then
        # interpolated on it. Cost ~ (track duration / dt_step) instead of D x K.
        u = np.arange(tk[0] - cfg.dt_max, tk[-1] + cfg.dt_max + cfg.dt_step, cfg.dt_step)
        x = (tk[None, :] + dts[:, None] - u[0]) / cfg.dt_step   # (D,K)
        j0 = np.clip(x.astype(int), 0, len(u) - 2)
        w = x - j0
        for k, tj in enumerate(trajs):
            if tj.t[-1] < t0 + tk[0] - cfg.dt_max - 60 or tj.t[0] > t0 + tk[-1] + cfg.dt_max:
                continue
            cand = np.where(rmin_all[k] < cfg.max_range_m)[0]
            for s in range(0, len(cand), chunk):
                idx = cand[s:s + chunk]
                ru, _, vu = doppler_ratio(tj, obs[idx], t0 + u, c)          # (m,U)
                ratio = ru[:, j0] * (1 - w) + ru[:, j0 + 1] * w              # (m,D,K)
                valid = vu[:, j0] & vu[:, j0 + 1]
                cst, lnf0 = track_cost(ratio, valid, fk, cfg.min_coverage)
                better = cst < cost[i, idx]
                cost[i, idx] = np.where(better, cst, cost[i, idx])
                fl[i, idx] = np.where(better, k, fl[i, idx])
                lf0[i, idx] = np.where(better, lnf0, lf0[i, idx])

    total = cost.sum(axis=0)  # (M,D)
    # silence filter: flights predicted to be audible but not matched to any track
    margin = 8.0
    spans = [(tr.t[0], tr.t[-1]) for tr in tracks]
    pen = np.zeros((M, D))
    for k in range(len(trajs)):
        close = rmin_all[k] < cfg.audible_m
        if not close.any():
            continue
        th = tcpa_all[k][:, None] - t0 - dts[None, :]  # heard CPA time, relative to the recording
        inside = (th > margin) & (th < dur - margin) & close[:, None]
        used = (fl == k).any(axis=0)
        near_track = np.zeros_like(inside)
        for a, b in spans:
            near_track |= (th > a - 20) & (th < b + 20)
        pen += np.where(inside & ~used & ~near_track, cfg.silence_penalty, 0.0)
    total = total + pen
    best_d = np.argmin(total, axis=1)
    r = np.arange(M)
    detail = {
        "dt": dts[best_d],
        "silence_violations": (pen[r, best_d] / max(cfg.silence_penalty, 1e-9)).round().astype(int),
        "tracks": [{"flight": fl[i, r, best_d], "f0": np.exp(lf0[i, r, best_d]), "rms_pct": cost[i, r, best_d]}
                   for i in range(nT)],
    }
    return total[r, best_d], detail


def _grid(cx: float, cy: float, half: float, step: float, circle: bool = True):
    xs = np.arange(-half, half + 1e-9, step)
    E, N = np.meshgrid(xs + cx, xs + cy)
    E, N = E.ravel(), N.ravel()
    if circle:
        m = (E - cx) ** 2 + (N - cy) ** 2 <= half ** 2
        E, N = E[m], N[m]
    return E, N


def search(tracks: list[TrackObs], trajs: list[Trajectory], frame: LocalFrame, dem: Dem, t0: float,
           dur: float, c: float, cfg: SearchConfig, log=print) -> pd.DataFrame:
    if not tracks:
        raise ValueError("no observed tracks: run detection or provide a tracks CSV")

    def run(E, N, stage):
        lat, lon, obs = observers(frame, dem, E, N, cfg.obs_agl)
        log(f"[search] {stage}: {len(E)} points x {len(trajs)} flights x {len(tracks)} tracks")
        cst, det = score_points(obs, tracks, trajs, t0, dur, c, cfg)
        df = pd.DataFrame({"lat": lat, "lon": lon, "e": E, "n": N, "cost": cst, "dt": det["dt"],
                           "silence_violations": det["silence_violations"]})
        for i, d in enumerate(det["tracks"]):
            df[f"trk{i}_flight"] = [trajs[k].label() if k >= 0 else "" for k in d["flight"]]
            df[f"trk{i}_f0"] = d["f0"]
            df[f"trk{i}_rms"] = d["rms_pct"]
        return df

    E, N = _grid(0.0, 0.0, cfg.radius_km * 1000, cfg.coarse_m)
    coarse = run(E, N, "coarse grid").sort_values("cost")
    seeds = []
    for _, row in coarse.iterrows():
        if len(seeds) >= cfg.refine_top:
            break
        if all((row.e - a) ** 2 + (row.n - b) ** 2 > (2 * cfg.refine_radius_m) ** 2 for a, b in seeds):
            seeds.append((row.e, row.n))
    fe, fn = [], []
    for a, b in seeds:
        e_, n_ = _grid(a, b, cfg.refine_radius_m, cfg.fine_m, circle=False)
        fe.append(e_); fn.append(n_)
    fine = run(np.concatenate(fe), np.concatenate(fn), "refinement")
    out = pd.concat([coarse.assign(stage="coarse"), fine.assign(stage="fine")], ignore_index=True)
    return out.sort_values("cost", ignore_index=True)


def overlay_curves(sp: Spectro, trajs: list[Trajectory], obs: np.ndarray, t0: float, c: float,
                   max_range_m: float = 8000.0, dt_max: float = 5.0, dt_step: float = 0.25,
                   fmin: float = 40.0, fmax: float = 1500.0, n_harm: int = 4):
    """Phase 1: at a known position, the predicted curve of each nearby flight, with f0 fitted on the spectrogram.
    Returns (list of dicts, best clock dt)."""
    dur = float(sp.t[-1])
    cands = []
    for tj in trajs:
        if tj.t[-1] < t0 - 90 or tj.t[0] > t0 + dur + 10:
            continue
        r, th = cpa(tj, obs[None, :], c)
        if r[0] < max_range_m and -60 < th[0] - t0 < dur + 60:
            cands.append((tj, float(r[0]), float(th[0] - t0)))
    cache: dict = {}
    dts = np.arange(-dt_max, dt_max + 1e-9, dt_step)
    best = (-np.inf, 0.0, [])
    for dt in dts:
        res = []
        for tj, rmin, th in cands:
            ratio, R, valid = doppler_ratio(tj, obs[None, :], t0 + sp.t + dt, c)
            valid = valid[0] & (R[0] < max_range_m)
            sc, f0 = energy_fit(sp, ratio[0], valid, fmin, fmax, n_harm, cache=cache)
            res.append({"label": tj.label(), "hex": tj.hex, "cpa_m": rmin, "cpa_heard_s": th,
                        "score": sc, "f0": f0, "ratio": ratio[0], "valid": valid})
        tot = sum(max(x["score"], 0) for x in res if np.isfinite(x["score"]))
        if tot > best[0]:
            best = (tot, float(dt), res)
    print(f"[overlay] {len(cands)} candidate flights, estimated clock offset {best[1]:+.2f}s", file=sys.stderr)
    return best[2], best[1]
