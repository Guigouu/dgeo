# SPDX-License-Identifier: AGPL-3.0-or-later
"""Address -> latitude/longitude, to fill in the search / capture centre.

- "40.4168, -3.7038" (or "40.4168 -3.7038", "40,4168; -3,7038") is parsed locally: nothing is sent.
- Anything else is looked up with OpenStreetMap Nominatim (https://nominatim.org, data © OpenStreetMap
  contributors, ODbL). The query leaves your machine: prefer a nearby public place or a district over your
  exact home address. Usage policy: one request at a time, identified User-Agent, no bulk geocoding.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import __version__

NOMINATIM = "https://nominatim.openstreetmap.org/search"
USER_AGENT = f"dgeo/{__version__} (+https://github.com/Guigouu/dgeo)"

_NUM = r"[-+]?\d{1,3}(?:[.,]\d+)?"
_COORDS = re.compile(rf"^\s*({_NUM})\s*(?:[;,]\s*|\s+)({_NUM})\s*$")


@dataclass
class Place:
    name: str
    lat: float
    lon: float


def parse_coords(text: str) -> tuple[float, float] | None:
    """'lat, lon' typed or pasted (Google Maps, OSM...). Decimal comma accepted when separated by ';' or space."""
    m = _COORDS.match(text)
    if not m:
        return None
    lat, lon = (float(g.replace(",", ".")) for g in m.groups())
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return lat, lon


def geocode(text: str, limit: int = 5, timeout: float = 10.0, session=None) -> list[Place]:
    """Coordinates typed directly, or up to ``limit`` Nominatim matches for an address / place name."""
    text = text.strip()
    if not text:
        return []
    c = parse_coords(text)
    if c:
        return [Place(f"{c[0]:.5f}, {c[1]:.5f}", *c)]
    import requests

    s = session or requests
    r = s.get(NOMINATIM, params={"q": text, "format": "jsonv2", "limit": limit},
              headers={"User-Agent": USER_AGENT, "Accept-Language": "en"}, timeout=timeout)
    r.raise_for_status()
    return [Place(p.get("display_name", text), float(p["lat"]), float(p["lon"])) for p in r.json()]
