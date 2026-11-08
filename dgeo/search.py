# SPDX-License-Identifier: AGPL-3.0-or-later
"""Phase 1 (overlay at a known position) and phase 2 (grid search at a known time)."""
from __future__ import annotations

import sys
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .audio import Spectro
from .detect import TrackObs
from .events import LoudnessEvent
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
    wind: tuple[float, float, float] | None = None  # uniform wind, ENU m/s (see geo.wind_vector)
    # loudness (broadband) events, see dgeo/events.py
    event_range_m: float = 9000.0  # max slant range at CPA for a LARGE aircraft (ADS-B category A2-A6)
    event_range_light_m: float = 2500.0  # same for light / rotorcraft / unknown category
    event_sigma_s: float = 6.0     # timing tolerance of a loudness peak vs the heard CPA
    event_cap: float = 4.0         # cap of the squared normalised residual (robustness)
    event_weight: float = 0.25     # weight: an unexplained event costs event_weight * event_cap
    lag_min_s: float = -25.0       # loudness peak - heard CPA, shared by all events, fitted per point
    lag_max_s: float = 10.0
    lag_step_s: float = 1.0


def observers(frame: LocalFrame, dem: Dem, e: np.ndarray, n: np.ndarray, agl: float):
    lat, lon, _ = frame.to_geodetic(e, n, np.zeros_like(e))
    h = dem.hae(lat, lon) + agl
    return lat, lon, frame.to_enu(lat, lon, h)


def _sub(tr: TrackObs, k: int):
    if len(tr.t) <= k:
        return tr.t, tr.f
    i = np.linspace(0, len(tr.t) - 1, k).round().astype(int)
    return tr.t[i], tr.f[i]


LARGE_CATEGORIES = {"A2", "A3", "A4", "A5", "A6"}  # A2 = business jets, turboprops


def event_ranges(trajs, cfg) -> np.ndarray:
    """Max CPA slant range at which each flight can explain a loudness event (by ADS-B emitter category)."""
    return np.array([cfg.event_range_m if str(tj.meta.get("category", "")).upper() in LARGE_CATEGORIES
                     else cfg.event_range_light_m for tj in trajs])


def _event_cost(events, rmin_all, tcpa_all, t0, dts, cfg, ranges):
    """Cost (M,D) of the loudness events at the best shared lag, and that lag (M,D) + matched flight per event.

    residual = t_event - (tcpa_heard - t0 - dt) - lag = a + (dt - lag),  a = t_event - (tcpa_heard - t0).
    It only depends on s = dt - lag, so the min over flights is tabulated once on an s grid."""
    M, D = rmin_all.shape[1], len(dts)
    lags = np.arange(cfg.lag_min_s, cfg.lag_max_s + 1e-9, cfg.lag_step_s)
    step = min(cfg.dt_step, cfg.lag_step_s)
    s_grid = np.arange(dts[0] - lags[-1], dts[-1] - lags[0] + step / 2, step)
    rel = tcpa_all - t0                                                   # (K,M)
    ok = rmin_all < ranges[:, None]
    tab = np.empty((len(events), M, len(s_grid)), dtype=np.float32)
    who = np.full((len(events), M, len(s_grid)), -1, dtype=np.int32)
    span = 3 * cfg.event_sigma_s + max(abs(s_grid[0]), abs(s_grid[-1]))
    for e, ev in enumerate(events):
        best = np.full((M, len(s_grid)), cfg.event_cap, dtype=np.float32)
        arg = np.full((M, len(s_grid)), -1, dtype=np.int32)
        a = ev.t - rel                                                    # (K,M)
        for k in np.where((ok & (np.abs(a) < span)).any(axis=1))[0]:
            m = ok[k] & (np.abs(a[k]) < span)
            r2 = ((a[k, m, None] + s_grid[None, :]) / cfg.event_sigma_s) ** 2
            better = r2 < best[m]
            best[m] = np.where(better, r2, best[m])
            arg[m] = np.where(better, k, arg[m])
        tab[e], who[e] = best, arg
    total_s = tab.sum(axis=0)                                             # (M,S)
    out = np.full((M, D), np.inf)
    lag_best = np.zeros((M, D))
    for lag in lags:
        idx = np.clip(np.round((dts - lag - s_grid[0]) / step).astype(int), 0, len(s_grid) - 1)
        v = total_s[:, idx]                                               # (M,D)
        better = v < out
        out = np.where(better, v, out)
        lag_best = np.where(better, lag, lag_best)
    idx_best = np.clip(np.round((dts[None, :] - lag_best - s_grid[0]) / step).astype(int), 0, len(s_grid) - 1)
    matched = np.take_along_axis(who, np.broadcast_to(idx_best, (len(events), M, D)), axis=2)  # (E,M,D)
    return cfg.event_weight * out, lag_best, matched


def score_points(obs: np.ndarray, tracks: list[TrackObs], trajs: list[Trajectory], t0: float, dur: float,
                 c: float, cfg: SearchConfig, chunk: int = 400, events: list[LoudnessEvent] | None = None):
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
        rmin_all[k], tcpa_all[k] = cpa(tj, obs, c, cfg.wind)

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
                ru, _, vu = doppler_ratio(tj, obs[idx], t0 + u, c, cfg.wind)          # (m,U)
                ratio = ru[:, j0] * (1 - w) + ru[:, j0 + 1] * w              # (m,D,K)
                valid = vu[:, j0] & vu[:, j0 + 1]
                cst, lnf0 = track_cost(ratio, valid, fk, cfg.min_coverage)
                better = cst < cost[i, idx]
                cost[i, idx] = np.where(better, cst, cost[i, idx])
                fl[i, idx] = np.where(better, k, fl[i, idx])
                lf0[i, idx] = np.where(better, lnf0, lf0[i, idx])

    total = cost.sum(axis=0)  # (M,D)
    events = events or []
    if events:
        ev_cost, lag_best, ev_flight = _event_cost(events, rmin_all, tcpa_all, t0, dts, cfg,
                                                   event_ranges(trajs, cfg))
        total = total + ev_cost
    else:
        ev_cost = np.zeros((M, D)); lag_best = np.zeros((M, D)); ev_flight = np.zeros((0, M, D), int)
    # silence filter: flights predicted to be audible but not matched to any track or event
    margin = 8.0
    spans = [(tr.t[0], tr.t[-1]) for tr in tracks] + [(ev.t - 5, ev.t + 5) for ev in events]
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
        "event_cost": ev_cost[r, best_d],
        "event_lag": lag_best[r, best_d],
        "events": [ev_flight[e, r, best_d] for e in range(len(events))],
        "silence_violations": (pen[r, best_d] / max(cfg.silence_penalty, 1e-9)).round().astype(int),
        "tracks": [{"flight": fl[i, r, best_d], "f0": np.exp(lf0[i, r, best_d]), "rms_pct": cost[i, r, best_d]}
                   for i in range(nT)],
    }
    return total[r, best_d], detail


def candidate_area(df: pd.DataFrame, coarse_m: float, deltas=(0.1, 0.3)) -> dict:
    """Size of the low-cost area on the coarse grid: for each delta, the area (km²) of the points whose cost
    is within ``delta`` of the minimum, and their max distance (m) from the best point. A large area means the
    recording constrains the position poorly (plateau), whatever the best point says."""
    co = df[df["stage"] == "coarse"] if "stage" in df.columns else df
    b = df.iloc[0]
    out = {}
    for d in deltas:
        z = co[co.cost <= co.cost.min() + d]
        dist = np.hypot(z.e - b.e, z.n - b.n)
        out[d] = (len(z) * (coarse_m / 1000) ** 2, float(dist.max()) if len(z) else 0.0)
    return out


def _grid(cx: float, cy: float, half: float, step: float, circle: bool = True):
    xs = np.arange(-half, half + 1e-9, step)
    E, N = np.meshgrid(xs + cx, xs + cy)
    E, N = E.ravel(), N.ravel()
    if circle:
        m = (E - cx) ** 2 + (N - cy) ** 2 <= half ** 2
        E, N = E[m], N[m]
    return E, N


def search(tracks: list[TrackObs], trajs: list[Trajectory], frame: LocalFrame, dem: Dem, t0: float,
           dur: float, c: float, cfg: SearchConfig, log=print,
           events: list[LoudnessEvent] | None = None) -> pd.DataFrame:
    events = events or []
    if not tracks and not events:
        raise ValueError("no observed tracks or loudness events: run detection first")

    def run(E, N, stage):
        lat, lon, obs = observers(frame, dem, E, N, cfg.obs_agl)
        log(f"[search] {stage}: {len(E)} points x {len(trajs)} flights x {len(tracks)} tracks"
            f" + {len(events)} loudness events")
        cst, det = score_points(obs, tracks, trajs, t0, dur, c, cfg, events=events)
        df = pd.DataFrame({"lat": lat, "lon": lon, "e": E, "n": N, "cost": cst, "dt": det["dt"],
                           "silence_violations": det["silence_violations"],
                           "event_cost": det["event_cost"], "event_lag": det["event_lag"]})
        for e, fk in enumerate(det["events"]):
            df[f"ev{e}_flight"] = [trajs[k].label() if k >= 0 else "" for k in fk]
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
                   fmin: float = 40.0, fmax: float = 1500.0, n_harm: int = 4, wind=None):
    """Phase 1: at a known position, the predicted curve of each nearby flight, with f0 fitted on the spectrogram.
    Returns (list of dicts, best clock dt)."""
    dur = float(sp.t[-1])
    cands = []
    for tj in trajs:
        if tj.t[-1] < t0 - 90 or tj.t[0] > t0 + dur + 10:
            continue
        r, th = cpa(tj, obs[None, :], c, wind)
        if r[0] < max_range_m and -60 < th[0] - t0 < dur + 60:
            cands.append((tj, float(r[0]), float(th[0] - t0)))
    cache: dict = {}
    dts = np.arange(-dt_max, dt_max + 1e-9, dt_step)
    best = (-np.inf, 0.0, [])
    for dt in dts:
        res = []
        for tj, rmin, th in cands:
            ratio, R, valid = doppler_ratio(tj, obs[None, :], t0 + sp.t + dt, c, wind)
            valid = valid[0] & (R[0] < max_range_m)
            sc, f0 = energy_fit(sp, ratio[0], valid, fmin, fmax, n_harm, cache=cache)
            res.append({"label": tj.label(), "hex": tj.hex, "cpa_m": rmin, "cpa_heard_s": th,
                        "score": sc, "f0": f0, "ratio": ratio[0], "valid": valid})
        tot = sum(max(x["score"], 0) for x in res if np.isfinite(x["score"]))
        if tot > best[0]:
            best = (tot, float(dt), res)
    print(f"[overlay] {len(cands)} candidate flights, estimated clock offset {best[1]:+.2f}s", file=sys.stderr)
    return best[2], best[1]
