# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADS-B acquisition.

Two paths, same output format (CSV, one row per position):

1. ``live``  — poll a public API in ADSBExchange v2 format while recording
   (adsb.lol or airplanes.live, ~1 req/s max). Simplest option for the POC: no large download.
2. ``hist``  — read the daily readsb traces published by adsb.lol
   (github.com/adsblol/globe_history_<year>, ODbL licence) after extraction, or ``hist_tar`` to
   stream them straight from the (split) release archive, without extracting it to disk.

Columns: t (unix s, time of the position), hex, flight, type, category, lat, lon,
alt_baro (ft), alt_geom (ft, HAE WGS84), gs (kt), track (deg), src.
"""
from __future__ import annotations

import csv
import gzip
import io
import json
import math
import os
import sys
import tarfile
import time
from pathlib import Path

import numpy as np
import pandas as pd

COLUMNS = ["t", "hex", "flight", "type", "category", "lat", "lon", "alt_baro", "alt_geom", "gs", "track", "src"]

# ADSBExchange v2 format: {"ac": [...], "now": <ms>}; radius in nautical miles.
# Several URL paths per provider: fall back to the next one on 404.
PROVIDERS = {
    "adsblol": ["https://api.adsb.lol/v2/point/{lat}/{lon}/{r}",
                "https://api.adsb.lol/v2/lat/{lat}/lon/{lon}/dist/{r}"],
    "airplaneslive": ["https://api.airplanes.live/v2/point/{lat}/{lon}/{r}"],
}


def _num(v):
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None  # e.g. alt_baro == "ground"


class _Source:
    """A provider with its own adaptive polling interval that *learns* its rate limit.

    - 429: honour Retry-After, and raise the interval floor ``floor`` to
      1.3x the interval at which the 429 occurred; the interval never drops below it again
      (avoids the 429 -> slow down -> speed up -> 429 oscillation and ~20 s gaps);
    - success: the interval decreases slowly (x0.97) down to the floor;
    - 10 min without a 429: the floor is relaxed by 10 % (the limit may change);
    - repeated 401/403 (3 in a row): the provider is disabled for the session.
    """

    FORBIDDEN_MAX = 3

    def __init__(self, name, urls, interval, max_interval=20.0):
        self.name, self.urls = name, list(urls)
        self.url = self.urls.pop(0)
        self.base = self.floor = self.cur = interval
        self.max_interval = max_interval
        self.next_at = 0.0
        self.backoff = interval
        self.n_ok = self.n_429 = self.n_forbidden = 0
        self.last_429 = 0.0
        self.disabled = False

    def ok(self, t):
        self.n_forbidden = 0
        if self.floor > self.base and t - self.last_429 > 600:
            self.floor = max(self.base, self.floor * 0.9)
            self.last_429 = t  # relax only once per period
        self.cur = max(self.floor, self.cur * 0.97)
        self.backoff = self.cur
        self.next_at = t + self.cur
        self.n_ok += 1

    def throttled(self, t, retry_after):
        self.n_429 += 1
        self.last_429 = t
        self.floor = min(self.max_interval, max(self.floor, self.cur * 1.3))
        self.cur = self.floor
        wait = retry_after if retry_after is not None else self.backoff
        self.backoff = min(self.backoff * 2, 60.0)
        self.next_at = t + max(wait, self.cur)
        return wait

    def forbidden(self, t):
        self.n_forbidden += 1
        if self.n_forbidden >= self.FORBIDDEN_MAX:
            self.disabled = True
            self.next_at = float("inf")
            return None
        return self.failed(t)

    def failed(self, t):
        wait = self.backoff
        self.backoff = min(self.backoff * 2, 60.0)
        self.next_at = t + wait
        return wait


def _retry_after(r) -> float | None:
    v = r.headers.get("Retry-After") if r is not None else None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def live(lat: float, lon: float, radius_nm: float, out: str, provider: str = "both",
         interval: float = 3.0, duration: float | None = None, user_agent: str = "dgeo-poc/0.1"):
    """Poll the API in a loop and append positions to the CSV. Ctrl-C to stop.

    ``provider`` = adsblol | airplaneslive | both. With ``both``, the two networks are polled
    alternately (each at its own interval): effective rate ≈ interval/2 without exceeding
    either provider's limit, and positions are merged (de-duplicated by aircraft + timestamp).
    """
    import requests

    names = list(PROVIDERS) if provider == "both" else [provider]
    fmt = dict(lat=f"{lat:.5f}", lon=f"{lon:.5f}", r=int(round(radius_nm)))
    sources = [_Source(n, [u.format(**fmt) for u in PROVIDERS[n]], interval) for n in names]
    for k, s in enumerate(sources):  # stagger the first requests
        s.next_at = time.time() + k * interval / len(sources)
    tty = sys.stderr.isatty()
    new = not os.path.exists(out)
    seen: set[tuple[str, float]] = set()
    t_end = time.time() + duration if duration else None
    sess = requests.Session()
    sess.headers["User-Agent"] = user_agent
    n_rows, last_log, n_ac = 0, 0.0, 0

    def log(msg, progress=False):
        if progress and tty:
            print(f"\r{msg}", end="", file=sys.stderr, flush=True)
        else:
            print(("\n" if tty else "") + msg if not progress else msg, file=sys.stderr, flush=True)

    with open(out, "a", newline="") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(COLUMNS)
        log(f"[live] {', '.join(s.name for s in sources)} every ~{interval}s each -> {out} (Ctrl-C to stop)")
        try:
            while t_end is None or time.time() < t_end:
                active = [s for s in sources if not s.disabled]
                if not active:
                    log("[live] no usable provider left, stopping")
                    break
                src = min(active, key=lambda s: s.next_at)
                time.sleep(max(0.0, src.next_at - time.time()))
                t_req = time.time()
                r = None
                try:
                    r = sess.get(src.url, timeout=10)
                    if r.status_code == 404 and src.urls:
                        src.url = src.urls.pop(0)
                        log(f"[live] {src.name}: 404, trying {src.url}")
                        continue
                    if r.status_code == 429:
                        wait = src.throttled(time.time(), _retry_after(r))
                        log(f"[live] {src.name}: 429, pausing {wait:.0f}s, interval floor -> {src.floor:.1f}s")
                        continue
                    if r.status_code in (401, 403):
                        wait = src.forbidden(time.time())
                        if wait is None:
                            log(f"[live] {src.name}: repeated {r.status_code}, provider disabled for this session "
                                f"(access denied for this IP or client)")
                        else:
                            log(f"[live] {src.name}: {r.status_code}, retrying in {wait:.0f}s")
                        continue
                    r.raise_for_status()
                    js = r.json()
                    src.ok(time.time())
                except Exception as e:  # network, 5xx…
                    wait = src.failed(time.time())
                    log(f"[live] {src.name}: error {e!r}, retrying in {wait:.0f}s")
                    continue
                now = (js.get("now") or t_req * 1000) / 1000.0
                for ac in js.get("ac") or []:
                    if ac.get("lat") is None or ac.get("lon") is None:
                        continue
                    t_pos = round(now - float(ac.get("seen_pos") or 0.0), 1)
                    key = (ac.get("hex", ""), t_pos)
                    if key in seen:
                        continue  # position not updated since the previous request
                    seen.add(key)
                    w.writerow([t_pos, ac.get("hex"), (ac.get("flight") or "").strip(), ac.get("t"),
                                ac.get("category"), ac["lat"], ac["lon"], _num(ac.get("alt_baro")),
                                _num(ac.get("alt_geom")), _num(ac.get("gs")), _num(ac.get("track")),
                                ac.get("type")])
                    n_rows += 1
                fh.flush()
                n_ac = len(js.get("ac") or [])
                if tty or time.time() - last_log > 30:  # avoid spamming the UI log
                    rates = "  ".join(f"{s.name} " + ("off" if s.disabled else f"{s.cur:.1f}s") for s in sources)
                    log(f"[live] {time.strftime('%H:%M:%S')}  {n_ac} aircraft, {n_rows} positions  ({rates})",
                        progress=True)
                    last_log = time.time()
        except KeyboardInterrupt:
            pass
    stats = ", ".join(f"{s.name}: {s.n_ok} ok / {s.n_429} x 429" + (" (disabled)" if s.disabled else "")
                      for s in sources)
    log(f"[live] done: {n_rows} positions ({stats})")


def _load_trace(raw: bytes):
    """A readsb ``trace_full_*.json`` file, gzip-compressed or not."""
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return json.loads(raw)


class _Window:
    """Area + time filter applied to readsb traces."""

    def __init__(self, t_start, t_end, lat, lon, radius_km):
        self.t_start, self.t_end, self.lat, self.lon = t_start, t_end, lat, lon
        self.dlat = radius_km / 111.0
        self.dlon = radius_km / (111.0 * math.cos(math.radians(lat)))

    def rows(self, js: dict, default_hex: str) -> list:
        """Format of a readsb trace point:
          [dt, lat, lon, alt_baro|"ground"|null, gs, track, flags, vrate, details|null, src_type,
           alt_geom|null, geom_rate, ias, roll]
        """
        base = float(js.get("timestamp", 0))
        tr = js.get("trace") or []
        if not tr or base + tr[-1][0] < self.t_start or base + tr[0][0] > self.t_end:
            return []
        hx = js.get("icao", default_hex)
        typ = js.get("t")
        flight, cat, out = None, None, []
        for pt in tr:
            t = base + pt[0]
            det = pt[8] if len(pt) > 8 and isinstance(pt[8], dict) else None
            if det:  # callsign / category are only sent now and then: keep the latest seen
                flight = (det.get("flight") or "").strip() or flight
                cat = det.get("category") or cat
            if t < self.t_start or t > self.t_end:
                continue
            la, lo = pt[1], pt[2]
            if la is None or lo is None or abs(la - self.lat) > self.dlat or abs(lo - self.lon) > self.dlon:
                continue
            out.append([t, hx, flight, typ, cat, la, lo, _num(pt[3]),
                        _num(pt[10]) if len(pt) > 10 else None, _num(pt[4]), _num(pt[5]),
                        pt[9] if len(pt) > 9 else None])
        return out


def hist(traces_dir: str, t_start: float, t_end: float, lat: float, lon: float, radius_km: float) -> pd.DataFrame:
    """Read extracted ``trace_full_<hex>.json`` files (readsb) and filter by area + time window."""
    win = _Window(t_start, t_end, lat, lon, radius_km)
    rows = []
    files = list(Path(traces_dir).rglob("trace_full_*.json"))
    if not files:
        raise FileNotFoundError(f"no trace_full_*.json under {traces_dir}")
    for i, p in enumerate(files):
        if i % 5000 == 0:
            print(f"\r[hist] {i}/{len(files)} files", end="", file=sys.stderr)
        try:
            js = _load_trace(p.read_bytes())
        except Exception:
            continue
        rows += win.rows(js, p.stem.replace("trace_full_", ""))
    print(f"\r[hist] {len(files)} files read, {len(rows)} positions kept", file=sys.stderr)
    return pd.DataFrame(rows, columns=COLUMNS)


class _Concat(io.RawIOBase):
    """Read several files (the ``.tar.aa``, ``.tar.ab``, … parts of a release) as one stream."""

    def __init__(self, paths):
        self.paths, self.fh = list(paths), None

    def readable(self):
        return True

    def readinto(self, b):
        while True:
            if self.fh is None:
                if not self.paths:
                    return 0
                p = self.paths.pop(0)
                self.fh = sys.stdin.buffer if p == "-" else open(p, "rb")
            n = self.fh.readinto(b)
            if n:
                return n
            if self.fh is not sys.stdin.buffer:
                self.fh.close()
            self.fh = None


def hist_tar(parts: list[str], t_start: float, t_end: float, lat: float, lon: float, radius_km: float,
             log=lambda m: print(m, file=sys.stderr)) -> pd.DataFrame:
    """Stream a globe_history release archive (its split parts in order, or "-" for stdin) and keep only the
    positions inside the area and time window. Nothing is extracted to disk, so a day of worldwide traces
    (several GB) can be filtered on a small machine, even while it downloads."""
    win = _Window(t_start, t_end, lat, lon, radius_km)
    rows, n = [], 0
    stream = io.BufferedReader(_Concat(sorted(parts) if "-" not in parts else parts), buffer_size=1 << 20)
    with tarfile.open(fileobj=stream, mode="r|*") as tar:
        for m in tar:
            name = m.name.rsplit("/", 1)[-1]
            if not (m.isfile() and name.startswith("trace_full_")):
                continue
            n += 1
            if n % 20000 == 0:
                log(f"[hist] {n} traces read, {len(rows)} positions kept")
            try:
                js = _load_trace(tar.extractfile(m).read())
            except Exception:
                continue
            rows += win.rows(js, name.split(".")[0].replace("trace_full_", ""))
    log(f"[hist] {n} traces read, {len(rows)} positions kept")
    return pd.DataFrame(rows, columns=COLUMNS)


def read_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, dtype={"hex": str, "flight": str, "type": str, "category": str, "src": str})
    for c in ("t", "lat", "lon", "alt_baro", "alt_geom", "gs", "track"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    # On-ground positions ("ground") have NaN alt_baro and no alt_geom: drop them.
    return df[np.isfinite(df["alt_baro"]) | np.isfinite(df["alt_geom"])]
