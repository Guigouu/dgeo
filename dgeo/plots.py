# SPDX-License-Identifier: AGPL-3.0-or-later
"""PNG figures (matplotlib, headless backend)."""
from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from .audio import Spectro  # noqa: E402


def spectro_fig(sp: Spectro, fmin=0.0, fmax=1000.0, what="z", ax=None, title=None):
    own = ax is None
    if own:
        fig, ax = plt.subplots(figsize=(14, 6))
    m = (sp.f >= fmin) & (sp.f <= fmax)
    img = sp.z[m] if what == "z" else sp.db[m]
    vmin, vmax = (0.5, 3) if what == "z" else np.percentile(img, [5, 99.5])
    ax.imshow(img, origin="lower", aspect="auto", cmap="magma", vmin=vmin, vmax=vmax,
              extent=[sp.t[0], sp.t[-1], sp.f[m][0], sp.f[m][-1]], interpolation="nearest")
    ax.set_xlabel("time since start (s)")
    ax.set_ylabel("frequency (Hz)")
    if title:
        ax.set_title(title)
    return (ax.figure, ax) if own else ax


def overlay_fig(sp: Spectro, curves, tracks=None, fmin=0.0, fmax=600.0, n_harm=4, out=None, title=None):
    fig, ax = spectro_fig(sp, fmin, fmax, title=title)
    colors = plt.cm.tab10.colors
    for i, cv in enumerate(curves):
        if not np.isfinite(cv.get("f0", np.nan)):
            continue
        col = colors[i % 10]
        v = cv["valid"]
        for k in range(1, n_harm + 1):
            ax.plot(sp.t[v], k * cv["f0"] * cv["ratio"][v], color=col, lw=1.0, alpha=0.8,
                    ls="-" if k == 1 else "--")
        tt = np.clip(cv["cpa_heard_s"], sp.t[0], sp.t[-1])
        ax.text(tt, fmax * 0.95, f"{cv['label']}\nCPA {cv['cpa_m']:.0f} m\nscore {cv['score']:.2f}",
                color=col, fontsize=8, va="top", ha="center",
                bbox=dict(facecolor="black", alpha=0.5, lw=0))
    for tr in tracks or []:
        ax.plot(tr.t, tr.f, ".", color="cyan", ms=2)
    ax.set_ylim(fmin, fmax)
    if out:
        fig.savefig(out, dpi=110, bbox_inches="tight")
        plt.close(fig)
    return fig


def heatmap_fig(df, truth=None, top=10, out=None, title=None):
    fig, ax = plt.subplots(figsize=(9, 8))
    d = df[np.isfinite(df["cost"])].sort_values("cost", ascending=False)
    sc = ax.scatter(d["lon"], d["lat"], c=np.log10(d["cost"] + 0.05), s=6, cmap="viridis_r")
    fig.colorbar(sc, ax=ax, label="log10(cost)  (lower = better)")
    best = df.nsmallest(top, "cost")
    ax.scatter(best["lon"], best["lat"], marker="x", c="red", s=40, label=f"top {top}")
    b0 = best.iloc[0]
    ax.scatter([b0["lon"]], [b0["lat"]], marker="*", c="red", s=250, label="best")
    if truth:
        ax.scatter([truth["lon"]], [truth["lat"]], marker="o", facecolors="none", edgecolors="white",
                   s=200, lw=2, label="ground truth")
    ax.set_xlabel("lon")
    ax.set_ylabel("lat")
    ax.set_aspect(1 / np.cos(np.radians(df["lat"].mean())))
    ax.legend(loc="upper right")
    if title:
        ax.set_title(title)
    if out:
        fig.savefig(out, dpi=110, bbox_inches="tight")
        plt.close(fig)
    return fig
