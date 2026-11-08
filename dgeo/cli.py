# SPDX-License-Identifier: AGPL-3.0-or-later
"""Command-line interface: python -m dgeo <command> --help"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from .timeguess import parse_iso

import numpy as np


def parse_time(s: str) -> float:
    try:
        return float(s)
    except ValueError:
        dt = parse_iso(s)
        if dt.tzinfo is None:
            raise SystemExit(f"--t0 '{s}': specify the time zone (e.g. 2025-06-01T12:00:00+02:00 or ...Z)")
        return dt.timestamp()


def _env(a):
    """Shared objects: local frame, DEM, speed of sound."""
    from .geo import Dem, LocalFrame, speed_of_sound

    dem = Dem(a.dem or None, constant_m=a.ground_elev, geoid_n=a.geoid_n)
    h0 = float(dem.hae(a.lat, a.lon))
    return LocalFrame(a.lat, a.lon, h0), dem, speed_of_sound(a.temp_c)


def _trajs(a, frame):
    from .adsb import read_csv
    from .doppler import build_trajectories

    df = read_csv(a.flights)
    tr = build_trajectories(df, frame, qnh=a.qnh, geoid_n=a.geoid_n, alt_source=a.alt_source)
    print(f"[adsb] {df['hex'].nunique()} aircraft, {len(tr)} trajectory segments", file=sys.stderr)
    return tr


def _spectro(a):
    from .audio import load_wav, spectrogram

    x, fs = load_wav(a.wav, a.fs)
    return spectrogram(x, fs, win_s=a.win, hop_s=a.hop, fmax=0.49 * a.fs)


def cmd_adsb_live(a):
    from .adsb import live

    live(a.lat, a.lon, a.radius_nm, a.out, a.provider, a.interval, a.duration)


def cmd_adsb_hist(a):
    from .adsb import hist

    t0 = parse_time(a.start)
    t1 = parse_time(a.end)
    df = hist(a.traces_dir, t0, t1, a.lat, a.lon, a.radius_km)
    df.to_csv(a.out, index=False)
    print(f"[hist] -> {a.out}", file=sys.stderr)


def cmd_spectro(a):
    from .plots import spectro_fig

    sp = _spectro(a)
    fig, _ = spectro_fig(sp, a.fmin_plot, a.fmax_plot, what=a.what, title=Path(a.wav).name)
    fig.savefig(a.out, dpi=110, bbox_inches="tight")
    print(f"-> {a.out}")


def cmd_detect(a):
    from .detect import detect, to_frame
    from .geo import speed_of_sound
    from .plots import overlay_fig

    sp = _spectro(a)
    c = speed_of_sound(a.temp_c)
    tr = detect(sp, c, fmin=a.fmin, fmax=a.fmax, thr=a.thr, min_dur_s=a.min_dur)
    to_frame(tr).to_csv(a.out, index=False)
    from .events import detect_events, to_frame as events_frame

    ev = detect_events(sp, exclude=[(t.t[0], t.t[-1]) for t in tr])
    ev_out = Path(a.events_out) if a.events_out else Path(a.out).with_name("events.csv")
    events_frame(ev).to_csv(ev_out, index=False)
    print(f"  loudness events (broadband, outside tracks): {[round(e.t) for e in ev]} -> {ev_out}")
    fits = [{"track": t.id, "t_start": float(t.t[0]), "t_end": float(t.t[-1]), **t.fit} for t in tr]
    Path(a.out).with_suffix(".fit.json").write_text(json.dumps(fits, indent=2))
    for f in fits:
        print(f"  track {f['track']}: {f['t_start']:.0f}-{f['t_end']:.0f}s  f0≈{f['f0']:.1f}Hz  "
              f"v≈{f['v']:.0f}m/s  d_cpa≈{f['d']:.0f}m  CPA heard at {f['t_cpa_heard']:.1f}s  "
              f"rms {f['rms_pct']:.2f}%")
    if a.png:
        overlay_fig(sp, [], tr, 0, min(a.fmax * 1.5, 0.49 * a.fs), out=a.png, title="detected tracks")
    print(f"-> {a.out} ({len(tr)} tracks)")


def cmd_overlay(a):
    from .detect import from_frame
    from .plots import overlay_fig
    from .search import observers
    import pandas as pd

    frame, dem, c = _env(a)
    trajs = _trajs(a, frame)
    sp = _spectro(a)
    _, _, obs = observers(frame, dem, np.array([0.0]), np.array([0.0]), a.obs_agl)
    curves, dt = overlay_curves(sp, trajs, obs[0], parse_time(a.t0), c, a.max_range, a.dt_max,
                                fmin=a.fmin, fmax=a.fmax)
    tracks = from_frame(pd.read_csv(a.tracks)) if a.tracks else None
    for cv in sorted(curves, key=lambda x: x["cpa_heard_s"]):
        print(f"  {cv['label']:<22} CPA {cv['cpa_m']:6.0f} m at {cv['cpa_heard_s']:6.1f}s  "
              f"f0 {cv['f0']:6.1f} Hz  score {cv['score']:.2f}")
    overlay_fig(sp, curves, tracks, 0, min(a.fmax * 1.5, 0.49 * a.fs), out=a.out,
                title=f"predicted curves at the known position (clock dt {dt:+.2f}s)")
    print(f"-> {a.out}")


def overlay_curves(*args, **kw):
    from .search import overlay_curves as oc

    return oc(*args, **kw)


def cmd_search(a):
    from .detect import from_frame
    from .plots import heatmap_fig
    from .search import SearchConfig, search
    import pandas as pd

    frame, dem, c = _env(a)
    trajs = _trajs(a, frame)
    tracks = from_frame(pd.read_csv(a.tracks)) if a.tracks else []
    events = []
    if a.events:
        from .events import from_frame as events_from_frame

        events = events_from_frame(pd.read_csv(a.events))
    dur = a.duration
    if dur is None:
        if not a.wav:
            raise SystemExit("--duration or --wav is required")
        from .audio import load_wav
        x, fs = load_wav(a.wav, 8000)
        dur = len(x) / fs
    cfg = SearchConfig(radius_km=a.radius_km, coarse_m=a.coarse, fine_m=a.fine, dt_max=a.dt_max,
                       max_range_m=a.max_range, audible_m=a.audible, obs_agl=a.obs_agl,
                       event_weight=a.event_weight)
    df = search(tracks, trajs, frame, dem, parse_time(a.t0), dur, c, cfg,
                log=lambda m: print(m, file=sys.stderr), events=events)
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    df.to_csv(out / "candidates.csv", index=False)
    _geojson(df.head(50), out / "top50.geojson")
    truth = json.loads(Path(a.truth).read_text()) if a.truth else None
    heatmap_fig(df, truth, out=out / "heatmap.png", title="cost per grid point (track mode)")
    _report(df, truth, coarse_m=a.coarse)
    print(f"-> {out}/candidates.csv, top50.geojson, heatmap.png")


def _geojson(df, path):
    feats = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [r.lon, r.lat]},
              "properties": {"rank": i + 1, "cost": float(r.cost), "dt": float(r.dt)}}
             for i, r in enumerate(df.itertuples())]
    Path(path).write_text(json.dumps({"type": "FeatureCollection", "features": feats}))


def haversine(lat1, lon1, lat2, lon2):
    p = np.radians
    a = np.sin(p(lat2 - lat1) / 2) ** 2 + np.cos(p(lat1)) * np.cos(p(lat2)) * np.sin(p(lon2 - lon1) / 2) ** 2
    return 2 * 6371000 * np.arcsin(np.sqrt(a))


def _report(df, truth=None, coarse_m: float = 250.0):
    from .search import candidate_area

    trk_cols = [c[:-7] for c in df.columns if c.endswith("_flight") and c.startswith("trk")]
    print("\n rank   lat        lon        cost   dt    silences  matched flights")
    for i, r in enumerate(df.head(5).itertuples()):
        fl = ", ".join(str(getattr(r, f"{k}_flight")) for k in trk_cols)
        print(f" {i+1:>4}  {r.lat:.5f}  {r.lon:.5f}  {r.cost:6.2f}  {r.dt:+.2f}  {r.silence_violations:>4}     {fl}")
    b = df.iloc[0]
    far = df[haversine(df.lat, df.lon, b.lat, b.lon) > 300]
    if len(far):
        f = far.iloc[0]
        print(f"\n 2nd distinct zone (>300 m): cost {f.cost:.2f} at {haversine(f.lat, f.lon, b.lat, b.lon):.0f} m "
              f"-> margin {f.cost - b.cost:.2f} (ratio {f.cost / max(b.cost, 1e-6):.1f}x)")
    for d, (area, radius) in candidate_area(df, coarse_m).items():
        print(f" candidate area (cost <= min + {d}): {area:.2f} km², up to {radius:.0f} m from the best point")
    if "event_lag" in df.columns and any(c.startswith("ev") and c.endswith("_flight") for c in df.columns):
        evs = [str(b[c]) or "-" for c in df.columns if c.startswith("ev") and c.endswith("_flight")]
        print(f" loudness events: lag {b.event_lag:+.0f} s, matched: {', '.join(evs)}")
    if truth:
        e = haversine(df.lat.iloc[0], df.lon.iloc[0], truth["lat"], truth["lon"])
        print(f" ERROR vs ground truth: {e:.0f} m")
        return e


def cmd_synth(a):
    from .synth import make

    t = make(a.out_dir, seed=a.seed, broadband_jets=a.broadband_jets)
    print(json.dumps(t, indent=2))


def cmd_demo(a):
    """Synthesis -> detection -> overlay -> search, with an error check against ground truth."""
    from .synth import make

    d = Path(a.out_dir)
    t = make(str(d), seed=a.seed)
    lat, lon = t["center"]
    common = ["--lat", str(lat), "--lon", str(lon), "--ground-elev", str(t["ground_ortho_m"]),
              "--geoid-n", str(t["geoid_n"]), "--temp-c", str(t["temp_c"])]
    main(["detect", "--wav", str(d / "rec.wav"), "--out", str(d / "tracks.csv"), "--png", str(d / "tracks.png")])
    main(["overlay", "--wav", str(d / "rec.wav"), "--flights", str(d / "flights.csv"),
          "--t0", str(t["t0_reported"]), "--tracks", str(d / "tracks.csv"), "--out", str(d / "overlay.png"),
          "--lat", str(t["lat"]), "--lon", str(t["lon"]), "--ground-elev", str(t["ground_ortho_m"]),
          "--geoid-n", str(t["geoid_n"]), "--temp-c", str(t["temp_c"])])
    main(["search", "--tracks", str(d / "tracks.csv"), "--flights", str(d / "flights.csv"),
          "--t0", str(t["t0_reported"]), "--duration", str(t["duration_s"]), "--radius-km", "8",
          "--out-dir", str(d / "search"), "--truth", str(d / "truth.json"), *common])


def build_parser():
    p = argparse.ArgumentParser(prog="dgeo", description="Acoustic geolocation POC: aircraft Doppler curves + ADS-B")
    sub = p.add_subparsers(dest="cmd", required=True)

    def geo_args(s, center_help):
        s.add_argument("--lat", type=float, required=True, help=center_help)
        s.add_argument("--lon", type=float, required=True)
        s.add_argument("--dem", nargs="*", help="Copernicus GLO-30 GeoTIFF tile(s)")
        s.add_argument("--ground-elev", type=float, default=0.0,
                       help="constant ground elevation when no DEM is given (m, orthometric)")
        s.add_argument("--geoid-n", type=float, default=0.0, help="geoid undulation N (m) at your location (EGM2008); default 0")
        s.add_argument("--qnh", type=float, default=1013.25, help="QNH (hPa), e.g. from the nearest airport METAR")
        s.add_argument("--alt-source", choices=["auto", "geom", "baro"], default="auto")
        s.add_argument("--temp-c", type=float, default=15.0, help="air temperature (°C)")
        s.add_argument("--obs-agl", type=float, default=1.5, help="microphone height above ground (m)")

    def audio_args(s, required=True):
        s.add_argument("--wav", required=required)
        s.add_argument("--fs", type=int, default=8000)
        s.add_argument("--win", type=float, default=1.0, help="STFT window (s)")
        s.add_argument("--hop", type=float, default=0.25, help="STFT hop (s)")
        s.add_argument("--fmin", type=float, default=40.0, help="minimum fundamental searched (Hz)")
        s.add_argument("--fmax", type=float, default=1500.0,
                       help="maximum fundamental searched (Hz); jets: lines up to ~1500 Hz")

    s = sub.add_parser("adsb-live", help="capture live ADS-B while recording")
    s.add_argument("--lat", type=float, required=True)
    s.add_argument("--lon", type=float, required=True)
    s.add_argument("--radius-nm", type=float, default=15)
    s.add_argument("--provider", choices=["both", "adsblol", "airplaneslive"], default="both",
                   help="both = alternate between the two networks (recommended)")
    s.add_argument("--interval", type=float, default=3.0, help="base polling interval per provider (s)")
    s.add_argument("--duration", type=float, help="duration (s); otherwise run until Ctrl-C")
    s.add_argument("--out", default="flights.csv")
    s.set_defaults(fn=cmd_adsb_live)

    s = sub.add_parser("adsb-hist", help="extract an area/time window from adsb.lol globe_history traces")
    s.add_argument("--traces-dir", required=True)
    s.add_argument("--start", required=True)
    s.add_argument("--end", required=True)
    s.add_argument("--lat", type=float, required=True)
    s.add_argument("--lon", type=float, required=True)
    s.add_argument("--radius-km", type=float, default=30)
    s.add_argument("--out", default="flights.csv")
    s.set_defaults(fn=cmd_adsb_hist)

    s = sub.add_parser("spectro", help="spectrogram PNG (to spot / trace tracks by hand)")
    audio_args(s)
    s.add_argument("--fmin-plot", type=float, default=0)
    s.add_argument("--fmax-plot", type=float, default=1500)
    s.add_argument("--what", choices=["z", "db"], default="z")
    s.add_argument("--temp-c", type=float, default=15.0)
    s.add_argument("--out", default="spectro.png")
    s.set_defaults(fn=cmd_spectro)

    s = sub.add_parser("detect", help="automatically extract Doppler tracks")
    audio_args(s)
    s.add_argument("--thr", type=float, default=2.5, help="detection threshold (z)")
    s.add_argument("--min-dur", type=float, default=15.0, help="minimum track duration (s)")
    s.add_argument("--temp-c", type=float, default=15.0)
    s.add_argument("--out", default="tracks.csv")
    s.add_argument("--events-out", help="loudness events CSV (default: events.csv next to --out)")
    s.add_argument("--png")
    s.set_defaults(fn=cmd_detect)

    s = sub.add_parser("overlay", help="PHASE 1: predicted curves at a KNOWN position")
    geo_args(s, "known recording position")
    audio_args(s)
    s.add_argument("--flights", required=True)
    s.add_argument("--t0", required=True, help="recording start time (ISO with time zone, or unix)")
    s.add_argument("--tracks", help="detected tracks to overlay")
    s.add_argument("--max-range", type=float, default=8000)
    s.add_argument("--dt-max", type=float, default=5.0)
    s.add_argument("--out", default="overlay.png")
    s.set_defaults(fn=cmd_overlay)

    s = sub.add_parser("search", help="PHASE 2: grid search for the recording position")
    geo_args(s, "centre of the search area")
    s.add_argument("--wav", help="(used for the duration if --duration is not given)")
    s.add_argument("--duration", type=float)
    s.add_argument("--tracks", help="tonal tracks CSV (from detect)")
    s.add_argument("--events", help="loudness events CSV (from detect) — EXPERIMENTAL, see docs/METHOD.md")
    s.add_argument("--event-weight", type=float, default=0.25, help="weight of the loudness-event term")
    s.add_argument("--flights", required=True)
    s.add_argument("--t0", required=True)
    s.add_argument("--radius-km", type=float, default=10)
    s.add_argument("--coarse", type=float, default=250)
    s.add_argument("--fine", type=float, default=25)
    s.add_argument("--dt-max", type=float, default=3.0)
    s.add_argument("--max-range", type=float, default=8000)
    s.add_argument("--audible", type=float, default=2000)
    s.add_argument("--truth", help="truth.json (to compute the position error)")
    s.add_argument("--out-dir", default="out")
    s.set_defaults(fn=cmd_search)

    s = sub.add_parser("synth", help="generate a synthetic scenario")
    s.add_argument("--out-dir", default="synth")
    s.add_argument("--seed", type=int, default=1)
    s.add_argument("--broadband-jets", action="store_true", help="add jets rendered as broadband noise (600 s)")
    s.set_defaults(fn=cmd_synth)

    s = sub.add_parser("demo", help="end-to-end synthetic scenario")
    s.add_argument("--out-dir", default="demo")
    s.add_argument("--seed", type=int, default=1)
    s.set_defaults(fn=cmd_demo)
    return p


def main(argv=None):
    a = build_parser().parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
