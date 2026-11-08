# SPDX-License-Identifier: AGPL-3.0-or-later
"""UI: a click on the capture-zone map moves the centre (map component mocked, no browser)."""
import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("streamlit_folium")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP = Path(__file__).resolve().parents[1] / "app" / "app.py"


def test_map_click_sets_centre(tmp_path, monkeypatch):
    s = tmp_path / "sessions" / "t1"
    s.mkdir(parents=True)
    (s / "meta.json").write_text(json.dumps({"tz": "UTC"}))
    monkeypatch.setenv("DGEO_DATA", str(tmp_path))
    # Streamlit's script runner replaces sys.modules["__main__"]: restore it after the test, or later
    # spawned worker processes (search --workers) would re-run the app script
    monkeypatch.setitem(sys.modules, "__main__", sys.modules["__main__"])
    script = f"""
import runpy, streamlit_folium
streamlit_folium.st_folium = lambda *a, **k: {{"last_clicked": {{"lat": 12.3456789, "lng": 365.5}}}}
runpy.run_path({str(APP)!r}, run_name="dgeo_app")
"""
    at = AppTest.from_string(script, default_timeout=60)
    at.run()
    assert not at.exception
    meta = json.loads((s / "meta.json").read_text())
    assert meta["center_lat"] == pytest.approx(12.34568)
    assert meta["center_lon"] == pytest.approx(5.5)  # longitude wrapped to [-180, 180)
    assert [n.value for n in at.number_input][:2] == pytest.approx([12.34568, 5.5])
