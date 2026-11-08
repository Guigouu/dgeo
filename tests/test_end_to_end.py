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
