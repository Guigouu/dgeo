# Robustness (phase 3)

How much of the pipeline survives the degradations a real recording goes through? This was measured with
`scripts/robustness.py` on synthetic scenarios: two propeller passes at ~0.8–0.9 km, a 3 km decoy, jets
and light aircraft in the ADS-B data, 300 s of audio, a ±5 km search and a start time known to ±3 s.
Each degradation is applied with ffmpeg (or numpy for the speech masker) to the same recordings. Detection
and search then run with default settings.

| degradation | description | tracks found | median error | runs < 150 m | median area Δ≤0.3 |
|---|---|---|---|---|---|
| `clean` | reference, no degradation | 2/2/2 | 24 m | 3/3 | 0.09 km² |
| `aac_64k` | AAC 64 kbit/s (typical phone recorder) | 2/2/2 | 32 m | 3/3 | 0.09 km² |
| `mp3_32k` | MP3 32 kbit/s (heavily compressed upload) | 2/1/2 | 21 m | 2/3 | 0.27 km² |
| `phone_call` | phone call: 300–3400 Hz band + Opus 12 kbit/s VoIP | 2/1/2 | 92 m | 2/3 | 0.18 km² |
| `highpass_300` | high-pass filter at 300 Hz ("remove the hum") | 2/2/2 | 15 m | 3/3 | 0.09 km² |
| `noise_suppression` | FFT noise suppression (like a phone's denoiser) | 2/1/2 | 32 m | 2/3 | 0.27 km² |
| `indoor` | indoors: walls (low-pass 800 Hz, −15 dB) + room reverberation | 2/2/2 | 58 m | 3/3 | 0.09 km² |
| `speech_masking` | speech-like masker 10 dB louder than the recording | 2/2/2 | 11 m | 3/3 | 0.09 km² |

Seeds 1, 2 and 3. "Tracks found" is the number of tonal tracks per seed (2 expected). The area column is
the median candidate area within cost + 0.3 of the minimum.

## Findings

- **The signal survives most degradations.** None of these removed both tracks or caused a large error
  while both were still found:
  - a 300 Hz high-pass filter;
  - a speech-like masker 10 dB louder than the recording;
  - indoor attenuation with reverberation;
  - a phone call (300–3400 Hz band, Opus 12 kbit/s VoIP).

  The filters remove the propeller fundamentals, but the harmonics remain, and the ratio f/f0 is the same
  for every harmonic. Filtering a recording or talking over it does not protect it.
- **Every failure is a lost track.** When one of the two passes disappears (MP3 32 kbit/s, phone call and
  noise suppression on seed 2), the remaining pass only constrains the position to a few km. The errors
  were 1–6 km. The method needs at least two well-separated passes. Lossy codecs and denoisers hurt mostly
  by erasing the weaker one.
- **What actually protects a recording:** cutting it into pieces, so that passes share no clock, or
  denoising strongly enough to erase the tones before upload.

## Reproduce

```bash
python scripts/robustness.py --seeds 1 2 3 --out robustness.md          # ~6 min, needs ffmpeg with libopus
python scripts/robustness.py --broadband-jets --out robustness_jets.md  # also uses loudness events
```

Synthetic scenarios are optimistic: the noise is stationary, the propeller harmonics are ideal, and the
ADS-B positions are exact up to 5 m of noise. Real recordings are harder (see the field notes in
[FIELD_GUIDE.md](FIELD_GUIDE.md)).
