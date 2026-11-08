# SPDX-License-Identifier: AGPL-3.0-or-later
"""Realistic synthetic scenario to validate the pipeline without leaving home.

Generates:
  - ``flights.csv``  : noisy ADS-B positions (same format as the live capture);
  - ``rec.wav``      : 5 min recording with 2-3 propeller passes (harmonics, exact Doppler
                       with retarded time, 1/R attenuation), pink noise, 50 Hz mains hum, gusts;
  - ``truth.json``   : true position, true clock offset, etc.

Trajectories are NOT straight lines (turn + deceleration + climb), so that the forward
model genuinely matters.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from scipy.io import wavfile

from .doppler import Trajectory, emission
from .geo import FT, LocalFrame, speed_of_sound

CENTER = (39.8283, -98.5795)      # arbitrary origin of the synthetic world (geographic centre of the contiguous US)
SYNTH_GEOID_N = 51.5             # geoid undulation of the synthetic world (m)
GROUND = 650.0                   # constant orthometric ground elevation (m)


def _flight(q_cpa, heading_deg, speed, accel, turn_dps, climb, t_cpa, span=(-400, 400), dt=0.1,
            manoeuvre_s=60.0):
    """Integrate a trajectory passing through q_cpa at t_cpa (heading = heading at t_cpa).
    Turn and acceleration only apply within +/-manoeuvre_s around the CPA."""
    tau = np.arange(span[0], span[1] + dt / 2, dt)
    tm = np.clip(tau, -manoeuvre_s, manoeuvre_s)
    hdg = np.radians(heading_deg + turn_dps * tm)
    spd = np.maximum(speed + accel * tm, 20)
    v = np.stack([spd * np.sin(hdg), spd * np.cos(hdg), np.full_like(tau, climb)], axis=-1)
    p = np.cumsum(v, axis=0) * dt
    i0 = np.argmin(np.abs(tau))
    p = p - p[i0] + np.asarray(q_cpa, float)
    return Trajectory("", t_cpa + tau, p, v)


def make(out_dir: str, seed: int = 1, dur: float = 300.0, fs: int = 8000):
    rng = np.random.default_rng(seed)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    c = speed_of_sound(15.0)
    hae_ground = GROUND + SYNTH_GEOID_N
    frame = LocalFrame(*CENTER, hae_ground)
    obs = np.array([2300.0 + rng.uniform(-150, 150), -1700.0 + rng.uniform(-150, 150), 1.5])
    t0_true = 1_778_000_000.0
    clock_err = 1.2                     # the phone clock is 1.2 s behind
    t0_reported = t0_true - clock_err

    side = lambda hdg: np.array([np.cos(np.radians(hdg)), -np.sin(np.radians(hdg)), 0.0])
    specs = [
        # hex, callsign, type, hdg, speed, accel, turn, climb, cpa_side_m, agl, t_cpa_rel, f0 (None = silent)
        ("34a1f0", "ANE8301", "AT76", 60, 105, -0.25, 0.35, -2.0, 450, 650, 70, 118.0),
        ("3433c2", "ECNXB", "C172", 200, 62, 0.05, -0.15, 2.5, -800, 420, 215, 81.0),
        ("34a1f7", "ANE8411", "AT76", 62, 110, 0.0, 0.0, -2.5, 2900, 900, 120, 121.0),  # distant decoy
        ("3c6744", "DLH1121", "A320", 330, 150, 0.0, 0.0, -4.0, -6500, 1500, 160, None),
        ("4ca2d6", "RYR77QK", "B38M", 140, 230, 0.0, 0.0, 0.0, 1500, 10500, 140, None),
        ("343146", "IBE3166", "A321", 20, 145, 0.0, 0.05, 6.0, 9000, 1200, 30, None),
        ("34510b", "ECMLT", "PA28", 90, 55, 0.0, 0.1, 0.0, 1200, 500, -150, None),
    ]
    rows, tracks_true = [], {}
    for hx, cs, ty, hdg, spd, acc, turn, climb, sd, agl, tcr, f0 in specs:
        q = obs + side(hdg) * sd
        q[2] = agl
        tj = _flight(q, hdg, spd, acc, turn, climb, t0_true + tcr)
        tj.hex = hx
        tracks_true[hx] = (tj, f0)
        # irregular ADS-B sampling (0.5-2 s) + position noise + altitude quantisation
        ts, t = [], tj.t[0] + rng.uniform(0, 1)
        while t < tj.t[-1]:
            ts.append(t)
            t += rng.uniform(0.5, 2.0)
        ts = np.array(ts)
        p, v = tj.at(ts)
        p = p + rng.normal(0, 5.0, p.shape) * np.array([1, 1, 0])
        lat, lon, h = frame.to_geodetic(p[:, 0], p[:, 1], p[:, 2])  # u relative to the ground (frame origin)
        alt_geom = np.round(h / FT / 25) * 25
        alt_baro = np.round((h - SYNTH_GEOID_N) / FT / 25) * 25   # QNH 1013.25 for simplicity
        gs = np.hypot(v[:, 0], v[:, 1]) / 0.514444
        trk = np.degrees(np.arctan2(v[:, 0], v[:, 1])) % 360
        for k in range(len(ts)):
            rows.append([round(ts[k], 1), hx, cs, ty, "A1", lat[k], lon[k], alt_baro[k], alt_geom[k],
                         round(gs[k], 1), round(trk[k], 1), "adsb_icao"])

    import pandas as pd
    from .adsb import COLUMNS
    pd.DataFrame(rows, columns=COLUMNS).sort_values("t").to_csv(out / "flights.csv", index=False)

    # ---------- audio ----------
    n = int(dur * fs)
    ta = t0_true + np.arange(n) / fs
    x = _pink(n, rng) * 0.05
    x += 0.004 * np.sin(2 * np.pi * 50 * ta) + 0.002 * np.sin(2 * np.pi * 150 * ta)  # mains hum
    for _ in range(25):  # gusts / broadband noise bursts
        a = rng.integers(0, n - fs)
        L = int(rng.uniform(0.2, 1.5) * fs)
        x[a:a + L] += rng.normal(0, 0.04, min(L, n - a)) * np.hanning(min(L, n - a))
    tg = np.arange(t0_true, t0_true + dur + 0.05, 0.05)
    harm = [1.0, 0.7, 0.45, 0.3]
    for hx, (tj, f0) in tracks_true.items():
        if f0 is None:
            continue
        _, _, vel, R, rvec, valid = emission(tj, obs[None, :], tg, c)
        rdot = np.sum(rvec * vel, axis=-1)[0] / R[0]
        fo = f0 * c / (c + rdot)
        amp = np.where(valid[0], 0.012 * (600.0 / R[0]) ** 1.2, 0.0)
        f_s = np.interp(ta, tg, fo)
        a_s = np.interp(ta, tg, amp)
        phase = 2 * np.pi * np.cumsum(f_s) / fs
        for k, hk in enumerate(harm, start=1):
            x += a_s * hk * np.sin(k * phase + rng.uniform(0, 2 * np.pi))
    x = x / (np.abs(x).max() * 1.05)
    wavfile.write(out / "rec.wav", fs, (x * 32767).astype(np.int16))

    lat, lon, _ = frame.to_geodetic(obs[0], obs[1], obs[2])
    truth = {"lat": float(lat), "lon": float(lon), "t0_reported": t0_reported, "clock_error_s": clock_err,
             "duration_s": dur, "ground_ortho_m": GROUND, "geoid_n": SYNTH_GEOID_N, "temp_c": 15.0,
             "center": CENTER, "audible": [s[1] for s in specs if s[-1] is not None]}
    (out / "truth.json").write_text(json.dumps(truth, indent=2))
    return truth


def _pink(n, rng):
    w = rng.normal(0, 1, n)
    W = np.fft.rfft(w)
    f = np.arange(len(W))
    W[1:] /= np.sqrt(f[1:])
    p = np.fft.irfft(W, n)
    return p / p.std()
