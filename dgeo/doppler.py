# SPDX-License-Identifier: AGPL-3.0-or-later
"""Forward model: ADS-B trajectories -> Doppler curve perceived by an observer.

Key point: the frequency heard at t_obs depends on the aircraft's radial velocity at the
EMISSION time t_e (retarded time), the solution of   t_obs = t_e + |p(t_e) - obs| / c.
It is solved by Newton's method (g = t_e + R/c - t_obs, g' = 1 + dR/dt/c); 4 iterations are enough.

    f_obs = f0 * c / (c + dR/dt(t_e))      (moving source, fixed observer, still air)

The ratio f_obs/f0 does not depend on the harmonic, so the code works on ``ratio``.

The retarded-time pitfall (a naive model gives a wrong curve, not just a delayed one) is explained in
Colsto's video "I discovered a sound that leaks your exact location (and it's everywhere)",
https://www.youtube.com/watch?v=UytYrhYLOKI, on which this project is based.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.signal import savgol_filter

from .geo import LocalFrame, aircraft_hae


@dataclass
class Trajectory:
    hex: str
    t: np.ndarray            # (N,) unix seconds, regular step
    p: np.ndarray            # (N,3) ENU m
    v: np.ndarray            # (N,3) ENU m/s
    meta: dict = field(default_factory=dict)

    @property
    def span(self):
        return float(self.t[0]), float(self.t[-1])

    def label(self) -> str:
        cs = (self.meta.get("flight") or "").strip()
        ty = self.meta.get("type") or "?"
        return f"{cs or self.hex} ({ty})"

    def at(self, te: np.ndarray):
        """Linearly interpolated position and velocity (any input shape), clamped at the ends.
        Since the time step is regular, the index is computed directly (much faster than np.interp)."""
        te = np.asarray(te, float)
        step = self.t[1] - self.t[0]
        x = np.clip((te - self.t[0]) / step, 0.0, len(self.t) - 1.000001)
        i = x.astype(np.int64)
        a = (x - i)[..., None]
        pos = self.p[i] * (1 - a) + self.p[i + 1] * a
        vel = self.v[i] * (1 - a) + self.v[i + 1] * a
        return pos, vel


def build_trajectories(df: pd.DataFrame, frame: LocalFrame, *, qnh: float, geoid_n: float,
                       alt_source: str = "auto", step: float = 1.0, smooth_s: float = 9.0,
                       max_gap: float = 30.0, min_duration: float = 30.0) -> list[Trajectory]:
    """Resample each flight at a fixed step, smooth it (Savitzky-Golay) and derive the velocity.

    ``df``: columns t, hex, lat, lon, alt_baro, alt_geom (+ optional flight, type).
    A flight is split into segments wherever a gap > ``max_gap`` s occurs.
    """
    out: list[Trajectory] = []
    df = df.dropna(subset=["lat", "lon"]).copy()
    df["h"] = aircraft_hae(df["alt_geom"], df["alt_baro"], qnh, geoid_n, alt_source)
    df = df[np.isfinite(df["h"])]
    win = max(5, int(round(smooth_s / step)) | 1)
    for hx, g in df.groupby("hex", sort=False):
        g = g.sort_values("t").drop_duplicates("t")
        if len(g) < 5:
            continue
        enu = frame.to_enu(g["lat"].values, g["lon"].values, g["h"].values)
        t = g["t"].values.astype(float)
        cuts = np.where(np.diff(t) > max_gap)[0] + 1
        for seg in np.split(np.arange(len(t)), cuts):
            if len(seg) < 5 or t[seg[-1]] - t[seg[0]] < min_duration:
                continue
            ts, ps = t[seg], enu[seg]
            tg = np.arange(np.ceil(ts[0]), np.floor(ts[-1]) + step / 2, step)
            pg = np.stack([np.interp(tg, ts, ps[:, i]) for i in range(3)], axis=-1)
            w = min(win, len(tg) - (1 - len(tg) % 2))
            if w < 5:
                continue
            p_s = savgol_filter(pg, w, 2, axis=0)
            v_s = savgol_filter(pg, w, 2, deriv=1, delta=step, axis=0)
            row = g.iloc[0]
            meta = {k: row[k] for k in ("flight", "type", "category") if k in g.columns and pd.notna(row.get(k))}
            out.append(Trajectory(str(hx), tg, p_s, v_s, meta))
    return out


def _c_eff(c: float, rv: np.ndarray, R: np.ndarray, wind):
    """Effective sound speed along the ray in a uniform wind: c + w . n, n = unit vector source -> observer
    (rv = source - observer, so n = -rv / R)."""
    if wind is None:
        return c
    w = np.asarray(wind, float)
    return c - (rv @ w) / np.maximum(R, 1e-6)


def emission(traj: Trajectory, obs: np.ndarray, t_obs: np.ndarray, c: float, iters: int = 4, wind=None):
    """Solve for the retarded (emission) time.

    obs: (M,3); t_obs: (M,K) or (K,) -> returns t_e, pos, vel, R, rvec of shape (M,K[,3]) and a validity mask.
    """
    obs = np.atleast_2d(obs)
    t_obs = np.broadcast_to(np.asarray(t_obs, float), (obs.shape[0],) + np.shape(t_obs)[-1:])
    o = obs[:, None, :]
    pos, _ = traj.at(t_obs)
    rv = pos - o
    R = np.linalg.norm(rv, axis=-1)
    te = t_obs - R / _c_eff(c, rv, R, wind)
    # Newton on g(te) = te + R(te)/c_eff - t_obs, g' ~ 1 + dR/dt/c_eff (quadratic convergence without wind;
    # with wind the slow variation of c_eff along the ray is neglected in g', it still converges fast)
    if wind is not None:
        iters = max(iters, 6)
    for _ in range(iters):
        pos, vel = traj.at(te)
        rv = pos - o
        R = np.linalg.norm(rv, axis=-1)
        ce = _c_eff(c, rv, R, wind)
        g = te + R / ce - t_obs
        gp = 1.0 + np.sum(rv * vel, axis=-1) / (np.maximum(R, 1e-6) * ce)
        te = te - g / gp
    pos, vel = traj.at(te)
    rvec = pos - o
    R = np.linalg.norm(rvec, axis=-1)
    valid = (te >= traj.t[0]) & (te <= traj.t[-1])
    return te, pos, vel, R, rvec, valid


def doppler_ratio(traj: Trajectory, obs: np.ndarray, t_obs: np.ndarray, c: float, wind=None):
    """Ratio f_obs/f0 (M,K), slant range (M,K) and validity mask.

    With a uniform wind w (ENU, m/s): f_obs = f0 * c_eff / (c_eff + dR/dt), c_eff = c + w . n.
    (In the air frame the observer moves at -w and the source at v - w.)"""
    _, _, vel, R, rvec, valid = emission(traj, obs, t_obs, c, wind=wind)
    rdot = np.sum(rvec * vel, axis=-1) / np.maximum(R, 1e-6)
    ce = _c_eff(c, rvec, R, wind)
    return ce / (ce + rdot), R, valid


def cpa(traj: Trajectory, obs: np.ndarray, c: float, wind=None):
    """For each observer (M,3): minimum slant range and *retarded* CPA time
    (the time at which the observer hears the closest point of approach)."""
    obs = np.atleast_2d(obs)
    best_r = np.full(len(obs), np.inf)
    best_t = np.zeros(len(obs))
    chunk = max(1, 2_000_000 // max(len(traj.t), 1))
    for i in range(0, len(obs), chunk):
        o = obs[i:i + chunk, None, :]
        rv_all = traj.p[None, :, :] - o
        r = np.linalg.norm(rv_all, axis=-1)
        k = np.argmin(r, axis=1)
        rr = r[np.arange(len(k)), k]
        best_r[i:i + chunk] = rr
        rv = rv_all[np.arange(len(k)), k]
        best_t[i:i + chunk] = traj.t[k] + rr / _c_eff(c, rv, rr, wind)
    return best_r, best_t


def straight_line_freq(t_obs, f0, v, d, tc, c):
    """Analytic model (constant velocity, straight-line trajectory) with exact retarded time.

    tc = emission time at CPA; d = distance at CPA. Used to fit detected tracks.
    """
    tau = np.asarray(t_obs, float) - tc
    c2, v2 = c * c, v * v
    s = (c2 * tau - np.sqrt(c2 * v2 * tau * tau + (c2 - v2) * d * d)) / (c2 - v2)
    R = np.sqrt(v2 * s * s + d * d)
    rdot = v2 * s / R
    return f0 * c / (c + rdot)
