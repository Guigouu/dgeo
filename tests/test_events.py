# SPDX-License-Identifier: AGPL-3.0-or-later
"""Loudness events (broadband jets): detection and their contribution to the search."""
from dgeo.adsb import read_csv
from dgeo.audio import load_wav, spectrogram
from dgeo.cli import haversine
from dgeo.detect import detect
from dgeo.doppler import build_trajectories
from dgeo.events import detect_events
from dgeo.geo import Dem, LocalFrame, speed_of_sound
from dgeo.search import SearchConfig, candidate_area, search
from dgeo.synth import make


def test_events_detected_and_used(tmp_path):
    t = make(str(tmp_path), seed=7, broadband_jets=True)
    c = speed_of_sound(t["temp_c"])
    x, fs = load_wav(str(tmp_path / "rec.wav"))
    sp = spectrogram(x, fs, fmax=3900)
    tracks = detect(sp, c)
    events = detect_events(sp, exclude=[(tr.t[0], tr.t[-1]) for tr in tracks])
    # the four broadband jets are heard at ~181, 316, 414 and 527 s
    assert sum(any(abs(e.t - h) < 8 for e in events) for h in (181, 316, 414, 527)) >= 3

    lat0, lon0 = t["center"]
    dem = Dem(constant_m=t["ground_ortho_m"], geoid_n=t["geoid_n"])
    frame = LocalFrame(lat0, lon0, float(dem.hae(lat0, lon0)))
    trajs = build_trajectories(read_csv(str(tmp_path / "flights.csv")), frame, qnh=1013.25, geoid_n=t["geoid_n"])
    cfg = SearchConfig(radius_km=5, coarse_m=300, refine_top=6)
    # a single tonal track + the loudness events must still find the right place
    df = search(tracks[:1], trajs, frame, dem, t["t0_reported"], t["duration_s"], c, cfg,
                log=lambda *_: None, events=events)
    assert haversine(df.lat.iloc[0], df.lon.iloc[0], t["lat"], t["lon"]) < 150
    assert abs(df.event_lag.iloc[0]) <= 6
    area, radius = candidate_area(df, cfg.coarse_m)[0.3]
    assert area > 0 and radius < 2000
