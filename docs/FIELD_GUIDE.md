# Field guide

How to make recordings that give the method something to work with. Record **your own** audio, at
a place you are entitled to record, and keep your ground-truth position private.

## What you want to hear

| Source | Why | Notes |
|---|---|---|
| Commercial turboprops (e.g. ATR 72, Dash 8) | Clean propeller tones, always ADS-B equipped | Best candidates |
| Helicopters | Very strong tones | Only those broadcasting ADS-B positions |
| Jets on approach / departure | Always ADS-B, frequent | Mostly broadband; occasional fan tones up to ~1 kHz (detection default f0 max = 1500 Hz) |
| Light aircraft | Perfect tones | In Europe often **without** ADS-B position (mandatory above 5.7 t / 250 kt only) |

## Where and when

- **Under an approach or departure path** of a busy airport, 5–20 km from the runways: low passes
  (300–1500 m), frequent, clean curves. The runway configuration depends on the wind — check a live
  traffic map before going out.
- Near general-aviation airfields and heliports for helicopters (check they broadcast positions).
- **Early morning or evening**, on weekdays, away from main roads: less road noise.
- Avoid strong wind (microphone noise, and wind biases the Doppler shift).

## Equipment and settings

- Phone lying still (or on a stand), microphone facing the sky, nothing rubbing against it.
- Record **without lossy compression or noise suppression** if possible (lossless/WAV mode;
  disable "voice" or "noise reduction" presets).
- Duration: **15–30 min** or more, to catch 2+ passes. Two passes constrain far more than one.
- Note your exact position (phone GPS or a point on a map): it is your ground truth.
- Microphone height is **above ground**: 1.5 m handheld, ~3 m per storey + 1.5 m on a balcony.

## Clock (critical)

The search tolerates ±3 s by default; the more accurate the start time, the better.

1. Open a reference clock (e.g. time.is) on another screen.
2. Start recording.
3. On a round second (e.g. 10:15:00) say "mark" out loud.
4. Start time = time of the "mark" − position of the "mark" in the file.

Many phone recorders write the file's `creation_time` metadata at the **end** of the recording, in
UTC, to within ~2 s. The app pre-fills the start time from it and lets you flip the hypothesis.

## Field notes

- Start the ADS-B capture **before** recording: audio without ADS-B coverage is unusable.
- In a dense city centre away from approach paths, most audible traffic is **jets at 3–5 km altitude,
  5–7 km away**: broadband noise, rarely tones. Clean tones come from a few aircraft passing close.
- A single pass constrains the position to a strip roughly 2 km wide; you need several passes.

## Workflow

1. `make up`, open http://localhost:8501, create a session. In the sidebar, set the ground elevation
   (or upload a DEM tile), the geoid undulation, QNH and temperature for your area.
2. **ADS-B** tab: centre = your approximate area, radius 15 NM, **Start capture**.
3. Record 15–30 min (with the "mark").
4. Let the capture run 2–3 more minutes, then **Stop**.
5. **Audio** tab: import the audio, check the start time (time zone!), look at the spectrogram
   (50–1500 Hz: look for descending "S" curves).
6. **Detection** tab: detection. If nothing: lower the threshold or widen the f0 range.
7. **Overlay** tab (optional): overlay at your real position. **This is the phase-1 test**: the coloured
   curves must land on the cyan tracks.
8. **Search** tab: search, radius 5 km, then 10, then 20. Compare with your real position.

## Supporting data

- **QNH and temperature**: the METAR of the nearest airport at recording time.
- **DEM** (optional, useful with relief): a Copernicus GLO-30 tile covering your area, from the public
  AWS Open Data bucket `copernicus-dem-30m`. Tiles are named after their south-west corner, e.g. for
  the tile covering 39–40°N, 98–99°W:

  ```bash
  aws s3 cp --no-sign-request \
    s3://copernicus-dem-30m/Copernicus_DSM_COG_10_N39_00_W099_00_DEM/Copernicus_DSM_COG_10_N39_00_W099_00_DEM.tif .
  ```

  Then upload it in the sidebar (or pass `--dem file.tif` on the CLI). Without a DEM a constant ground
  elevation is used. See [THIRD_PARTY.md](../THIRD_PARTY.md) for the required attribution.

- **Historical ADS-B** (if the live capture failed): adsb.lol daily dumps (ODbL 1.0),
  `github.com/adsblol/globe_history_<year>`, one release per day and per instance (`…-planes-readsb-prod-0`).

  A full day weighs several GB. There is no need to extract it: `--tar` reads the parts as one stream
  and keeps only the positions inside the area and time window.

  ```bash
  gh release download vYYYY.MM.DD-planes-readsb-prod-0 -R adsblol/globe_history_YYYY
  python -m dgeo adsb-hist --tar vYYYY.MM.DD-planes-readsb-prod-0.tar.a* \
      --start YYYY-MM-DDT09:00:00Z --end YYYY-MM-DDT10:00:00Z --lat <lat> --lon <lon> --radius-km 30 \
      --out flights.csv
  ```

  To avoid storing the archive at all, pipe the download into it (`--tar -` reads stdin):

  ```bash
  TAG=vYYYY.MM.DD-planes-readsb-prod-0; R=adsblol/globe_history_YYYY
  for a in $(gh release view $TAG -R $R --json assets -q '.assets[].name' | grep '\.tar\.a' | sort); do
    gh release download $TAG -R $R -p "$a" -O -
  done | python -m dgeo adsb-hist --tar - --start … --end … --lat <lat> --lon <lon> --out flights.csv
  ```

  An already extracted directory can still be read with `--traces-dir`. Check the exact asset names of the
  release; they have changed over the years.

## Quick diagnosis

| Symptom | Likely cause | Action |
|---|---|---|
| No track detected | Weak signal, phone processing, mostly broadband jets | Lower threshold, longer recording, lossless audio; check the **Overlay** tab |
| Overlay consistently shifted in time | Wrong start time | Increase the clock uncertainty, use the "mark" |
| Predicted curves too steep / too flat | Wrong altitude or distance | Check QNH, `--alt-source`, ground elevation / DEM, microphone height |
| Right shape, wrong frequency | Normal: f0 is free | — |
| Audible aircraft missing from ADS-B | No ADS-B position broadcast | Ignore that pass (drop the track) |
