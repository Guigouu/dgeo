# SPDX-License-Identifier: AGPL-3.0-or-later
"""Full synthetic scenario: the true position must come out on top (~20 m expected)."""
import json

import pandas as pd

from dgeo.audio import load_wav, spectrogram
from dgeo.adsb import read_csv
from dgeo.cli import haversine
from dgeo.detect import detect
from dgeo.doppler import build_trajectories
from dgeo.geo import Dem, LocalFrame, speed_of_sound
from dgeo.search import SearchConfig, search
from dgeo.synth import make


def test_synthetic_scenario(tmp_path):
    t = make(str(tmp_path), seed=7)
    c = speed_of_sound(t["temp_c"])
    x, fs = load_wav(str(tmp_path / "rec.wav"))
    tracks = detect(spectrogram(x, fs), c)
    assert len(tracks) >= 2

    lat0, lon0 = t["center"]
    dem = Dem(constant_m=t["ground_ortho_m"], geoid_n=t["geoid_n"])
    frame = LocalFrame(lat0, lon0, float(dem.hae(lat0, lon0)))
    trajs = build_trajectories(read_csv(str(tmp_path / "flights.csv")), frame, qnh=1013.25, geoid_n=t["geoid_n"])
    cfg = SearchConfig(radius_km=5, coarse_m=300, refine_top=6)
    df = search(tracks, trajs, frame, dem, t["t0_reported"], t["duration_s"], c, cfg, log=lambda *_: None)
    err = haversine(df.lat.iloc[0], df.lon.iloc[0], t["lat"], t["lon"])
    assert err < 150, f"error {err:.0f} m"
    assert abs(df.dt.iloc[0] - t["clock_error_s"]) <= 0.5


def test_unknown_start_time(tmp_path):
    """Start time off by 2 minutes: the wide clock-offset scan must recover both position and time."""
    t = make(str(tmp_path), seed=7)
    c = speed_of_sound(t["temp_c"])
    x, fs = load_wav(str(tmp_path / "rec.wav"))
    tracks = detect(spectrogram(x, fs), c)
    lat0, lon0 = t["center"]
    dem = Dem(constant_m=t["ground_ortho_m"], geoid_n=t["geoid_n"])
    frame = LocalFrame(lat0, lon0, float(dem.hae(lat0, lon0)))
    trajs = build_trajectories(read_csv(str(tmp_path / "flights.csv")), frame, qnh=1013.25, geoid_n=t["geoid_n"])
    shift = 120.0
    cfg = SearchConfig(radius_km=4, coarse_m=300, refine_top=4, time_window_s=200, time_step_s=1.0)
    df = search(tracks, trajs, frame, dem, t["t0_reported"] + shift, t["duration_s"], c, cfg, log=lambda *_: None)
    err = haversine(df.lat.iloc[0], df.lon.iloc[0], t["lat"], t["lon"])
    assert err < 150, f"error {err:.0f} m"
    assert abs(df["dt"].iloc[0] - (t["clock_error_s"] - shift)) <= 0.75


def test_parallel_scoring_matches_serial(tmp_path):
    import numpy as np

    from dgeo.search import _score_parallel, observers, score_points

    t = make(str(tmp_path), seed=7)
    c = speed_of_sound(t["temp_c"])
    x, fs = load_wav(str(tmp_path / "rec.wav"))
    tracks = detect(spectrogram(x, fs), c)
    lat0, lon0 = t["center"]
    dem = Dem(constant_m=t["ground_ortho_m"], geoid_n=t["geoid_n"])
    frame = LocalFrame(lat0, lon0, float(dem.hae(lat0, lon0)))
    trajs = build_trajectories(read_csv(str(tmp_path / "flights.csv")), frame, qnh=1013.25, geoid_n=t["geoid_n"])
    E, N = np.meshgrid(np.arange(-3000, 3001, 600.0), np.arange(-3000, 3001, 600.0))
    _, _, obs = observers(frame, dem, E.ravel(), N.ravel(), 1.5)
    cfg = SearchConfig()
    a, da = score_points(obs, tracks, trajs, t["t0_reported"], t["duration_s"], c, cfg)
    b, db = _score_parallel(obs, tracks, trajs, t["t0_reported"], t["duration_s"], c, cfg, [], 2)
    assert np.allclose(a, b) and np.allclose(da["dt"], db["dt"])
    assert all(np.array_equal(x["flight"], y["flight"]) for x, y in zip(da["tracks"], db["tracks"]))
