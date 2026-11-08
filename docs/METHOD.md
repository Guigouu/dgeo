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

### Wind (optional)

A uniform horizontal wind `w` (from the METAR: speed, direction it blows FROM) changes the sound speed
along each ray: `c_eff = c + w·n`, with `n` the unit vector from the source to the observer. It enters
both the retarded time (`t_obs = t_e + R / c_eff`) and the Doppler ratio, `f_obs = f0·c_eff / (c_eff + Ṙ)`
(in the air frame the observer moves at `−w` and the source at `v − w`). On synthetic scenarios with a
12 m/s wind, modelling it reduced the error from 26 to 15 m and from 42 to 33 m: a second-order effect for
passes within ~1 km, larger for distant ones. Wind aloft differs from the surface wind; a single value is used.

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

## 6b. Loudness events (experimental)

Many passes, especially jets a few km away, produce no usable tone, only a broad hump of energy.
`dgeo/events.py` detects them as peaks of the 150–1500 Hz level above the per-frequency background
(6 s smoothing, prominence ≥ 4 dB, width 10–240 s), excluding the spans already covered by tonal tracks.

In the search, each event is matched to the flight whose predicted heard CPA is closest, with:

- a **lag** `loudness peak − heard CPA` shared by all events and fitted per grid point
  (−25…+10 s): directivity and absorption shift the peak, in field data it led the CPA by 10–20 s;
- a timing tolerance `event_sigma_s` = 6 s and a capped squared residual (unmatched event = cap);
- a **category filter**: only large aircraft (ADS-B emitter categories A2–A6) can explain an event up to
  9 km; light aircraft, rotorcraft and unknown categories only up to 2.5 km. Without it, distant light
  aircraft were matched by chance and pulled the solution away;
- a low weight (`event_weight` = 0.25): events alone are not reliable (dense traffic gives chance matches),
  they only refine a solution anchored by tonal tracks.

Observed effect: on a synthetic scenario with one tonal track and four broadband jets, the error drops from
125 m to 48 m; on one 90-minute urban field recording with a single tonal pass, the candidate area
(cost ≤ min + 0.1) shrank from 8.2 to 2.0 km² with the true position still inside, while higher weights
excluded it. One recording is not an evaluation: the term is **off by default**.

## Candidate area

Besides the best point, the search reports the area of the coarse-grid points whose cost is within 0.1 and
0.3 of the minimum, and how far they extend from the best point. A large area means a plateau: the
recording does not pin the position down, whatever the best point says. This is a descriptive measure,
not a calibrated confidence region.

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

- **Wind** is modelled as a single uniform vector (surface METAR); a vertical profile would be better for high passes.
- **Jets** are mostly broadband. Their loudness peaks are still informative (in field tests they
  preceded the heard CPA by a fairly constant 10–20 s); a timing-based term could use them.
- **Unknown recording time**: a large `dt_max` (60–600 s) with a coarse-then-fine step, pre-filtered
  by the fitted (v, d).
- Score the N best candidates in energy mode too (faint tracks that were not detected).
- Tile-level parallelism for large areas.
