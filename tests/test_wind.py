# SPDX-License-Identifier: AGPL-3.0-or-later
"""Uniform wind in the forward model."""
import numpy as np

from dgeo.doppler import Trajectory, doppler_ratio, emission
from dgeo.geo import wind_vector

C = 340.0


def line(v=(100.0, 0.0, 0.0), y=500.0, z=600.0):
    t = np.arange(-300.0, 300.5, 1.0)
    v = np.asarray(v, float)
    p = np.stack([v[0] * t, v[1] * t + y, np.full_like(t, z)], axis=-1)
    return Trajectory("x", t, p, np.tile(v, (len(t), 1)))


def test_wind_vector_convention():
    assert np.allclose(wind_vector(10, 270), [10, 0, 0], atol=1e-9)   # from the west -> blows east
    assert np.allclose(wind_vector(5, 0), [0, -5, 0], atol=1e-9)      # from the north -> blows south


def test_static_source_has_no_doppler_in_wind():
    tr = line(v=(0.0, 0.0, 0.0))
    ratio, _, _ = doppler_ratio(tr, np.array([[0.0, 0.0, 0.0]]), np.linspace(-50, 50, 20), C, wind=(15.0, 5.0, 0.0))
    assert np.allclose(ratio, 1.0)


def test_travel_time_with_wind():
    """Arrival time = emission time + R / (c + w.n), n = source -> observer."""
    tr = line(v=(0.0, 0.0, 0.0), y=3000.0, z=0.0)        # static source 3 km north of the observer
    w = np.array([0.0, -10.0, 0.0])                       # wind blowing south: towards the observer
    te, _, _, R, _, _ = emission(tr, np.array([[0.0, 0.0, 0.0]]), np.array([0.0]), C, wind=w)
    assert abs(-te[0, 0] - 3000.0 / (C + 10.0)) < 1e-6


def test_wind_changes_the_curve_and_zero_wind_is_neutral():
    tr = line()
    obs = np.array([[0.0, 0.0, 0.0]])
    t = np.linspace(-40, 40, 81)
    r0, _, _ = doppler_ratio(tr, obs, t, C)
    rz, _, _ = doppler_ratio(tr, obs, t, C, wind=(0.0, 0.0, 0.0))
    rw, _, _ = doppler_ratio(tr, obs, t, C, wind=(15.0, 0.0, 0.0))
    assert np.allclose(r0, rz)
    assert np.max(np.abs(rw - r0)) > 2e-3      # 15 m/s along track: a measurable change
