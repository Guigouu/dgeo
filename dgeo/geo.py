# SPDX-License-Identifier: AGPL-3.0-or-later
"""Geodesy: WGS84 <-> ECEF <-> local ENU, DEM, altitude conversions.

Convention: all internal heights are ELLIPSOIDAL heights (HAE, WGS84), in metres.
The data sources do not all use the same reference:

- ADS-B ``alt_geom`` (readsb)    : GNSS height, referenced to the WGS84 ellipsoid (feet).
- ADS-B ``alt_baro``             : pressure altitude (1013.25 hPa), to be corrected with QNH;
                                   approximates an orthometric altitude (above the geoid).
- Copernicus GLO-30 DEM          : orthometric altitude (EGM2008 geoid).

HAE = orthometric altitude + N (geoid undulation, from −107 m to +86 m worldwide; look it up for your
area with an EGM2008 geoid calculator).
"""
from __future__ import annotations

import numpy as np

A = 6378137.0
F = 1 / 298.257223563
E2 = F * (2 - F)
FT = 0.3048
M_PER_HPA = 8.5  # ~27-30 ft/hPa near the ground; good enough for a POC
DEFAULT_GEOID_N = 0.0  # set the real value for your area (--geoid-n / sidebar)


def geodetic_to_ecef(lat, lon, h):
    lat = np.radians(np.asarray(lat, dtype=float))
    lon = np.radians(np.asarray(lon, dtype=float))
    h = np.asarray(h, dtype=float)
    s = np.sin(lat)
    n = A / np.sqrt(1 - E2 * s * s)
    x = (n + h) * np.cos(lat) * np.cos(lon)
    y = (n + h) * np.cos(lat) * np.sin(lon)
    z = (n * (1 - E2) + h) * s
    return x, y, z


def ecef_to_geodetic(x, y, z):
    x, y, z = (np.asarray(v, dtype=float) for v in (x, y, z))
    lon = np.arctan2(y, x)
    p = np.hypot(x, y)
    lat = np.arctan2(z, p * (1 - E2))
    h = np.zeros_like(p)
    for _ in range(6):
        s = np.sin(lat)
        n = A / np.sqrt(1 - E2 * s * s)
        h = p / np.cos(lat) - n
        lat = np.arctan2(z, p * (1 - E2 * n / (n + h)))
    return np.degrees(lat), np.degrees(lon), h


class LocalFrame:
    """Local East-North-Up tangent frame centred on (lat0, lon0, h0)."""

    def __init__(self, lat0: float, lon0: float, h0: float = 0.0):
        self.lat0, self.lon0, self.h0 = lat0, lon0, h0
        self.o = np.array(geodetic_to_ecef(lat0, lon0, h0))
        la, lo = np.radians(lat0), np.radians(lon0)
        self.R = np.array([
            [-np.sin(lo), np.cos(lo), 0.0],
            [-np.sin(la) * np.cos(lo), -np.sin(la) * np.sin(lo), np.cos(la)],
            [np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)],
        ])

    def to_enu(self, lat, lon, h) -> np.ndarray:
        """-> array (..., 3) in metres."""
        x, y, z = geodetic_to_ecef(lat, lon, h)
        d = np.stack([x - self.o[0], y - self.o[1], z - self.o[2]], axis=-1)
        return d @ self.R.T

    def to_geodetic(self, e, n, u):
        enu = np.stack([np.asarray(e, float), np.asarray(n, float), np.asarray(u, float)], axis=-1)
        d = enu @ self.R
        return ecef_to_geodetic(d[..., 0] + self.o[0], d[..., 1] + self.o[1], d[..., 2] + self.o[2])


class Dem:
    """Ground elevation as HAE.

    - ``paths``: one or more GeoTIFF tiles (e.g. Copernicus GLO-30, in EPSG:4326),
      read with rasterio if available;
    - otherwise ``constant_m``: constant orthometric elevation (m above mean sea level).
    The geoid undulation ``geoid_n`` is added to obtain an HAE.
    """

    def __init__(self, paths=None, constant_m: float | None = None, geoid_n: float = DEFAULT_GEOID_N):
        self.geoid_n = geoid_n
        self.constant = constant_m
        self.tiles = []
        for p in paths or []:
            import rasterio  # optional

            with rasterio.open(p) as ds:
                if ds.crs is not None and ds.crs.to_epsg() not in (4326, None):
                    raise ValueError(f"{p}: unsupported CRS {ds.crs} (expected EPSG:4326)")
                arr = ds.read(1).astype(np.float32)
                nodata = ds.nodata
                if nodata is not None:
                    arr[arr == nodata] = np.nan
                self.tiles.append((arr, ~ds.transform, ds.bounds))
        if not self.tiles and self.constant is None:
            raise ValueError("Dem: provide GeoTIFF tiles or a constant elevation")

    def ortho(self, lat, lon) -> np.ndarray:
        lat = np.asarray(lat, float)
        lon = np.asarray(lon, float)
        out = np.full(np.broadcast(lat, lon).shape, np.nan)
        for arr, inv, b in self.tiles:
            m = (lon >= b.left) & (lon < b.right) & (lat > b.bottom) & (lat <= b.top) & np.isnan(out)
            if not m.any():
                continue
            col, row = inv * (lon[m], lat[m])
            # bilinear interpolation (pixel centres at +0.5)
            c = np.clip(np.asarray(col) - 0.5, 0, arr.shape[1] - 1.001)
            r = np.clip(np.asarray(row) - 0.5, 0, arr.shape[0] - 1.001)
            c0, r0 = np.floor(c).astype(int), np.floor(r).astype(int)
            fc, fr = c - c0, r - r0
            v = (arr[r0, c0] * (1 - fc) * (1 - fr) + arr[r0, c0 + 1] * fc * (1 - fr)
                 + arr[r0 + 1, c0] * (1 - fc) * fr + arr[r0 + 1, c0 + 1] * fc * fr)
            out[m] = v
        if self.constant is not None:
            out = np.where(np.isnan(out), self.constant, out)
        if np.isnan(out).any():
            raise ValueError("Dem: points outside the provided tiles (add the neighbouring tile or use --ground-elev)")
        return out

    def hae(self, lat, lon) -> np.ndarray:
        return self.ortho(lat, lon) + self.geoid_n


def aircraft_hae(alt_geom_ft, alt_baro_ft, qnh_hpa: float, geoid_n: float, source: str = "auto"):
    """Aircraft ellipsoidal height (m). ``source``: auto | geom | baro."""
    geom = np.asarray(alt_geom_ft, dtype=float) * FT
    baro = np.asarray(alt_baro_ft, dtype=float) * FT + (qnh_hpa - 1013.25) * M_PER_HPA + geoid_n
    if source == "geom":
        return geom
    if source == "baro":
        return baro
    return np.where(np.isfinite(geom), geom, baro)


def speed_of_sound(temp_c: float) -> float:
    return 331.3 * np.sqrt(1 + temp_c / 273.15)
