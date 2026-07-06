"""Render a shot as a PNG chart (sent as the post-shot photo).

Same visual language as the journal and the GaggiMate web UI: temperature on
the left axis, pressure/flow on the right, weight on its own scale, dashed
targets, phase markers. matplotlib is an optional dependency (``[plots]``
extra) — callers fall back to a text summary when it is missing.
"""

from __future__ import annotations

import io

from .slog import Shot

# Same palette as the GaggiMate web UI shot chart (design match, no code copied)
COLORS = {
    "temp": "#F0561D",
    "target_temp": "#731F00",
    "press": "#0066CC",
    "flow": "#63993D",
    "puck": "#204D00",
    "weight": "#8B5CF6",
    "wflow": "#4b2e8d",
    "phase": "#6B7280",
}


def render_shot_png(shot: Shot, *, title: str | None = None) -> bytes:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    t = shot.times_s
    s = shot.series
    have = lambda k: k in s and any(s[k])  # noqa: E731

    fig, ax_temp = plt.subplots(figsize=(8, 4.5), dpi=110)
    ax_bar = ax_temp.twinx()

    dash = (0, (4, 4))
    if have("ct"):
        ax_temp.plot(t, s["ct"], color=COLORS["temp"], lw=2.2, label="Temp")
    if have("tt"):
        ax_temp.plot(t, s["tt"], color=COLORS["target_temp"], lw=1.4, ls=dash)
    if have("cp"):
        ax_bar.plot(t, s["cp"], color=COLORS["press"], lw=2.2, label="Pressure")
    if have("tp"):
        ax_bar.plot(t, s["tp"], color=COLORS["press"], lw=1.4, ls=dash, alpha=0.8)
    if have("fl"):
        ax_bar.plot(t, s["fl"], color=COLORS["flow"], lw=1.8, label="Pump flow")
    if have("tf"):
        ax_bar.plot(t, s["tf"], color=COLORS["flow"], lw=1.4, ls=dash, alpha=0.8)
    if have("pf"):
        ax_bar.plot(t, s["pf"], color=COLORS["puck"], lw=1.8, label="Puck flow")

    weight = s.get("v") if have("v") else s.get("ev") if have("ev") else None
    if weight:
        ax_g = ax_temp.twinx()
        ax_g.spines.right.set_position(("axes", 1.12))
        ax_g.plot(t, weight, color=COLORS["weight"], lw=2.2, label="Weight")
        ax_g.set_ylabel("g", color=COLORS["weight"])
        ax_g.set_ylim(bottom=0)
        ax_g.tick_params(axis="y", colors=COLORS["weight"], labelsize=8)

    for phase in shot.phases:
        px = phase.sample_index * shot.sample_interval_ms / 1000
        if 0.3 < px < (t[-1] if t else 0) - 0.3:
            ax_temp.axvline(px, color=COLORS["phase"], lw=1.0, alpha=0.8)
            ax_temp.annotate(
                phase.name[:18], (px, 0.99), xycoords=("data", "axes fraction"),
                rotation=90, va="top", ha="right", fontsize=6.5, color="white",
                bbox={"boxstyle": "round,pad=0.25", "facecolor": "#162132",
                      "alpha": 0.75, "edgecolor": "none"},
            )

    ax_temp.set_xlabel("s", fontsize=8)
    ax_temp.set_ylabel("°C", color=COLORS["temp"], fontsize=9)
    ax_temp.tick_params(labelsize=8)
    ax_temp.tick_params(axis="y", colors=COLORS["temp"])
    ax_bar.set_ylabel("bar · g/s", color=COLORS["press"], fontsize=9)
    ax_bar.tick_params(axis="y", colors=COLORS["press"], labelsize=8)
    ax_bar.set_ylim(0, 16)
    ax_temp.grid(True, axis="y", lw=0.3, alpha=0.4)
    if title:
        ax_temp.set_title(title, fontsize=10)

    handles, labels = [], []
    for ax in fig.axes:
        h, lab = ax.get_legend_handles_labels()
        handles += h
        labels += lab
    fig.legend(handles, labels, loc="lower center", ncol=len(labels),
               bbox_to_anchor=(0.5, 0.0), fontsize=7.5, frameon=False)

    fig.tight_layout(rect=(0, 0.08, 1, 1))
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()
