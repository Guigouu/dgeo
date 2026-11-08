# SPDX-License-Identifier: AGPL-3.0-or-later
"""POC web interface: streamlit run app/app.py

A *session* = a folder under $DGEO_DATA/sessions/<name>/ containing:
  flights.csv (ADS-B), rec.wav (audio), meta.json (parameters, t0…), tracks.csv, outputs.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from datetime import date, datetime, time as dtime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, available_timezones

import numpy as np
import pandas as pd
import pydeck as pdk
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dgeo.adsb import read_csv  # noqa: E402
from dgeo.audio import load_wav, spectrogram  # noqa: E402
from dgeo.cli import haversine  # noqa: E402
from dgeo.detect import detect, from_frame, to_frame  # noqa: E402
from dgeo.doppler import build_trajectories  # noqa: E402
from dgeo.geo import Dem, LocalFrame, speed_of_sound  # noqa: E402
from dgeo.plots import heatmap_fig, overlay_fig, spectro_fig  # noqa: E402
from dgeo.search import SearchConfig, observers, overlay_curves, search  # noqa: E402
from dgeo.timeguess import guess_start, parse_creation, probe  # noqa: E402

DATA = Path(os.environ.get("DGEO_DATA", "./data"))
SESS = DATA / "sessions"
DEMS = DATA / "dem"
TIMEZONES = ["UTC"] + sorted(z for z in available_timezones() if "/" in z and not z.startswith(("Etc/", "SystemV/")))
SOURCE_URL = os.environ.get("DGEO_SOURCE_URL", "https://github.com/Guigouu/dgeo")
SESS.mkdir(parents=True, exist_ok=True)
DEMS.mkdir(parents=True, exist_ok=True)

st.set_page_config(page_title="dgeo — Doppler POC", page_icon="✈️", layout="wide")


# ---------------------------------------------------------------- helpers
@st.cache_resource
def procs() -> dict:
    return {}  # session -> subprocess.Popen of the live capture


def meta_path(s: Path) -> Path:
    return s / "meta.json"


def _read_json(p: Path) -> dict | None:
    try:
        v = json.loads(p.read_text())
        return v if isinstance(v, dict) else None
    except (OSError, ValueError):
        return None


def load_meta(s: Path) -> dict:
    """Tolerant read: meta.json, else the last valid version (meta.json.bak), else {}."""
    p = meta_path(s)
    if not p.exists():
        return {}
    m = _read_json(p)
    if m is None:
        m = _read_json(p.with_suffix(".json.bak")) or {}
        _write_meta(s, m)  # repair the damaged file
    return m


def _write_meta(s: Path, m: dict):
    """Atomic write: temporary file + os.replace (never an empty or truncated meta.json,
    even if two Streamlit runs overlap). The previous version is kept as .bak."""
    p = meta_path(s)
    tag = f"{os.getpid()}.{time.time_ns()}"
    old = _read_json(p) if p.exists() else None
    if old:
        bak = p.with_name(f".meta.{tag}.bak.tmp")
        bak.write_text(json.dumps(old, indent=2, default=str))
        os.replace(bak, p.with_suffix(".json.bak"))
    tmp = p.with_name(f".meta.{tag}.tmp")
    tmp.write_text(json.dumps(m, indent=2, default=str))
    os.replace(tmp, p)


def save_meta(s: Path, **kw):
    m = load_meta(s)
    if all(m.get(k) == v for k, v in kw.items()):
        return  # nothing changes: don't write (avoids a write on every rerun)
    m.update(kw)
    _write_meta(s, m)


def capture_running(name: str) -> bool:
    p = procs().get(name)
    return p is not None and p.poll() is None


@st.cache_data(show_spinner=False)
def cached_spectro(path: str, mtime: float, win: float, hop: float):
    x, fs = load_wav(path, 8000)
    return spectrogram(x, fs, win_s=win, hop_s=hop, fmax=3900), len(x) / fs


def env(m: dict, lat: float, lon: float):
    dem_files = [str(p) for p in sorted(DEMS.glob("*.tif"))]
    dem = Dem(dem_files or None, constant_m=m.get("ground_elev", 0.0), geoid_n=m.get("geoid_n", 0.0))
    frame = LocalFrame(lat, lon, float(dem.hae(lat, lon)))
    return frame, dem, speed_of_sound(m.get("temp_c", 15.0))


def trajs_for(s: Path, m: dict, frame):
    df = read_csv(str(s / "flights.csv"))
    return build_trajectories(df, frame, qnh=m.get("qnh", 1013.25), geoid_n=m.get("geoid_n", 0.0),
                              alt_source=m.get("alt_source", "auto"))


def show_fig(fig):
    st.pyplot(fig, clear_figure=True)


# ---------------------------------------------------------------- sidebar
st.sidebar.title("✈️ dgeo")
st.sidebar.caption("Acoustic geolocation from Doppler + ADS-B — POC")
st.sidebar.caption(f"[Source code]({SOURCE_URL}) · AGPL-3.0-or-later")
names = sorted(p.name for p in SESS.iterdir() if p.is_dir())
new = st.sidebar.text_input("New session", placeholder="e.g. backyard-test-1")
if st.sidebar.button("Create", disabled=not new):
    (SESS / new).mkdir(exist_ok=True)
    save_meta(SESS / new, created=datetime.now(timezone.utc).isoformat())
    st.session_state["session"] = new
    st.rerun()
if not names:
    st.info("Create a session in the sidebar (or run the synthetic demo below).")
cur = st.sidebar.selectbox("Session", names, index=names.index(st.session_state["session"])
                           if st.session_state.get("session") in names else 0) if names else None

st.sidebar.divider()
st.sidebar.subheader("Synthetic demo")
if st.sidebar.button("Generate a test scenario"):
    from dgeo.synth import make

    d = SESS / "demo-synth"
    t = make(str(d))
    save_meta(d, t0=t["t0_reported"], center_lat=t["center"][0], center_lon=t["center"][1],
              known_lat=t["lat"], known_lon=t["lon"], ground_elev=t["ground_ortho_m"], geoid_n=t["geoid_n"],
              temp_c=t["temp_c"], truth={"lat": t["lat"], "lon": t["lon"]}, duration=t["duration_s"])
    st.session_state["session"] = "demo-synth"
    st.rerun()

if not cur:
    st.stop()
S = SESS / cur
M = load_meta(S)
# Repair: audio info lost (damaged meta.json) while the imported file is still there
_ups = sorted(S.glob("upload_*"))
if _ups and (S / "rec.wav").exists() and not M.get("audio_name"):
    _dur, _tag = probe(str(_ups[0]))
    _name = _ups[0].name.removeprefix("upload_")
    save_meta(S, audio_name=_name, audio_src=f"{_name}:{_ups[0].stat().st_size}", audio_creation_tag=_tag,
              duration=_dur)
    M = load_meta(S)

with st.sidebar.expander("Physical parameters", expanded=False):
    temp = st.number_input("Air temperature (°C)", value=float(M.get("temp_c", 15.0)), step=1.0)
    qnh = st.number_input("QNH (hPa) — nearest airport METAR", value=float(M.get("qnh", 1013.25)), step=1.0)
    ground = st.number_input("Ground elevation if no DEM (m)", value=float(M.get("ground_elev", 0.0)),
                             help="orthometric elevation of the ground (m above mean sea level)")
    geoid = st.number_input("Geoid undulation N (m)", value=float(M.get("geoid_n", 0.0)),
                            help="EGM2008 geoid undulation at your location (−107 to +86 m worldwide)")
    alt_src = st.selectbox("Aircraft altitude", ["auto", "geom", "baro"],
                           index=["auto", "geom", "baro"].index(M.get("alt_source", "auto")))
    if st.button("Save parameters"):
        save_meta(S, temp_c=temp, qnh=qnh, ground_elev=ground, geoid_n=geoid, alt_source=alt_src)
        st.rerun()
    dem_up = st.file_uploader("DEM GeoTIFF tile (Copernicus GLO-30)", type=["tif", "tiff"])
    if dem_up:
        (DEMS / dem_up.name).write_bytes(dem_up.getbuffer())
        st.success(f"DEM added: {dem_up.name}")
    st.caption("Loaded DEMs: " + (", ".join(p.name for p in DEMS.glob('*.tif')) or "none (constant elevation)"))

st.title(f"Session: {cur}")
tabs = st.tabs(["① ADS-B", "② Audio", "③ Detection", "④ Overlay (known position)", "⑤ Search"])

# ---------------------------------------------------------------- 1. ADS-B
with tabs[0]:
    st.markdown("Start the capture **before** you start recording audio, and stop it a few minutes after.")
    c1, c2, c3, c4 = st.columns(4)
    lat_c = c1.number_input("Centre latitude", value=float(M.get("center_lat", 0.0)), format="%.5f")
    lon_c = c2.number_input("Centre longitude", value=float(M.get("center_lon", 0.0)), format="%.5f")
    rad = c3.number_input("Radius (NM)", value=15, min_value=1, max_value=100)
    prov = c4.selectbox("Provider", ["both", "adsblol", "airplaneslive"],
                        help="both: alternates adsb.lol / airplanes.live, each within its own rate limit")
    running = capture_running(cur)
    b1, b2 = st.columns(2)
    if b1.button("▶ Start capture", disabled=running, type="primary"):
        save_meta(S, center_lat=lat_c, center_lon=lon_c)
        log = open(S / "adsb_live.log", "a")
        procs()[cur] = subprocess.Popen(
            [sys.executable, "-m", "dgeo", "adsb-live", "--lat", str(lat_c), "--lon", str(lon_c),
             "--radius-nm", str(rad), "--provider", prov, "--out", str(S / "flights.csv")],
            stdout=log, stderr=log, cwd=str(Path(__file__).resolve().parents[1]))
        st.rerun()
    if b2.button("■ Stop", disabled=not running):
        procs()[cur].send_signal(signal.SIGINT)
        procs()[cur].wait(timeout=10)
        st.rerun()
    st.write("Capture running ✅" if running else "Capture stopped")
    if st.button("↻ Refresh"):
        st.rerun()
    up = st.file_uploader("…or import a flights.csv (CLI capture / adsb-hist)", type=["csv"])
    if up:
        (S / "flights.csv").write_bytes(up.getbuffer())
        st.success("flights.csv imported")
    if (S / "flights.csv").exists():
        df = read_csv(str(S / "flights.csv"))
        if len(df):
            t_min, t_max = df.t.min(), df.t.max()
            st.caption(f"{len(df)} positions, {df.hex.nunique()} aircraft, "
                       f"{datetime.fromtimestamp(t_min, timezone.utc):%H:%M:%S} → {datetime.fromtimestamp(t_max, timezone.utc):%H:%M:%S} UTC")
            st.map(df.iloc[::max(1, len(df) // 5000)][["lat", "lon"]], size=20)
    if (S / "adsb_live.log").exists():
        with st.expander("Capture log"):
            st.code((S / "adsb_live.log").read_text()[-3000:])

# ---------------------------------------------------------------- 2. Audio
with tabs[1]:
    st.markdown("Import the recording (wav, m4a, mp3, ogg, flac…), then enter its **exact start time**.")
    aud = st.file_uploader("Audio", type=["wav", "m4a", "mp3", "ogg", "flac", "aac", "opus", "webm", "mp4"])
    if aud is not None:
        sig = f"{aud.name}:{aud.size}"
        if M.get("audio_src") != sig:  # new file: convert it only once
            for old in S.glob("upload_*"):
                old.unlink()
            raw = S / f"upload_{aud.name}"
            raw.write_bytes(aud.getbuffer())
            with st.spinner(f"Converting {aud.name}…"):
                r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw), "-ac", "1", "-ar", "16000",
                                    str(S / "rec.wav")], capture_output=True, text=True)
            if r.returncode:
                st.error(r.stderr)
            else:
                dur_raw, tag = probe(str(raw))
                fr = None
                if (S / "flights.csv").exists():
                    fdf = read_csv(str(S / "flights.csv"))
                    fr = (float(fdf.t.min()), float(fdf.t.max())) if len(fdf) else None
                g = guess_start(parse_creation(tag), dur_raw, time.time(), fr)
                # results derived from the previous audio are no longer valid
                for f in ("tracks.csv", "tracks.fit.json", "candidates.csv"):
                    (S / f).unlink(missing_ok=True)
                choice = g["choice"]
                save_meta(S, audio_src=sig, audio_name=aud.name, audio_creation_tag=tag, duration=dur_raw,
                          t0_hyp=g["hypotheses"], t0_hyp_choice=choice, t0_hyp_reason=g["reason"],
                          t0=g["hypotheses"].get(choice) if choice else None,
                          t0_source=f"metadata:{choice}" if choice else None)
                M = load_meta(S)
                st.success(f"{aud.name} converted to rec.wav (mono 16 kHz).")
    if M.get("audio_name") and (S / "rec.wav").exists():
        info = f"Loaded file: **{M['audio_name']}**"
        if M.get("duration"):
            info += f" — duration {int(M['duration'] // 60)} min {M['duration'] % 60:.0f} s"
        if M.get("audio_creation_tag"):
            info += f" — creation_time metadata `{M['audio_creation_tag']}`"
        st.caption(info)
    tz_name = st.selectbox("Time zone of the entered time", TIMEZONES, index=TIMEZONES.index(M.get("tz", "UTC")) if M.get("tz", "UTC") in TIMEZONES else 0)
    save_meta(S, tz=tz_name)

    def guess_from_meta():
        """(Re)compute the start time from the metadata already read, without re-importing the file.
        "now" = when the imported file was saved (more accurate than the current time)."""
        ups = sorted(S.glob("upload_*"))
        now = ups[0].stat().st_mtime if ups else time.time()
        fr = None
        if (S / "flights.csv").exists():
            fdf = read_csv(str(S / "flights.csv"))
            fr = (float(fdf.t.min()), float(fdf.t.max())) if len(fdf) else None
        g = guess_start(parse_creation(M.get("audio_creation_tag")), M.get("duration"), now, fr)
        ch = g["choice"]
        save_meta(S, t0_hyp=g["hypotheses"], t0_hyp_choice=ch, t0_hyp_reason=g["reason"],
                  t0=g["hypotheses"].get(ch) if ch else M.get("t0"),
                  t0_source=f"metadata:{ch}" if ch else M.get("t0_source"))

    # files imported before this feature existed: compute on the fly if no time is set yet
    if M.get("audio_creation_tag") and M.get("duration") and not M.get("t0_hyp") and not M.get("t0"):
        guess_from_meta()
        st.rerun()
    hyp = M.get("t0_hyp") or {}
    if hyp and str(M.get("t0_source", "")).startswith("metadata"):
        labels = {"fin": "the END of the recording", "debut": "the START of the recording"}
        fmt_h = lambda k: (f"{labels[k]} → starts at "
                           f"{datetime.fromtimestamp(hyp[k], ZoneInfo(tz_name)):%H:%M:%S} ({tz_name})")
        keys = list(hyp)
        cur_k = M.get("t0_hyp_choice") if M.get("t0_hyp_choice") in keys else keys[0]
        st.info(f"Start time **pre-filled from the file metadata** "
                f"(reason for the choice: {M.get('t0_hyp_reason')}). Allow ±5 s of clock uncertainty "
                f"in ④ and ⑤; a spoken time mark remains more reliable.")
        k = st.radio("The creation_time metadata corresponds to…", keys, index=keys.index(cur_k),
                     format_func=fmt_h, horizontal=True)
        if k != cur_k:
            save_meta(S, t0_hyp_choice=k, t0=hyp[k], t0_source=f"metadata:{k}", t0_hyp_reason="manual choice")
            st.rerun()
    elif M.get("audio_name") and not M.get("t0"):
        st.warning("No usable time found in the file: enter the start time.")
    if M.get("audio_creation_tag") and not str(M.get("t0_source", "")).startswith("metadata"):
        if st.button("↺ Use the file metadata for the start time"):
            guess_from_meta()
            st.rerun()
    t0_cur = M.get("t0")
    d_def, h_def = date.today(), dtime(12, 0, 0)
    if t0_cur:
        dt0 = datetime.fromtimestamp(float(t0_cur), ZoneInfo(tz_name))
        d_def, h_def = dt0.date(), dt0.time().replace(microsecond=0)
    c1, c2 = st.columns(2)
    d_in = c1.date_input("Start date", d_def)
    h_in = c2.text_input("Start time (HH:MM:SS[.s])", h_def.strftime("%H:%M:%S"))
    if st.button("Save start time"):
        try:
            hh = dtime.fromisoformat(h_in)
            t0 = datetime.combine(d_in, hh, ZoneInfo(tz_name)).timestamp()
            save_meta(S, t0=t0, t0_source="manual")
            st.success(f"t0 = {datetime.fromtimestamp(t0, timezone.utc):%Y-%m-%d %H:%M:%S.%f} UTC")
            M = load_meta(S)
        except ValueError as e:
            st.error(str(e))
    if (S / "rec.wav").exists():
        c1, c2, c3 = st.columns(3)
        fmax_p = c1.slider("Max displayed frequency (Hz)", 100, 3900, 1500, 50)
        win = c2.select_slider("STFT window (s)", [0.5, 1.0, 2.0], 1.0)
        what = c3.radio("Display", ["z", "db"], horizontal=True)
        sp, dur = cached_spectro(str(S / "rec.wav"), (S / "rec.wav").stat().st_mtime, win, 0.25)
        save_meta(S, duration=dur)
        fig, _ = spectro_fig(sp, 0, fmax_p, what=what, title=f"rec.wav — {dur:.0f} s")
        show_fig(fig)
        st.audio(str(S / "rec.wav"))

# ---------------------------------------------------------------- 3. Detection
with tabs[2]:
    if not (S / "rec.wav").exists():
        st.warning("Import an audio file first (tab ②).")
    else:
        c1, c2, c3, c4 = st.columns(4)
        thr = c1.number_input("Threshold (σ)", value=2.5, step=0.25)
        fmin = c2.number_input("f0 min (Hz)", value=40.0)
        fmax = c3.number_input("f0 max (Hz)", value=1500.0, help="propellers: 600 is enough; jets: lines up to ~1500 Hz")
        mind = c4.number_input("Min duration (s)", value=15.0)
        if st.button("Detect tracks", type="primary"):
            sp, _ = cached_spectro(str(S / "rec.wav"), (S / "rec.wav").stat().st_mtime, 1.0, 0.25)
            with st.spinner("Detecting…"):
                tr = detect(sp, speed_of_sound(M.get("temp_c", 15.0)), fmin=fmin, fmax=fmax, thr=thr, min_dur_s=mind)
            to_frame(tr).to_csv(S / "tracks.csv", index=False)
            (S / "tracks.fit.json").write_text(json.dumps([{"piste": t.id, **t.fit} for t in tr], indent=2))
            st.success(f"{len(tr)} track(s)")
        up = st.file_uploader("…or import hand-drawn tracks (CSV: track,t,f)", type=["csv"])
        if up:
            (S / "tracks.csv").write_bytes(up.getbuffer())
        if (S / "tracks.csv").exists():
            tr = from_frame(pd.read_csv(S / "tracks.csv"))
            if (S / "tracks.fit.json").exists():
                fits = pd.DataFrame(json.loads((S / "tracks.fit.json").read_text()))
                if len(fits):
                    st.dataframe(fits.rename(columns={"piste": "track", "v": "v (m/s)", "d": "d_cpa (m)",
                                                      "t_cpa_heard": "CPA heard (s)",
                                                      "rms_pct": "rms (%)"}).round(2), hide_index=True)
            sp, _ = cached_spectro(str(S / "rec.wav"), (S / "rec.wav").stat().st_mtime, 1.0, 0.25)
            show_fig(overlay_fig(sp, [], tr, 0, 1500, title="kept tracks (cyan)"))

# ---------------------------------------------------------------- 4. Overlay
with tabs[3]:
    st.markdown("**Phase 1** — at a position you know, the curves predicted from ADS-B "
                "should line up with the curves in the spectrogram.")
    ok = (S / "rec.wav").exists() and (S / "flights.csv").exists() and M.get("t0")
    if not ok:
        st.warning("Required: flights.csv (①), rec.wav and start time (②).")
    else:
        c1, c2, c3, c4 = st.columns(4)
        klat = c1.number_input("Known latitude", value=float(M.get("known_lat", M.get("center_lat", 0.0))), format="%.6f")
        klon = c2.number_input("Known longitude", value=float(M.get("known_lon", M.get("center_lon", 0.0))), format="%.6f")
        agl = c3.number_input("Mic height above ground (m)", value=float(M.get("obs_agl", 1.5)),
                              help="height ABOVE GROUND: 1.5 m hand-held, ~3 m per storey + 1.5 m. "
                                   "Terrain elevation is set in the sidebar.")
        dtm = c4.number_input("Clock uncertainty ± (s)", value=5.0)
        if st.button("Compute overlay", type="primary"):
            save_meta(S, known_lat=klat, known_lon=klon, obs_agl=agl)
            frame, dem, c = env(M, klat, klon)
            trajs = trajs_for(S, M, frame)
            sp, _ = cached_spectro(str(S / "rec.wav"), (S / "rec.wav").stat().st_mtime, 1.0, 0.25)
            _, _, obs = observers(frame, dem, np.array([0.0]), np.array([0.0]), agl)
            with st.spinner("Forward model + f0 fit…"):
                curves, dt = overlay_curves(sp, trajs, obs[0], float(M["t0"]), c, dt_max=dtm)
            tracks = from_frame(pd.read_csv(S / "tracks.csv")) if (S / "tracks.csv").exists() else None
            st.metric("Estimated clock offset", f"{dt:+.2f} s")
            st.dataframe(pd.DataFrame([{k: v for k, v in cv.items() if k not in ("ratio", "valid")} for cv in curves])
                         .round(2).sort_values("cpa_heard_s"), hide_index=True)
            show_fig(overlay_fig(sp, curves, tracks, 0, 1500, title=f"overlay (dt {dt:+.2f}s)"))
            # zoom on the best-explained passes: on a long recording the full view is unreadable
            st.markdown("**Zoom on the 4 best-scoring passes**")
            for cv in sorted([c for c in curves if np.isfinite(c["score"])], key=lambda c: -c["score"])[:4]:
                fig = overlay_fig(sp, [cv], tracks, 0, 1500, title=f"{cv['label']} — CPA {cv['cpa_m']:.0f} m, "
                                  f"f0 {cv['f0']:.0f} Hz, score {cv['score']:.2f}")
                fig.axes[0].set_xlim(max(0, cv["cpa_heard_s"] - 90), cv["cpa_heard_s"] + 90)
                show_fig(fig)

# ---------------------------------------------------------------- 5. Search
with tabs[4]:
    st.markdown("**Phase 2** — search for the position from the tracks, the ADS-B data and the start time.")
    ok = (S / "tracks.csv").exists() and (S / "flights.csv").exists() and M.get("t0")
    if not ok:
        st.warning("Required: flights.csv (①), start time (②), tracks (③).")
    else:
        c1, c2, c3 = st.columns(3)
        slat = c1.number_input("Search centre latitude", value=float(M.get("center_lat", 0.0)), format="%.5f")
        slon = c2.number_input("Search centre longitude", value=float(M.get("center_lon", 0.0)), format="%.5f")
        srad = c3.number_input("Radius (km)", value=8.0, min_value=1.0, max_value=40.0)
        c1, c2, c3, c4, c5 = st.columns(5)
        coarse = c1.number_input("Coarse grid (m)", value=250, step=50)
        fine = c2.number_input("Fine grid (m)", value=25, step=5)
        dtm = c3.number_input("Clock uncertainty ± (s)", value=3.0)
        aud = c4.number_input("'Must be audible' distance (m)", value=2000)
        mrng = c5.number_input("Max range (m)", value=8000)
        if st.button("Run search", type="primary"):
            frame, dem, c = env(M, slat, slon)
            trajs = trajs_for(S, M, frame)
            tracks = from_frame(pd.read_csv(S / "tracks.csv"))
            cfg = SearchConfig(radius_km=srad, coarse_m=coarse, fine_m=fine, dt_max=dtm, audible_m=aud,
                               max_range_m=mrng, obs_agl=float(M.get("obs_agl", 1.5)))
            box = st.empty()
            with st.spinner("Grid search…"):
                res = search(tracks, trajs, frame, dem, float(M["t0"]), float(M.get("duration", 300)), c, cfg,
                             log=lambda msg: box.caption(msg))
            res.to_csv(S / "candidates.csv", index=False)
            save_meta(S, center_lat=slat, center_lon=slon, search_coarse_m=coarse, search_fine_m=fine)
            st.rerun()
        if (S / "candidates.csv").exists():
            res = pd.read_csv(S / "candidates.csv")
            b = res.iloc[0]
            far = res[haversine(res.lat, res.lon, b.lat, b.lon) > 300]
            ref_pts = []  # known reference positions: (name, lat, lon)
            if M.get("truth"):
                ref_pts.append(("Ground truth (demo)", M["truth"]["lat"], M["truth"]["lon"]))
            elif M.get("known_lat") is not None:
                ref_pts.append(("Known position (tab ④)", M["known_lat"], M["known_lon"]))
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Best point", f"{b.lat:.4f}, {b.lon:.4f}", help=f"{b.lat:.6f}, {b.lon:.6f}")
            c2.metric("Cost (cumulative rms %)", f"{b.cost:.2f}")
            if len(far):
                c3.metric("2nd area / 1st ratio", f"{far.iloc[0].cost / max(b.cost, 1e-6):.2f}×",
                          help="cost of the best area more than 300 m away ÷ cost of the best point. "
                               "≈ 1.0 = plateau, position poorly constrained; > 1.5 = clear solution.")
            if ref_pts:
                name, rl, rn = ref_pts[0]
                c4.metric(f"Error vs {name[:1].lower() + name[1:]}", f"{haversine(b.lat, b.lon, rl, rn):.0f} m")

            # ---- relative cost classes (cost / best cost) ----
            CLASSES = [  # (key, label, upper bound of the ratio, RGB colour)
                ("c1", "≤ 1.05× — most likely area", 1.05, (0, 120, 50)),
                ("c2", "1.05 – 1.25× — plausible", 1.25, (110, 200, 70)),
                ("c3", "1.25 – 2× — unlikely", 2.0, (255, 165, 0)),
                ("c4", "> 2× — ruled out", np.inf, (215, 40, 40)),
            ]
            d = res[np.isfinite(res.cost)].copy()
            d["ratio"] = d.cost / max(b.cost, 1e-9)
            d["cls"] = pd.cut(d.ratio, [-np.inf] + [c[2] for c in CLASSES], labels=[c[0] for c in CLASSES]).astype(str)
            col = {k: rgb for k, _, _, rgb in CLASSES}
            lab = {k: l for k, l, _, _ in CLASSES}
            d["rgb"] = d.cls.map(col)
            d["classe"] = d.cls.map(lab)
            fl_cols = [c for c in d.columns if c.endswith("_flight")]
            d["vols"] = d[fl_cols].fillna("").astype(str).agg(", ".join, axis=1) if fl_cols else ""
            d["cout_txt"] = d.cost.map(lambda v: f"{v:.3f}")
            d["ratio_txt"] = d.ratio.map(lambda v: f"{v:.2f}")
            d["dt_txt"] = d["dt"].map(lambda v: f"{v:+.2f}")

            st.markdown("##### Map")
            with st.container(border=True):
                st.caption("Classes (point cost ÷ best cost) — tick to show")
                cc = st.columns(len(CLASSES))
                shown = []
                for (k, l, _, rgb), cx in zip(CLASSES, cc):
                    n = int((d.cls == k).sum())
                    cx.markdown(f"<span style='display:inline-block;width:12px;height:12px;border-radius:50%;"
                                f"background:rgb{rgb};margin-right:6px;vertical-align:middle'></span>"
                                f"<span style='vertical-align:middle'>{l} <b>({n})</b></span>",
                                unsafe_allow_html=True)
                    if cx.checkbox("show", value=(k != "c4"), key=f"show_{k}"):
                        shown.append(k)
                st.caption("Layers")
                o1, o2, o3, o4, o5 = st.columns(5)
                show_coarse = o1.checkbox("Coarse grid", True, key="show_coarse")
                show_fine = o2.checkbox("Fine grid", True, key="show_fine")
                show_top = o3.checkbox("Top 10", True, key="show_top")
                show_ref = o4.checkbox("Known position", True, key="show_ref", disabled=not ref_pts)
                show_fl = o5.checkbox("Tracks of matched flights", True, key="show_fl")
                opacity = st.slider("Point opacity", 0.1, 1.0, 0.55, 0.05, key="map_opacity")

            sel = d[d.cls.isin(shown)]
            stage_ok = []
            if show_coarse:
                stage_ok.append("coarse")
            if show_fine:
                stage_ok.append("fine")
            sel = sel[sel.stage.isin(stage_ok)] if "stage" in sel.columns else sel
            a = int(255 * opacity)
            r_coarse = float(M.get("search_coarse_m", 250)) * 0.45
            r_fine = float(M.get("search_fine_m", 25)) * 0.6
            layers = []
            for stg, rad in (("coarse", r_coarse), ("fine", r_fine)):  # coarse below, fine on top
                part = sel[sel.stage == stg] if "stage" in sel.columns else (sel if stg == "coarse" else sel.iloc[:0])
                if len(part):
                    part = part.assign(fill=[list(c) + [a] for c in part.rgb])
                    layers.append(pdk.Layer("ScatterplotLayer", part.drop(columns=["rgb"]), get_position="[lon, lat]",
                                            get_fill_color="fill", get_radius=rad, pickable=True))
            if show_fl and fl_cols and (S / "flights.csv").exists():
                fdf = read_csv(str(S / "flights.csv")).sort_values("t")
                names = set(b[fl_cols].dropna().astype(str)) - {""}
                paths = []
                for hx, g in fdf.groupby("hex"):
                    fl = g["flight"].dropna()
                    ty = g["type"].dropna()
                    lbl = f"{(fl.iloc[0].strip() if len(fl) and fl.iloc[0].strip() else hx)} ({ty.iloc[0] if len(ty) else '?'})"
                    if lbl in names:
                        paths.append({"vols": lbl, "path": g[["lon", "lat"]].values.tolist()})
                if paths:
                    layers.append(pdk.Layer("PathLayer", paths, get_path="path", get_color=[30, 90, 255, 220],
                                            width_min_pixels=2, pickable=True))
            if show_top:
                top = res.head(10).assign(classe="top 10", vols="", cout_txt=res.head(10).cost.map("{:.3f}".format),
                                          ratio_txt=(res.head(10).cost / b.cost).map("{:.2f}".format),
                                          dt_txt=res.head(10)["dt"].map("{:+.2f}".format))
                layers.append(pdk.Layer("ScatterplotLayer", top, get_position="[lon, lat]", get_fill_color=[0, 0, 0, 0],
                                        get_line_color=[20, 20, 20, 255], stroked=True, line_width_min_pixels=1.5,
                                        get_radius=r_fine * 2, pickable=True))
            layers.append(pdk.Layer("ScatterplotLayer", res.head(1).assign(classe="BEST POINT", vols="",
                                    cout_txt=f"{b.cost:.3f}", ratio_txt="1.00", dt_txt=f"{b['dt']:+.2f}"),
                                    get_position="[lon, lat]", get_fill_color=[230, 0, 230, 255],
                                    get_line_color=[255, 255, 255, 255], stroked=True, line_width_min_pixels=2,
                                    radius_min_pixels=7, get_radius=30, pickable=True))
            if show_ref and ref_pts:
                rp = pd.DataFrame([{"lat": rl, "lon": rn, "classe": nm, "vols": "", "cout_txt": "", "ratio_txt": "",
                                    "dt_txt": ""} for nm, rl, rn in ref_pts])
                layers.append(pdk.Layer("ScatterplotLayer", rp, get_position="[lon, lat]", get_fill_color=[0, 0, 0, 0],
                                        get_line_color=[0, 170, 255, 255], stroked=True, line_width_min_pixels=3,
                                        radius_min_pixels=10, get_radius=40, pickable=True))
            st.pydeck_chart(pdk.Deck(layers=layers, initial_view_state=pdk.ViewState(
                latitude=float(b.lat), longitude=float(b.lon), zoom=12), map_style=None,
                tooltip={"html": "<b>{classe}</b><br/>cost {cout_txt} ({ratio_txt}×)<br/>clock {dt_txt} s<br/>{vols}",
                         "style": {"fontSize": "12px"}}))
            leg = [("rgb(230,0,230)", "●", "best point"), ("#141414", "○", "top 10"),
                   ("rgb(30,90,255)", "━", "ADS-B track of matched flights")]
            if ref_pts:
                leg.append(("rgb(0,170,255)", "◯", ref_pts[0][0].lower()))
            st.markdown(" &nbsp;·&nbsp; ".join(f"<span style='color:{c};font-weight:700'>{s}</span> {t}"
                                               for c, s, t in leg), unsafe_allow_html=True)

            with st.expander("Top 15 candidates"):
                show_cols = [c for c in res.columns if c not in ("e", "n")]
                st.dataframe(res.head(15)[show_cols].round(5), hide_index=True)
            with st.expander("Static view (matplotlib)"):
                show_fig(heatmap_fig(res, M.get("truth") or (
                    {"lat": M["known_lat"], "lon": M["known_lon"]} if M.get("known_lat") is not None else None)))
            st.download_button("Download candidates.csv", (S / "candidates.csv").read_bytes(), "candidates.csv")
