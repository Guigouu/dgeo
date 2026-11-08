# SPDX-License-Identifier: AGPL-3.0-or-later
"""Historical ADS-B (readsb globe_history traces): directory reader and streaming archive reader agree."""
import gzip
import io
import json
import tarfile

from dgeo.adsb import hist, hist_tar

T0 = 1_750_000_000.0


def trace(icao, lat0, lon0, gz):
    pts = []
    for k in range(0, 600, 5):  # 10 min, northbound, details (callsign, category) only on the first point
        det = {"flight": "TEST123 ", "category": "A3"} if k == 0 else None
        pts.append([k, lat0 + k * 1e-4, lon0, 30000, 420.0, 0.0, 0, 0, det, "adsb_icao", 30500, 0, 280, 0])
    raw = json.dumps({"icao": icao, "timestamp": T0, "t": "A320", "trace": pts}).encode()
    return gzip.compress(raw) if gz else raw


def build(tmp_path):
    files = {"traces/aa/trace_full_aaaaaa.json": trace("aaaaaa", 40.0, -3.0, gz=True),       # inside
             "traces/bb/trace_full_bbbbbb.json": trace("bbbbbb", 45.0, 2.0, gz=False),       # far away
             "traces/cc/trace_full_cccccc.json": trace("cccccc", 40.01, -3.01, gz=False)}    # inside
    for name, raw in files.items():
        p = tmp_path / "dir" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(raw)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, raw in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(raw)
            tar.addfile(info, io.BytesIO(raw))
    data = buf.getvalue()
    parts = []
    for i, k in enumerate(range(0, len(data), 3000)):  # split like the release assets (.tar.aa, .tar.ab, …)
        p = tmp_path / f"day.tar.a{chr(97 + i)}"
        p.write_bytes(data[k:k + 3000])
        parts.append(str(p))
    return parts


def test_directory_and_stream_agree(tmp_path):
    parts = build(tmp_path)
    assert len(parts) > 1
    args = (T0 + 60, T0 + 300, 40.0, -3.0, 20.0)
    a = hist(str(tmp_path / "dir"), *args)
    b = hist_tar(parts[::-1], *args, log=lambda *_: None)  # part order is restored by name
    assert set(a.hex) == {"aaaaaa", "cccccc"}
    assert len(a) == len(b) > 0
    assert a.sort_values(["hex", "t"]).reset_index(drop=True).equals(b.sort_values(["hex", "t"]).reset_index(drop=True))
    assert (a.t >= T0 + 60).all() and (a.t <= T0 + 300).all()
    # callsign and category sent before the window are carried over
    assert set(a.flight) == {"TEST123"} and set(a.category) == {"A3"}
