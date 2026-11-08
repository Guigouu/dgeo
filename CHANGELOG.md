# Changelog

## 0.1.0 — initial public release

- Forward model with retarded time (Newton), ENU frame, altitude handling (GNSS / baro + QNH / geoid), DEM support.
- Spectrogram whitening, log-frequency harmonic sum, automatic Doppler track detection and straight-line fit.
- Grid search (coarse → fine) with a shared clock offset and a silence filter; tonal-contrast overlay at a known position.
- Live ADS-B capture (adsb.lol, airplanes.live) with adaptive rate limiting; reader for adsb.lol `globe_history` dumps.
- Start time pre-filled from audio file metadata (start/end hypotheses).
- Streamlit UI (5 tabs, map with cost classes and toggleable layers), CLI, Docker image.
- Synthetic benchmark and tests; CI on Python 3.10 and 3.12 plus a Docker build.
- Date/time parsing that behaves identically on Python 3.10+ (fractional seconds, `Z` suffix).
