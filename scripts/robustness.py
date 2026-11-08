# SPDX-License-Identifier: AGPL-3.0-or-later
"""Robustness benchmark (phase 3): how much of the pipeline survives realistic audio degradations?

Synthetic scenarios are degraded (lossy codecs, phone-call band and codec, high-pass, noise suppression,
indoor attenuation and reverberation, speech masking), then detection + search are run on each.

    python scripts/robustness.py --seeds 1 2 3 --out robustness.md      # needs ffmpeg (with libopus)

Writes a Markdown table: tracks found, position error and candidate area per degradation.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
from scipy.io import wavfile
from scipy.signal import butter, sosfilt

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dgeo.adsb import read_csv  # noqa: E402
from dgeo.audio import load_wav, spectrogram  # noqa: E402
from dgeo.cli import haversine  # noqa: E402
from dgeo.detect import detect  # noqa: E402
from dgeo.doppler import build_trajectories  # noqa: E402
from dgeo.events import detect_events  # noqa: E402
from dgeo.geo import Dem, LocalFrame, speed_of_sound  # noqa: E402
from dgeo.search import SearchConfig, candidate_area, search  # noqa: E402
from dgeo.synth import make  # noqa: E402

# name -> (description, ffmpeg arguments applied between input and output, or a python function)
FFMPEG = {
    "clean": ("reference, no degradation", None),
    "aac_64k": ("AAC 64 kbit/s (typical phone recorder)", ["-c:a", "aac", "-b:a", "64k"]),
    "mp3_32k": ("MP3 32 kbit/s (heavily compressed upload)", ["-c:a", "libmp3lame", "-b:a", "32k"]),
    "phone_call": ("phone call: 300–3400 Hz band + Opus 12 kbit/s VoIP",
                   ["-af", "highpass=f=300,lowpass=f=3400", "-c:a", "libopus", "-b:a", "12k",
                    "-application", "voip"]),
    "highpass_300": ("high-pass filter at 300 Hz (\"remove the hum\")", ["-af", "highpass=f=300:poles=2"]),
    "noise_suppression": ("FFT noise suppression (like a phone's denoiser)", ["-af", "afftdn=nr=24:nf=-30"]),
    "indoor": ("indoors: walls (low-pass 800 Hz, −15 dB) + room reverberation",
               ["-af", "lowpass=f=800,volume=-15dB,aecho=0.8:0.6:40|70:0.4|0.25"]),
}


def speech_masking(x: np.ndarray, fs: int, rng) -> np.ndarray:
    """Speech-like masker: 300–3400 Hz noise with 4 Hz syllabic modulation, 10 dB above the recording RMS."""
    sos = butter(4, [300, min(3400, 0.45 * fs)], btype="band", fs=fs, output="sos")
    n = sosfilt(sos, rng.normal(0, 1, len(x)))
    env = 0.5 * (1 + np.sin(2 * np.pi * 4 * np.arange(len(x)) / fs + rng.uniform(0, 6.28))) ** 2
    masker = n * env
    masker *= 10 ** (10 / 20) * np.sqrt(np.mean(x ** 2)) / (np.sqrt(np.mean(masker ** 2)) + 1e-12)
    return x + masker


PYTHON = {"speech_masking": ("speech-like masker 10 dB louder than the recording", speech_masking)}


def degrade(src: Path, dst: Path, name: str, rng):
    if name in PYTHON:
        fs, x = wavfile.read(src)
        y = PYTHON[name][1](x.astype(np.float64) / 32768, fs, rng)
        y = y / (np.abs(y).max() * 1.05)
        wavfile.write(dst, fs, (y * 32767).astype(np.int16))
        return
    args = FFMPEG[name][1]
    if args is None:
        shutil.copy(src, dst)
        return
    enc = dst.with_suffix(".ogg" if "libopus" in args else ".m4a" if "aac" in args else
                          ".mp3" if "libmp3lame" in args else ".wav")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), *args, str(enc)], check=True)
    if enc != dst:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(enc), "-ac", "1", "-ar", "8000",
                        str(dst)], check=True)


def run_case(d: Path, wav: Path, t: dict, use_events: bool):
    c = speed_of_sound(t["temp_c"])
    x, fs = load_wav(str(wav), 8000)
    sp = spectrogram(x, fs, fmax=3900)
    tracks = detect(sp, c)
    events = detect_events(sp, exclude=[(tr.t[0], tr.t[-1]) for tr in tracks]) if use_events else []
    lat0, lon0 = t["center"]
    dem = Dem(constant_m=t["ground_ortho_m"], geoid_n=t["geoid_n"])
    frame = LocalFrame(lat0, lon0, float(dem.hae(lat0, lon0)))
    trajs = build_trajectories(read_csv(str(d / "flights.csv")), frame, qnh=1013.25, geoid_n=t["geoid_n"])
    if not tracks and not events:
        return len(tracks), len(events), None, None
    cfg = SearchConfig(radius_km=5, coarse_m=300, refine_top=6)
    df = search(tracks, trajs, frame, dem, t["t0_reported"], t["duration_s"], c, cfg, log=lambda *_: None,
                events=events)
    err = haversine(df.lat.iloc[0], df.lon.iloc[0], t["lat"], t["lon"])
    return len(tracks), len(events), float(err), candidate_area(df, cfg.coarse_m)[0.3][0]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seeds", type=int, nargs="+", default=[1, 2, 3])
    ap.add_argument("--cases", nargs="+", default=list(FFMPEG) + list(PYTHON))
    ap.add_argument("--broadband-jets", action="store_true", help="also add broadband jets and use events")
    ap.add_argument("--out", default="robustness.md")
    a = ap.parse_args()
    rng = np.random.default_rng(0)
    rows = {}
    with tempfile.TemporaryDirectory() as tmp:
        for seed in a.seeds:
            d = Path(tmp) / f"s{seed}"
            t = make(str(d), seed=seed, broadband_jets=a.broadband_jets)
            for name in a.cases:
                t1 = time.time()
                wav = d / f"{name}.wav"
                degrade(d / "rec.wav", wav, name, rng)
                res = run_case(d, wav, t, a.broadband_jets)
                rows.setdefault(name, []).append(res)
                print(f"seed {seed} {name:18s} tracks {res[0]} events {res[1]} "
                      f"err {res[2] if res[2] is None else round(res[2])} m  ({time.time() - t1:.0f}s)", file=sys.stderr)
    desc = {**{k: v[0] for k, v in FFMPEG.items()}, **{k: v[0] for k, v in PYTHON.items()}}
    lines = ["| degradation | description | tracks found | median error | runs < 150 m | median area Δ≤0.3 |",
             "|---|---|---|---|---|---|"]
    for name, res in rows.items():
        errs = [r[2] for r in res if r[2] is not None]
        areas = [r[3] for r in res if r[3] is not None]
        tr = "/".join(str(r[0]) for r in res)
        med = f"{np.median(errs):.0f} m" if errs else "—"
        ok = sum(e < 150 for e in errs)
        ar = f"{np.median(areas):.2f} km²" if areas else "—"
        lines.append(f"| `{name}` | {desc[name]} | {tr} | {med} | {ok}/{len(res)} | {ar} |")
    table = "\n".join(lines)
    Path(a.out).write_text(table + "\n")
    print(table)
    Path(a.out).with_suffix(".json").write_text(json.dumps(rows, indent=1))


if __name__ == "__main__":
    main()
