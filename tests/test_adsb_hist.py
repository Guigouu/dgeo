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


class FakeGitHub:
    """Serves the split release parts like github.com/<repo>/releases/download/<tag>/<tag>.tar.<part>."""

    def __init__(self, parts, repo):
        self.files = {}
        self.calls = []
        for p in parts:
            part = p.rsplit(".", 1)[-1]
            self.files[(repo, part)] = open(p, "rb").read()

    def head(self, url, allow_redirects, timeout, headers):
        repo, part = url.split("/")[4], url.rsplit(".", 1)[-1]

        class R:
            status_code = 302 if (repo, part) in self.files else 404
        return R()

    def get(self, url, stream, timeout, headers):
        self.calls.append(url)
        repo = url.split("/")[4]
        part = url.rsplit(".", 1)[-1]
        data = self.files.get((repo, part))

        class R:
            status_code = 200 if data is not None else 404

            def raise_for_status(self):
                pass

            def iter_content(self, n):
                for k in range(0, len(data), 1000):  # small chunks: exercises the buffering
                    yield data[k:k + 1000]

            def close(self):
                pass
        return R()


def test_download_streams_parts_and_falls_back_to_the_main_repo(tmp_path):
    from dgeo.adsb import hist_download

    parts = build(tmp_path)
    gh = FakeGitHub(parts, repo="globe_history")  # day not in globe_history_<year>: fallback
    df = hist_download(T0 + 60, T0 + 300, 40.0, -3.0, 20.0, session=gh, log=lambda *_: None)
    ref = hist(str(tmp_path / "dir"), T0 + 60, T0 + 300, 40.0, -3.0, 20.0)
    assert len(df) == len(ref) > 0 and set(df.hex) == {"aaaaaa", "cccccc"}
    tag = "v2025.06.15-planes-readsb-prod-0"  # T0 = 2025-06-15 15:06:40 UTC
    assert gh.calls[0].endswith(f"/globe_history_2025/releases/download/{tag}/{tag}.tar.aa")  # 404
    assert gh.calls[1].endswith(f"/globe_history/releases/download/{tag}/{tag}.tar.aa")
    assert gh.calls[-1].endswith(f".tar.a{chr(97 + len(parts))}")  # stops at the first missing part


def test_download_missing_day(tmp_path):
    import pytest

    from dgeo.adsb import hist_download

    from dgeo.adsb import NotPublishedYet

    gh = FakeGitHub([], repo="globe_history")
    with pytest.raises(NotPublishedYet, match="latest available"):
        hist_download(T0, T0 + 60, 40.0, -3.0, 20.0, session=gh, log=lambda *_: None)
    assert gh.calls == []  # checked with HEAD requests, nothing downloaded
