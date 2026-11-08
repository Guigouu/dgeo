# dgeo — acoustic geolocation from aircraft Doppler curves

[![CI](https://github.com/Guigouu/dgeo/actions/workflows/ci.yml/badge.svg)](https://github.com/Guigouu/dgeo/actions/workflows/ci.yml)
[![License: AGPL-3.0-or-later](https://img.shields.io/badge/license-AGPL--3.0--or--later-blue.svg)](LICENSE)

**Proof of concept.** dgeo estimates where an audio recording was made by matching the Doppler
curves of aircraft heard in it against public ADS-B flight trajectories.

```
audio ──► whitened spectrogram ──► Doppler tracks (t, f) ─┐
                                                          ├─► cost per grid point ─► map + top candidates
ADS-B ──► smoothed ENU trajectories ──► predicted curve ──┘   (free f0, shared clock offset, silence filter)
```

> [!WARNING]
> **Disclaimer — educational and research use only.** dgeo is a proof of concept published to
> study and illustrate how much location information leaks through background aircraft noise. It is
> not a product, comes with no warranty of accuracy, and must not be used to locate, track, identify
> or expose any person, nor for any unlawful purpose. You are solely responsible for how you use it and
> for complying with applicable laws (privacy, data protection, recording consent) and with the terms
> of the data sources. The authors accept no liability for any misuse. See [Responsible use](#responsible-use).

## Credits

The method is a re-implementation of the technique presented by **Colsto** in
[“I discovered a sound that leaks your exact location (and it's everywhere)”](https://www.youtube.com/watch?v=UytYrhYLOKI):
spectrogram → Doppler curves → forward model with retarded time → grid search with a locked
time offset and a silence filter. All credit for the idea and the original demonstration goes to
the author of the video. This project is an independent implementation and is not affiliated with
the video's author.

## Status

| Phase | Goal | Status |
|---|---|---|
| 0. Synthetic benchmark | Validate the pipeline end to end | ✅ 15–30 m error, clock offset recovered to ±0.25 s (`make test`) |
| 1. Field test, known position | Predicted curves must overlay observed ones | ✅ A jet tone overlaid within ~2 s of clock offset |
| 2. Search at known time | Recover one's own position | 🟡 One tonal pass gives a ~2 km plateau; the experimental loudness-event term (jets) shrank it ~4× on a field recording; needs more field data |
| 3. Robustness | Indoors, phone calls, jets only, … | to do |
| 4. Extensions | Unknown time, historical data, wind, distributed search | 🟡 Implemented, validated on synthetic data only: unknown start time (`--time-window`), streamed historical ADS-B (`adsb-hist --tar`), uniform wind, multi-process and tiled search (`--workers`, `dgeo merge`) |

## Responsible use

This tool can reveal where a recording was made. Use it **only on recordings you made yourself, or
with the informed consent of the people involved**, and in compliance with the law (in the EU, a
location linked to a person is personal data under the GDPR). Do not use it to locate, track or
expose people. If you publish results, do not publish your ground-truth positions.

The flip side is useful: background aircraft noise survives high-pass filtering, other sounds and
phone-call compression. Cutting a recording into pieces or strong noise suppression make the method
much harder to apply.

## Quick start (Docker)

```bash
git clone https://github.com/Guigouu/dgeo.git && cd dgeo
make up                 # = docker compose up -d --build
# UI: http://localhost:8501
```

In the UI: sidebar → **Generate a test scenario**, then the **Detection** → **Overlay** → **Search** tabs. You should recover the
synthetic position within a few tens of metres.

Other targets: `make test` (pytest in the container), `make demo` (CLI demo, outputs in `./data/demo`),
`make logs`, `make down`. Data persists in `./data` (mounted at `/data`).

Pre-built image (from the GitHub Releases):

```bash
docker run --rm -p 127.0.0.1:8501:8501 -v "$PWD/data:/data" ghcr.io/guigouu/dgeo:latest
```

Without Docker (Python ≥ 3.10):

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[ui,dem,dev]"      # ffmpeg is also needed to import m4a/mp3
make local-ui
```

> The UI has no authentication; the port is bound to `127.0.0.1` by default. If you expose it,
> remember that the AGPL requires you to offer the source code to its users: the sidebar links to it
> (set `DGEO_SOURCE_URL` to your fork's URL).

## The interface

| Tab | Purpose |
|---|---|
| ADS-B | Start/stop a live capture (adsb.lol and/or airplanes.live) inside the container, or import a `flights.csv`. |
| Audio | Import (wav/m4a/mp3/… converted with ffmpeg), start time (pre-filled from file metadata), spectrogram. |
| Detection | Automatic track extraction + fit (f0, speed, CPA distance). Hand-traced tracks can be imported as CSV. |
| Overlay | **Phase 1** (optional): at a known position, predicted curves over the spectrogram + estimated clock offset. |
| Search | **Phase 2**: grid search, map with cost classes and toggleable layers, top candidates, CSV export. |

Physical parameters (sidebar): air temperature, QNH, ground elevation or a DEM GeoTIFF tile, geoid
undulation. Defaults are sea level and N = 0: **set them for your area**, an error of a few tens of
metres in heights distorts the predicted curves.

## Command line

Everything the UI does is available from the CLI (`dgeo <command> --help`, or `python -m dgeo`):

```bash
# while recording (Ctrl-C to stop)
dgeo adsb-live --lat <lat> --lon <lon> --radius-nm 15 --out flights.csv

ffmpeg -i rec.m4a -ac 1 -ar 16000 rec.wav
dgeo spectro --wav rec.wav --out spectro.png
dgeo detect  --wav rec.wav --out tracks.csv --png tracks.png   # also writes events.csv (loudness events)

# phase 1: known position
dgeo overlay --wav rec.wav --flights flights.csv --t0 2026-01-15T10:15:03+01:00 \
    --lat <lat> --lon <lon> --tracks tracks.csv --out overlay.png

# phase 2: search
dgeo search --tracks tracks.csv --flights flights.csv --wav rec.wav \
    --t0 2026-01-15T10:15:03+01:00 --lat <lat> --lon <lon> --radius-km 10 --out-dir out/
#   add --events events.csv to also use broadband jet passes (experimental)
#   add --time-window 600 if the start time is only known to ±10 min; --workers 4 to use 4 CPUs

# past recording: stream a day of adsb.lol history and keep only your area/time window (see FIELD_GUIDE)
dgeo adsb-hist --tar day.tar.a* --start <iso> --end <iso> --lat <lat> --lon <lon> --out flights.csv

# synthetic end-to-end demo (dgeo synth --broadband-jets adds jets heard as noise only)
dgeo demo --out-dir demo/
```

## Documentation

- [docs/METHOD.md](docs/METHOD.md) — physics and algorithms (frames, retarded time, detection, cost, search).
- [docs/FIELD_GUIDE.md](docs/FIELD_GUIDE.md) — how to make useful recordings, clock sync, supporting data, diagnosis.
- [THIRD_PARTY.md](THIRD_PARTY.md) — dependency licences and data-source terms.

## Project layout

```
dgeo/
  geo.py        WGS84/ECEF/ENU, DEM (GeoTIFF), altitudes (geom/baro/QNH/geoid), speed of sound
  adsb.py       live capture (v2 API, adaptive rate limiting) + adsb.lol globe_history reader
  doppler.py    smoothed trajectories, retarded time (Newton), f/f0 ratio, CPA, straight-line model
  audio.py      WAV → whitened spectrogram (z-scores), log-frequency harmonic sum
  detect.py     track extraction, (f0, v, d, tc) fit, harmonic de-duplication
  events.py     loudness events (broadband passes, mostly jets)
  score.py      track↔curve cost (closed-form f0) and tonal-contrast score on the spectrogram
  search.py     overlay (phase 1), coarse→fine search with shared clock offset and silence filter (phase 2)
  timeguess.py  start time from file metadata (start/end hypotheses)
  synth.py      realistic synthetic scenario (turns, deceleration, noise, hum, gusts)
  plots.py      figures
  cli.py        command line
app/app.py      Streamlit interface
tests/          physics, metadata timing, end to end
```

## Known limitations

- **Wind** is modelled as one uniform vector from the surface report (`--wind-speed`, `--wind-from`, sidebar); wind aloft is not.
- **Light aviation**: in Europe ADS-B Out is only mandatory above 5.7 t or 250 kt; many small aircraft
  do not broadcast positions. Turboprops, equipped helicopters and jets are the useful targets.
- **Jets** are mostly broadband noise, which tone detection ignores; only their occasional tones are used.
- **Live ADS-B timing**: position time is reconstructed as `now − seen_pos` (~0.1–0.5 s).
- **Phone processing** (noise suppression, AGC, lossy codecs) can remove faint tones.
- A single pass only constrains the position to a ~2 km strip.

## Releases

Versions follow [semantic versioning](https://semver.org/) (0.x: anything may change). Releases are
automatic: when `main` contains a version with no `vX.Y.Z` tag yet, the release workflow runs the tests,
builds the wheel and the Docker image (`ghcr.io/guigouu/dgeo`), creates the tag and publishes the
GitHub Release with the matching `CHANGELOG.md` section.

One release per merged feature or fix:

| Change | Version bump | Example |
|---|---|---|
| new feature (`feat`) | minor | 0.2.0 → 0.3.0 |
| bug fix (`fix`) | patch | 0.3.0 → 0.3.1 |
| docs, tests, CI only | none: listed under `## Unreleased`, shipped with the next release | |

```bash
python scripts/bump_version.py minor   # or patch: pyproject.toml + dgeo/__init__.py, Unreleased -> X.Y.Z
# commit with the PR; merging it into main publishes the release
```

## Contributing

Issues and pull requests are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md). Please never include
real recordings, ADS-B captures or coordinates that could identify a person's location in issues or
test fixtures.

## Licence

Copyright © 2026 the dgeo contributors.

dgeo is free software: you can redistribute it and/or modify it under the terms of the
[GNU Affero General Public License](LICENSE) as published by the Free Software Foundation, either
version 3 of the License, or (at your option) any later version. It is distributed WITHOUT ANY
WARRANTY. If you run a modified version as a network service, you must offer its source code to its
users (AGPL section 13).

ADS-B data © adsb.lol contributors (ODbL 1.0) — see [THIRD_PARTY.md](THIRD_PARTY.md) for data
attributions.
