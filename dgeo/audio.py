# SPDX-License-Identifier: AGPL-3.0-or-later
"""Audio -> whitened spectrogram (z-scores).

Whitening removes:
  1. stationary noise per frequency bin (median over time: 50 Hz mains hum, ventilation, fridge…)
  2. broadband transients per frame (median over frequency: gusts, clicks)
then normalises by a robust standard deviation (MAD). A Doppler curve, weak but tonal,
then stands out as a positive ridge.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import gcd

import numpy as np
from scipy.io import wavfile
from scipy.signal import resample_poly, stft


@dataclass
class Spectro:
    t: np.ndarray      # (T,) seconds since start of recording (frame centres)
    f: np.ndarray      # (F,) Hz
    z: np.ndarray      # (F,T) whitened z-scores
    db: np.ndarray     # (F,T) raw level in dB (for display)

    def harmonic_log(self, fmin: float, fmax: float, n_harm: int = 4, bins_per_octave: int = 144):
        """Spectrogram on a log-frequency axis with a weighted harmonic sum.

        Returns (g, H) with g (G,) candidate fundamental frequencies and H (G,T).
        On a log axis, multiplying f0 is a vertical translation: this is what makes
        sweeps over f0 cheap.
        """
        n = int(np.ceil(np.log2(fmax / fmin) * bins_per_octave)) + 1
        g = fmin * 2.0 ** (np.arange(n) / bins_per_octave)
        H = np.zeros((n, self.z.shape[1]))
        wsum = 0.0
        for k in range(1, n_harm + 1):
            fk = g * k
            m = fk < self.f[-1]
            if not m.any():
                break
            w = 1.0 / np.sqrt(k)
            idx = np.interp(fk[m], self.f, np.arange(len(self.f)))
            i0 = np.floor(idx).astype(int)
            a = idx - i0
            i1 = np.minimum(i0 + 1, len(self.f) - 1)
            H[m] += w * ((1 - a)[:, None] * self.z[i0] + a[:, None] * self.z[i1])
            wsum += w
        return g, H / max(wsum, 1e-9)


def load_wav(path: str, target_fs: int = 8000):
    fs, raw = wavfile.read(path)
    x = raw.astype(np.float32)  # float32: keeps a 1 h recording lightweight
    if np.issubdtype(raw.dtype, np.integer):
        x /= float(np.iinfo(raw.dtype).max)
    if x.ndim > 1:
        x = x.mean(axis=1)
    if target_fs and fs != target_fs:
        g = gcd(int(fs), int(target_fs))
        x = resample_poly(x, target_fs // g, fs // g)
        fs = target_fs
    return x, fs


def spectrogram(x: np.ndarray, fs: int, win_s: float = 1.0, hop_s: float = 0.25,
                fmax: float = 2000.0) -> Spectro:
    nper = int(win_s * fs)
    hop = int(hop_s * fs)
    f, t, Z = stft(x, fs=fs, window="hann", nperseg=nper, noverlap=nper - hop,
                   boundary=None, padded=False)
    keep = (f > 0) & (f <= fmax)
    f, S = f[keep], np.abs(Z[keep]).astype(np.float32)
    db = 20 * np.log10(S + np.float32(1e-12))
    w = db - np.median(db, axis=1, keepdims=True)
    w = w - np.median(w, axis=0, keepdims=True)
    mad = np.median(np.abs(w - np.median(w))) * 1.4826 + 1e-9
    return Spectro(t=t, f=f, z=(w / mad).astype(np.float32), db=db)
