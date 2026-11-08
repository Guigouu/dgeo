# Method

## 1. Reference frame and heights

Everything is computed in a local **ENU** (East-North-Up) frame centred on the search area, from
WGS84 coordinates converted to ECEF. All internal heights are **ellipsoidal (HAE)**.

| Source | Reference | Conversion to HAE |
|---|---|---|
| ADS-B `alt_geom` | GNSS, WGS84 ellipsoid (readsb) | none |
| ADS-B `alt_baro` | standard pressure 1013.25 hPa | `+ (QNH − 1013.25) × 8.5 m/hPa + N` |
| Copernicus GLO-30 DEM | EGM2008 geoid | `+ N` |

`N` is the geoid undulation (−107 m to +86 m worldwide; default 0 — set `--geoid-n` for your area with an
EGM2008 geoid calculator).
An error of a few tens of metres in altitude changes the slant range and therefore the shape of the
curve: prefer `alt_geom`, and check that it agrees with the QNH-corrected `alt_baro` (some
transponders report a GNSS altitude above mean sea level; in that case force `--alt-source baro`
with the right QNH).

## 2. Trajectories

Each flight is resampled at 1 s, split at gaps > 30 s, then smoothed with a Savitzky-Golay filter
(9 s window, order 2). Velocity is the derivative of the same filter. ADS-B positions are noisy
(≈ 5–10 m) and the radial velocity is very sensitive to that noise: without smoothing the predicted
curves are jagged.

## 3. Forward model: retarded time

At `t_obs` the observer hears a sound emitted at `t_e`:

```
t_obs = t_e + |p(t_e) − o| / c
```

solved with Newton's method (`g = t_e + R/c − t_obs`, `g' = 1 + Ṙ/c`), 4 iterations. The observed
frequency is

```
f_obs = f0 · c / (c + Ṙ(t_e))        Ṙ = (p − o)·v / |p − o|
```

Using the radial velocity at `t_obs` instead of `t_e` gives a **wrong** curve, not merely a shifted
one (see `test_naive_model_is_wrong`). `c = 331.3·√(1 + T/273.15)`.

The ratio `f_obs/f0` is the same for every harmonic: we never know which harmonic a track follows,
and we do not need to.

## 4. Spectrogram

- resampling to 8 kHz, 1 s Hann STFT, 0.25 s hop (≈ 1 Hz resolution);
- dB → subtract the per-frequency temporal median (stationary noise: mains hum, ventilation)
  → subtract the per-frame spectral median (gusts, clicks) → divide by a robust standard deviation (MAD).

## 5. Track detection

1. Harmonic sum on a **log-frequency** axis (144 bins per octave), harmonics 1–4 weighted 1/√k,
   fundamentals 40–1500 Hz (propellers sit low; jet fan tones can reach ~1 kHz and above).
2. Local peaks above `thr` σ (default 2.5) in each frame.
3. Greedy frame-to-frame linking with linear extrapolation, 3 % tolerance per frame, gaps ≤ 2 s.
4. Rejection: duration < 15 s, no frequency decrease, straight-line fit RMS > 2.5 %,
   speed outside [15, 250] m/s, or a fit pinned to the minimum distance bound.
5. Fit of the **exact** straight-line model (retarded time solved in closed form):
   `(f0, v, d, tc)` by robust least squares on `ln f`. Gives an indicative speed and CPA distance
   (useful as a pre-filter over very large areas).
6. De-duplication: two simultaneous tracks with a constant frequency ratio (σ < 1 %) are the same
   source at two harmonics.

## 6. Cost of a grid point ("track" mode)

For a point `o`, an observed track `(t_k, f_k)` and a candidate flight:

```
r_k    = predicted ratio at t0 + t_k + dt
ln f0* = mean_k( ln f_k − ln r_k )               (closed-form f0, no sweep)
cost   = RMS_k( ln f_k − ln r_k − ln f0* ) × 100 (≈ % frequency error)
```

- each track takes the flight with the lowest cost (capped at `miss_cost` = 5 % if none fits);
- `dt` (clock error, ±3 s by default) is **shared by all tracks**: the time offset between two passes
  is fixed by the geometry, so it cannot be fitted per track;
- **silence filter**: any flight predicted closer than `audible_m` (2 km) whose CPA falls inside the
  recording with no matching track adds `silence_penalty`;
- point cost = sum over tracks + penalties, at the best `dt`.

Optimisation: the physics is computed once on a regular time grid covering the track ±dt, then
interpolated for each `(dt, t_k)`. Flights whose CPA is beyond `max_range_m` are ignored.

## 7. Search

1. Coarse grid (250 m) over a disc of radius R.
2. The 15 best distinct areas are refined at 25 m within ±500 m.
3. Outputs: `candidates.csv`, `top50.geojson`, `heatmap.png`.

Confidence indicator: ratio between the cost of the best distinct area (> 300 m away) and the best
cost. ≈ 1.0 means a plateau (position poorly constrained); on the synthetic benchmark it is ≈ 2–3.5×.
A single high-altitude pass typically yields a plateau about 2 km wide on either side of its ground
track; two or more passes are needed for a sharp solution.

## 8. "Energy" mode (phase 1, known position)

At a known position, for each flight: predicted curve over all frames, then an f0 sweep by vertical
translation along the log-frequency harmonic-sum axis. The score is a **tonal contrast**: mean z along
the curve minus the mean z along parallel curves at ±6 % in frequency. Broadband noise (jets, wind,
traffic) lights up every frequency and scores ≈ 0; only a tone that actually follows the predicted
curve stands out. The clock offset `dt` is swept globally. This produces the plot in the **Overlay** tab.

## 9. Known limitations and ideas

- **Wind** is not modelled: `c_eff = c + w·û` along the ray (METAR / model profile).
- **Jets** are mostly broadband. Their loudness peaks are still informative (in field tests they
  preceded the heard CPA by a fairly constant 10–20 s); a timing-based term could use them.
- **Unknown recording time**: a large `dt_max` (60–600 s) with a coarse-then-fine step, pre-filtered
  by the fitted (v, d).
- Score the N best candidates in energy mode too (faint tracks that were not detected).
- Tile-level parallelism for large areas.
