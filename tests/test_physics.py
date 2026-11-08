# SPDX-License-Identifier: AGPL-3.0-or-later
import numpy as np

from dgeo.doppler import Trajectory, doppler_ratio, emission, straight_line_freq
from dgeo.geo import LocalFrame

C = 340.0


def straight(v=100.0, d_side=500.0, alt=600.0):
    t = np.arange(-300.0, 300.0 + 0.5, 1.0)
    p = np.stack([v * t, np.full_like(t, d_side), np.full_like(t, alt)], axis=-1)
    vel = np.tile([v, 0.0, 0.0], (len(t), 1))
    return Trajectory("x", t, p, vel)


def test_enu_roundtrip():
    fr = LocalFrame(39.8283, -98.5795, 700.0)
    lat, lon, h = fr.to_geodetic(np.array([1234.5, -8000.0]), np.array([-4321.0, 9000.0]), np.array([10.0, 1500.0]))
    enu = fr.to_enu(lat, lon, h)
    assert np.allclose(enu, [[1234.5, -4321.0, 10.0], [-8000.0, 9000.0, 1500.0]], atol=1e-4)


def test_retarded_time_is_consistent():
    tr = straight()
    obs = np.array([[0.0, 0.0, 0.0]])
    t_obs = np.linspace(-60, 60, 50)
    te, pos, _, R, _, valid = emission(tr, obs, t_obs, C)
    assert valid.all()
    assert np.allclose(t_obs - te[0], R[0] / C, atol=1e-5)


def test_numeric_matches_analytic_straight_line():
    v, side, alt = 100.0, 500.0, 600.0
    tr = straight(v, side, alt)
    obs = np.array([[0.0, 0.0, 0.0]])
    t_obs = np.linspace(-60, 60, 200)
    ratio, _, _ = doppler_ratio(tr, obs, t_obs, C)
    ref = straight_line_freq(t_obs, 1.0, v, np.hypot(side, alt), 0.0, C)
    assert np.max(np.abs(ratio[0] - ref)) < 1e-7


def test_naive_model_is_wrong():
    """Without retarded time, the curve is wrong (not merely shifted in time)."""
    tr = straight()
    obs = np.array([[0.0, 0.0, 0.0]])
    t_obs = np.linspace(-60, 60, 200)
    good, _, _ = doppler_ratio(tr, obs, t_obs, C)
    pos, vel = tr.at(t_obs)
    r = pos - obs[0]
    naive = C / (C + np.sum(r * vel, axis=-1) / np.linalg.norm(r, axis=-1))
    best = min(np.max(np.abs(np.interp(t_obs, t_obs + s, naive) - good[0])) for s in np.arange(-10, 10, 0.1))
    assert best > 0.01
