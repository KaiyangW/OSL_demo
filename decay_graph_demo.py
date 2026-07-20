"""Interactive Plotly explorer + publication Matplotlib export for the demo.

Run:
    python decay_plot_interactive.py fit_results.xlsx
    python decay_plot_interactive.py              # pops a file picker

What this script does
---------------------
1. Read the Excel workbook produced by ``fit_gui.py`` (sheets ``Parameters``
   and ``Fit_Curve``).
2. Open a Plotly figure in the default browser with:
     - Data, Fit, and optional IRF traces
     - Weighted-residual sub-panel
     - Per-series vertical baseline offset (the "观赏性" knob)
     - Log-x toggle, axis-range zoom, legend toggle, colour picker
     - Annotation box with tau / beta / chi2 pulled from the Parameters sheet
     - Time-unit selector (ns / us / ms / s, auto by default)
     - A "Save publication PNG/PDF" button that re-renders the current view
       as a 600 dpi Matplotlib figure next to the .xlsx file.

What is intentionally NOT here
------------------------------
- No Dash server (just Plotly's built-in HTML + a tiny Flask callback for
  the export button — keeps the dependency surface small).
- No EMF export.
- No multi-file overlay (single workbook at a time).

Dependencies: numpy, pandas, plotly, matplotlib, openpyxl.
"""

from __future__ import annotations

import math
import os
import re
import sys
import base64
import io
import json
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker

import plotly.graph_objects as go
from plotly.subplots import make_subplots
from dash import Dash, Input, Output, State, dcc, html, no_update
import webbrowser


# ----------------------------------------------------------------------
# Style constants (mirror the private PlotUtils publication settings)
# ----------------------------------------------------------------------
FONT_FAMILY        = "Arial"
FONT_SIZE          = 22
EXPORT_DPI         = 600
EXPORT_WIDTH_PX    = 850
MARGIN_LEFT_PX     = 90
MARGIN_RIGHT_PX    = 90
MARGIN_TOP_PX      = 40
MARGIN_BOTTOM_PX   = 90
DECAY_DATA_H_PX    = 600
RESIDUAL_DATA_H    = 170
PANEL_GAP_PX       = 20
TICK_WIDTH         = 1.0
MAJOR_TICK_LEN     = 8
MINOR_TICK_LEN     = 4
AXES_LINEWIDTH     = 1.0
RESIDUAL_LINE_W    = 1.0
RESIDUAL_LABEL_COL = "#8B0000"

COLOR_DATA = "#003399"
COLOR_FIT  = "#CC0000"
COLOR_IRF  = "#808080"
COLOR_RES  = "#000000"

TIME_UNIT_DIVISORS = {"ns": 1, "us": 1e3, "ms": 1e6, "s": 1e9}
TIME_UNIT_CHOICES  = ["auto", "ns", "us", "ms", "s"]


# ----------------------------------------------------------------------
# Excel ingestion
# ----------------------------------------------------------------------
def _parse_formatted_time(text):
    text = str(text or "").strip()
    if not text:
        return "", ""
    m = re.match(r"^([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)\s*(\S+)$", text)
    if not m:
        return text, ""
    return m.group(1), m.group(2)


def read_params_defaults(filepath):
    defaults = {"chi2": "", "tau": "", "tau_unit": "ns", "beta": "", "tau_sub": "p",
                "ex": "", "emi": ""}
    try:
        df = pd.read_excel(filepath, sheet_name="Parameters")
    except Exception:
        return defaults
    if df is None or df.empty:
        return defaults

    p_col = v_col = f_col = None
    for c in df.columns:
        cl = str(c).strip().lower()
        if cl in ("parameter", "param"):
            p_col = c
        elif cl == "value":
            v_col = c
        elif cl in ("formatted_value", "formatted value"):
            f_col = c
    if p_col is None or v_col is None:
        if len(df.columns) < 2:
            return defaults
        p_col, v_col = df.columns[0], df.columns[1]

    lookup = {}
    for _, row in df.iterrows():
        pname = str(row[p_col]).strip()
        val = row[v_col]
        fmt = row[f_col] if f_col is not None and f_col in row.index else None
        if pd.notna(val) or (fmt is not None and pd.notna(fmt) and str(fmt).strip()):
            lookup[pname] = {"value": val, "formatted": fmt}

    def fscalar(v):
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return ""
        if isinstance(v, float):
            return str(int(v)) if v == int(v) else f"{v:.6g}"
        return str(v).strip()

    chi2 = lookup.get("Reduced Chi-Squared")
    if chi2:
        defaults["chi2"] = fscalar(chi2["value"])

    for key in ("Int_Ave_Tau_2 (ns)", "Int_Ave_Tau_1 (ns)"):
        e = lookup.get(key)
        if not e:
            continue
        fmt = e.get("formatted")
        if fmt is not None and pd.notna(fmt) and str(fmt).strip():
            val, unit = _parse_formatted_time(fmt)
            if val:
                defaults["tau"] = val
                defaults["tau_unit"] = unit or "ns"
                break
        val = e.get("value")
        if val is not None and not (isinstance(val, float) and pd.isna(val)):
            defaults["tau"] = fscalar(val)
            defaults["tau_unit"] = "ns"
            break

    for key in ("Beta_Stretching_2", "Beta_Stretching_1"):
        e = lookup.get(key)
        if e:
            defaults["beta"] = fscalar(e["value"])
            break

    return defaults


def load_fit_curve(filepath):
    df = pd.read_excel(filepath, sheet_name="Fit_Curve")
    if "Full_Time (ns)" not in df.columns:
        raise ValueError("Missing Full_Time (ns) column in Fit_Curve sheet.")

    y_cols = [c for c in df.columns
              if c not in ("Full_Time (ns)", "Fit_Time (ns)")
              and pd.api.types.is_numeric_dtype(df[c])]
    data_col = next((c for c in y_cols if c in ("Plot_Counts", "Raw_Counts")), None)
    irf_col  = next((c for c in y_cols if "IRF" in str(c) and not str(c).startswith("Fit_")), None)
    fit_col  = next((c for c in y_cols if c in ("Fit_Plot_Fitted Data", "Fit_Fitted Data")), None)
    res_col  = "Fit_Weighted Residuals" if "Fit_Weighted Residuals" in df.columns else None
    fit_time_col = "Fit_Time (ns)" if "Fit_Time (ns)" in df.columns else "Full_Time (ns)"

    if data_col is None or fit_col is None:
        raise ValueError("Fit_Curve sheet missing decay/fit columns.")

    return {
        "df": df,
        "time_col":    "Full_Time (ns)",
        "fit_time_col": fit_time_col,
        "data_col":    data_col,
        "irf_col":     irf_col,
        "fit_col":     fit_col,
        "res_col":     res_col,
        "t_max_ns":    float(df["Full_Time (ns)"].max()),
    }


def resolve_time_unit(max_ns, choice="auto"):
    if choice and choice != "auto" and choice in TIME_UNIT_DIVISORS:
        return TIME_UNIT_DIVISORS[choice], choice
    if max_ns >= 1e9:
        return 1e9, "s"
    if max_ns >= 1e6:
        return 1e6, "ms"
    if max_ns >= 1e3:
        return 1e3, "us"
    return 1, "ns"


# ----------------------------------------------------------------------
# Annotation text (Plotly HTML and Matplotlib LaTeX)
# ----------------------------------------------------------------------
def annotation_html(params):
    lines = []
    tau  = params.get("tau", "")
    unit = params.get("tau_unit", "ns")
    beta = params.get("beta", "")
    chi2 = params.get("chi2", "")
    sub  = params.get("tau_sub", "")
    ex   = params.get("ex", "")
    emi  = params.get("emi", "")
    if ex:
        lines.append(f"λ<sub>ex</sub> = {ex} nm")
    if emi:
        lines.append(f"λ<sub>emi</sub> = {emi} nm")
    if tau:
        sub_html = f"<sub>{sub}</sub>" if sub else ""
        lines.append(f"τ{sub_html} = {tau} {unit}")
    if beta:
        lines.append(f"β = {beta}")
    if chi2:
        lines.append(f"χ<sup>2</sup> = {chi2}")
    return "<br>".join(lines)


def annotation_latex(params):
    lines = []
    tau  = params.get("tau", "")
    unit = params.get("tau_unit", "ns")
    beta = params.get("beta", "")
    chi2 = params.get("chi2", "")
    sub  = params.get("tau_sub", "")
    if tau:
        sub_ltx = r"_{\mathrm{" + sub + r"}} " if sub else " "
        unit_ltx = {"ns": r"\ \mathrm{ns}", "us": r"\ \mathrm{us}",
                    "ms": r"\ \mathrm{ms}", "s": r"\ \mathrm{s}"}.get(unit, r"\ \mathrm{ns}")
        lines.append(rf"$\tau{sub_ltx}= {tau}{unit_ltx}$")
    if beta:
        lines.append(rf"$\beta = {beta}$")
    if chi2:
        lines.append(rf"$\chi^2 = {chi2}$")
    return "\n".join(lines)


# ----------------------------------------------------------------------
# Helpers shared by Plotly and Matplotlib rendering
# ----------------------------------------------------------------------
def nice_residual_limit(max_abs):
    if max_abs <= 0 or not math.isfinite(max_abs):
        return 1.0
    mag = 10 ** math.floor(math.log10(max_abs))
    scaled = max_abs / mag
    for m in (1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10):
        if scaled <= m:
            return m * mag
    return 10 * mag


def format_residual_tick(v):
    if abs(v) < 1e-12:
        return "0"
    if abs(v) >= 1000 or abs(v) < 0.01:
        return f"{v:.2e}"
    return f"{v:.3g}"


def _shift(series, off):
    off = float(off or 0.0)
    return series + off if off != 0.0 else series


# ----------------------------------------------------------------------
# JSON config persistence (saved next to the .xlsx)
# ----------------------------------------------------------------------
def _config_path(filepath):
    """Path of the per-dataset plot config JSON, beside the .xlsx."""
    base = os.path.splitext(filepath)[0]
    return base + "_plot_config.json"


def load_config(filepath):
    """Load a previously-saved plot config, or None if not present."""
    p = _config_path(filepath)
    if not os.path.isfile(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[config] load failed ({e}); using defaults")
        return None


def save_config(filepath, cfg):
    """Persist the current plot config next to the .xlsx."""
    # Don't dump colors/widths (they're constants); keep the file human-readable.
    slim = {
        "visible":    cfg.get("visible", {}),
        "offsets":    cfg.get("offsets", {}),
        "log_x":      cfg.get("log_x", False),
        "time_unit":  cfg.get("time_unit", "auto"),
        "xrange":     cfg.get("xrange"),
        "yrange":     cfg.get("yrange"),
        "legend_pos": cfg.get("legend_pos", {}),
        "text_pos":   cfg.get("text_pos", {}),
        "text_params": cfg.get("text_params", {}),
    }
    try:
        with open(_config_path(filepath), "w", encoding="utf-8") as f:
            json.dump(slim, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[config] save failed: {e}")


def legend_anchor(x, y):
    """Pick a sensible xanchor/yanchor so the legend stays on-screen."""
    xa = "right" if x > 0.5 else "left"
    ya = "top"   if y > 0.5 else "bottom"
    return xa, ya


_LEGEND_LOC_MAP = {
    ("left",  "bottom"): "lower left",
    ("left",  "top"):    "upper left",
    ("right", "bottom"): "lower right",
    ("right", "top"):    "upper right",
}


def _legend_pos(cfg):
    lp = cfg.get("legend_pos") or {}
    return float(lp.get("x", 0.97)), float(lp.get("y", 0.97))


def _text_pos(cfg):
    """Position of the tau/beta/chi2 annotation box, in paper-fraction coords."""
    tp = cfg.get("text_pos") or {}
    return float(tp.get("x", 0.05)), float(tp.get("y", 0.05))


def text_anchor(x, y):
    """Anchor the annotation box so it stays on canvas regardless of corner."""
    xa = "left"  if x < 0.5 else "right"
    ya = "top"   if y > 0.5 else "bottom"
    return xa, ya


_TEXT_VA_MAP = {"top": "top", "bottom": "bottom"}
_TEXT_HA_MAP = {"left": "left", "right": "right"}


# ----------------------------------------------------------------------
# Plotly figure builder (respects offsets / visible flags / log-x)
# ----------------------------------------------------------------------
def build_plotly(info, cfg):
    df = info["df"]
    divisor, unit = resolve_time_unit(info["t_max_ns"], cfg.get("time_unit", "auto"))
    offsets  = cfg.get("offsets", {})
    visible  = cfg.get("visible", {})
    colors   = cfg.get("colors", {})
    widths   = cfg.get("widths", {})
    log_x    = bool(cfg.get("log_x", False))
    xrange   = cfg.get("xrange")
    yrange   = cfg.get("yrange")

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        row_heights=[0.78, 0.22], vertical_spacing=0.06,
    )

    has_residuals = False
    residual_series = []

    # IRF
    if visible.get("irf", False) and info["irf_col"]:
        s = df[[info["time_col"], info["irf_col"]]].dropna()
        if not s.empty:
            fig.add_trace(go.Scatter(
                x=s[info["time_col"]] / divisor,
                y=_shift(s[info["irf_col"]], offsets.get("irf", 0.0)),
                mode="lines",
                line=dict(color=colors.get("irf", COLOR_IRF),
                          width=widths.get("irf", 1.5), dash="dash"),
                name="IRF",
            ), row=1, col=1)

    # Data
    if visible.get("raw", True):
        s = df[[info["time_col"], info["data_col"]]].dropna()
        if not s.empty:
            fig.add_trace(go.Scatter(
                x=s[info["time_col"]] / divisor,
                y=_shift(s[info["data_col"]], offsets.get("raw", 0.0)),
                mode="lines",
                line=dict(color=colors.get("raw", COLOR_DATA),
                          width=widths.get("raw", 2.0)),
                name="Data",
            ), row=1, col=1)

    # Fit
    if visible.get("fit", True):
        s = df[[info["fit_time_col"], info["fit_col"]]].dropna()
        if not s.empty:
            fig.add_trace(go.Scatter(
                x=s[info["fit_time_col"]] / divisor,
                y=_shift(s[info["fit_col"]], offsets.get("fit", 0.0)),
                mode="lines",
                line=dict(color=colors.get("fit", COLOR_FIT),
                          width=widths.get("fit", 2.5)),
                name="Fit",
            ), row=1, col=1)

    # Residuals
    if visible.get("residual", True) and info["res_col"]:
        s = df[[info["fit_time_col"], info["res_col"]]].dropna()
        if not s.empty:
            fig.add_trace(go.Scatter(
                x=s[info["fit_time_col"]] / divisor,
                y=s[info["res_col"]],
                mode="lines",
                line=dict(color=colors.get("residual", COLOR_RES),
                          width=widths.get("residual", RESIDUAL_LINE_W)),
                name="Residuals", showlegend=False,
            ), row=2, col=1)
            residual_series.append(s[info["res_col"]])
            has_residuals = True

    default_xmax = info["t_max_ns"] / divisor
    if log_x:
        pos_min = None
        for col in (info["data_col"], info["fit_col"]):
            if col is None or col not in df.columns:
                continue
            t = df[info["time_col"]].to_numpy()
            y = df[col].to_numpy()
            mask = (t > 0) & np.isfinite(y)
            if mask.any():
                v = float(t[mask].min()) / divisor
                if pos_min is None or v < pos_min:
                    pos_min = v
        xmin = pos_min if pos_min and pos_min > 0 else max(default_xmax * 1e-5, 1e-3)
        xmax = default_xmax * 1.02
        if xrange and len(xrange) == 2:
            try:
                lo, hi = float(xrange[0]), float(xrange[1])
                if math.isfinite(hi) and hi > 0:
                    xmax = hi
                if math.isfinite(lo) and lo > 0:
                    xmin = lo
            except (TypeError, ValueError):
                pass
        xaxis_kwargs = {"type": "log", "range": [math.log10(xmin), math.log10(xmax)]}
    else:
        xaxis_kwargs = {"tickformat": ".0f"}
        if xrange and len(xrange) == 2 and xrange[1] <= default_xmax * 1.5:
            xaxis_kwargs["range"] = list(xrange)
        else:
            xaxis_kwargs["range"] = [0, default_xmax * 1.02]

    axis_base = dict(
        ticks="inside", tickwidth=1.0, ticklen=8, tickcolor="black",
        showline=True, linewidth=1.0, linecolor="black",
        mirror=True, showgrid=False,
    )

    fig.update_xaxes(**axis_base, **xaxis_kwargs, row=2, col=1)
    fig.update_xaxes(**axis_base, **xaxis_kwargs, showticklabels=False, row=1, col=1)
    fig.update_yaxes(**axis_base, type="log",
                     exponentformat="power",
                     **({"range": yrange} if yrange else {}),
                     row=1, col=1)

    if has_residuals:
        max_abs = float(np.nanmax([np.nanmax(np.abs(s)) for s in residual_series])) if residual_series else 1.0
        yl = nice_residual_limit(max_abs * 1.03)
        yt = [-yl, 0.0, yl]
        fig.update_yaxes(
            **axis_base, range=[-yl, yl],
            tickmode="array", tickvals=yt,
            ticktext=[format_residual_tick(v) for v in yt],
            row=2, col=1,
        )
        fig.add_hline(y=0, line_dash="dot", line_color="#aaaaaa", line_width=1, row=2, col=1)
        fig.add_annotation(
            x=0.97, y=0.97, xref="x2 domain", yref="y2 domain",
            text="Weighted Residuals", showarrow=False,
            font=dict(family=FONT_FAMILY, size=FONT_SIZE - 4, color=RESIDUAL_LABEL_COL),
            xanchor="right", yanchor="top",
        )

    lx, ly = _legend_pos(cfg)
    xa, ya = legend_anchor(lx, ly)

    fig.update_layout(
        width=900, height=750, autosize=False,
        font=dict(family=FONT_FAMILY, size=FONT_SIZE - 4, color="black"),
        plot_bgcolor="white", paper_bgcolor="white",
        legend=dict(
            x=lx, y=ly, xanchor=xa, yanchor=ya,
            bgcolor="rgba(255,255,255,0.85)",
            bordercolor="rgba(0,0,0,0.25)", borderwidth=1,
            font=dict(size=FONT_SIZE - 4),
        ),
        margin=dict(l=90, r=40, t=40, b=90),
        # uirevision keeps user drags (legend, zoom) stable across re-renders
        uirevision="stable",
    )

    ann = annotation_html(cfg.get("text_params", {}))
    if ann:
        tx, ty = _text_pos(cfg)
        txa, tya = text_anchor(tx, ty)
        fig.add_annotation(
            x=tx, y=ty, xref="paper", yref="paper",
            text=ann, showarrow=False, align="left",
            xanchor=txa, yanchor=tya,
            font=dict(family=FONT_FAMILY, size=FONT_SIZE - 4, color="black"),
            bgcolor="rgba(255,255,255,0.85)",
            bordercolor="rgba(0,0,0,0.25)", borderwidth=1,
            captureevents=True,
        )

    return fig, divisor, unit, default_xmax


# ----------------------------------------------------------------------
# Matplotlib static export (respects current view: offsets, log-x, ranges)
# ----------------------------------------------------------------------
def setup_matplotlib_style():
    plt.rcParams.update({
        "font.family":      FONT_FAMILY,
        "font.size":        FONT_SIZE,
        "axes.linewidth":   AXES_LINEWIDTH,
        "xtick.direction":  "in",
        "ytick.direction":  "in",
        "xtick.major.size": MAJOR_TICK_LEN,
        "xtick.minor.size": MINOR_TICK_LEN,
        "ytick.major.size": MAJOR_TICK_LEN,
        "ytick.minor.size": MINOR_TICK_LEN,
        "xtick.major.width": TICK_WIDTH,
        "ytick.major.width": TICK_WIDTH,
        "axes.grid":        False,
    })


def export_matplotlib(info, cfg, out_base):
    setup_matplotlib_style()

    df = info["df"]
    divisor, unit = resolve_time_unit(info["t_max_ns"], cfg.get("time_unit", "auto"))
    offsets = cfg.get("offsets", {})
    colors  = cfg.get("colors", {})
    widths  = cfg.get("widths", {})
    visible = cfg.get("visible", {})
    log_x   = bool(cfg.get("log_x", False))
    xrange  = cfg.get("xrange")
    yrange  = cfg.get("yrange")
    default_xmax = info["t_max_ns"] / divisor

    total_h_px = (MARGIN_TOP_PX + DECAY_DATA_H_PX + PANEL_GAP_PX
                  + RESIDUAL_DATA_H + MARGIN_BOTTOM_PX)
    fig = plt.figure(figsize=(EXPORT_WIDTH_PX / 72, total_h_px / 72), dpi=EXPORT_DPI)
    axes_left = MARGIN_LEFT_PX / EXPORT_WIDTH_PX
    axes_w    = (EXPORT_WIDTH_PX - MARGIN_LEFT_PX - MARGIN_RIGHT_PX) / EXPORT_WIDTH_PX
    decay_bottom = (MARGIN_BOTTOM_PX + RESIDUAL_DATA_H + PANEL_GAP_PX) / total_h_px
    decay_h      = DECAY_DATA_H_PX / total_h_px
    res_bottom   = MARGIN_BOTTOM_PX / total_h_px
    res_h        = RESIDUAL_DATA_H / total_h_px

    ax_decay = fig.add_axes([axes_left, decay_bottom, axes_w, decay_h])
    ax_res   = fig.add_axes([axes_left, res_bottom, axes_w, res_h], sharex=ax_decay)

    # IRF
    if visible.get("irf", False) and info["irf_col"]:
        s = df[[info["time_col"], info["irf_col"]]].dropna()
        if not s.empty:
            ax_decay.plot(
                s[info["time_col"]] / divisor,
                _shift(s[info["irf_col"]], offsets.get("irf", 0.0)),
                color=colors.get("irf", COLOR_IRF),
                linewidth=widths.get("irf", 1.5), linestyle="--", label="IRF",
            )

    # Data
    if visible.get("raw", True):
        s = df[[info["time_col"], info["data_col"]]].dropna()
        if not s.empty:
            ax_decay.plot(
                s[info["time_col"]] / divisor,
                _shift(s[info["data_col"]], offsets.get("raw", 0.0)),
                color=colors.get("raw", COLOR_DATA),
                linewidth=widths.get("raw", 2.0), label="Data",
            )

    # Fit
    if visible.get("fit", True):
        s = df[[info["fit_time_col"], info["fit_col"]]].dropna()
        if not s.empty:
            ax_decay.plot(
                s[info["fit_time_col"]] / divisor,
                _shift(s[info["fit_col"]], offsets.get("fit", 0.0)),
                color=colors.get("fit", COLOR_FIT),
                linewidth=widths.get("fit", 2.5), label="Fit",
            )

    ax_decay.set_yscale("log")
    ax_decay.set_ylabel("Counts", fontsize=FONT_SIZE)
    ax_decay.yaxis.set_major_formatter(ticker.LogFormatterMathtext())
    ax_decay.tick_params(axis="x", which="both", labelbottom=False)
    for sp in ax_decay.spines.values():
        sp.set_visible(True); sp.set_linewidth(AXES_LINEWIDTH)
    ax_decay.tick_params(axis="x", which="both", direction="in", top=False)
    ax_decay.tick_params(axis="y", which="both", direction="in", right=False)
    ax_decay.tick_params(which="major", length=MAJOR_TICK_LEN)
    ax_decay.tick_params(which="minor", length=MINOR_TICK_LEN)

    lx, ly = _legend_pos(cfg)
    xa, ya = legend_anchor(lx, ly)
    leg = ax_decay.legend(
        loc=_LEGEND_LOC_MAP[(xa, ya)],
        bbox_to_anchor=(lx, ly),
        handlelength=1.8, handletextpad=0.5,
    )
    for text, line in zip(leg.get_texts(), leg.get_lines()):
        text.set_color(line.get_color())

    ann = annotation_latex(cfg.get("text_params", {}))
    if ann:
        tx, ty = _text_pos(cfg)
        txa, tya = text_anchor(tx, ty)
        ax_decay.text(
            tx, ty, ann, transform=ax_decay.transAxes,
            fontsize=FONT_SIZE,
            verticalalignment=_TEXT_VA_MAP[tya],
            horizontalalignment=_TEXT_HA_MAP[txa],
            bbox=dict(boxstyle="round,pad=0.3", facecolor="white",
                      edgecolor="0.5", linewidth=0.5, alpha=0.85),
        )

    # Residual panel
    has_res = False
    res_series = []
    if visible.get("residual", True) and info["res_col"]:
        s = df[[info["fit_time_col"], info["res_col"]]].dropna()
        if not s.empty:
            ax_res.plot(
                s[info["fit_time_col"]] / divisor, s[info["res_col"]],
                color=colors.get("residual", COLOR_RES),
                linewidth=widths.get("residual", RESIDUAL_LINE_W),
            )
            res_series.append(s[info["res_col"]])
            has_res = True

    if res_series:
        max_abs = float(np.nanmax([np.nanmax(np.abs(s)) for s in res_series]))
        yl = nice_residual_limit(max_abs * 1.03)
    else:
        yl = 1.0
    ax_res.axhline(0, color="#aaaaaa", linewidth=1, linestyle="--")
    ax_res.set_xlabel(f"Time ({unit})", fontsize=FONT_SIZE)
    ax_res.set_ylabel("")
    ax_res.set_ylim(-yl, yl)
    yt = [-yl, 0.0, yl]
    ax_res.yaxis.set_major_locator(ticker.FixedLocator(yt))
    ax_res.yaxis.set_major_formatter(
        ticker.FuncFormatter(lambda v, _: format_residual_tick(v)))
    ax_res.yaxis.set_minor_locator(ticker.NullLocator())
    ax_res.xaxis.set_major_formatter(ticker.FormatStrFormatter("%.0f"))
    ax_res.tick_params(axis="x", which="both", direction="in", top=False,
                       labelsize=FONT_SIZE, width=TICK_WIDTH)
    ax_res.tick_params(axis="y", which="both", direction="in", right=False,
                       labelsize=FONT_SIZE, width=TICK_WIDTH)
    ax_res.tick_params(which="major", length=MAJOR_TICK_LEN)
    for sp in ax_res.spines.values():
        sp.set_visible(True); sp.set_linewidth(AXES_LINEWIDTH)
    ax_res.text(0.97, 0.97, "Weighted Residuals",
                transform=ax_res.transAxes, fontsize=FONT_SIZE,
                color=RESIDUAL_LABEL_COL,
                verticalalignment="top", horizontalalignment="right")

    # X limits
    if log_x:
        for ax in (ax_decay, ax_res):
            ax.set_xscale("log", nonpositive="clip")
            ax.xaxis.set_major_formatter(ticker.LogFormatterMathtext())
        # Same xmin/xmax heuristic as Plotly
        pos_min = None
        for col in (info["data_col"], info["fit_col"]):
            if col is None or col not in df.columns:
                continue
            t = df[info["time_col"]].to_numpy()
            y = df[col].to_numpy()
            mask = (t > 0) & np.isfinite(y)
            if mask.any():
                v = float(t[mask].min()) / divisor
                if pos_min is None or v < pos_min:
                    pos_min = v
        xmin = pos_min if pos_min and pos_min > 0 else max(default_xmax * 1e-5, 1e-3)
        xmax = default_xmax * 1.02
        if xrange and len(xrange) == 2:
            try:
                lo, hi = float(xrange[0]), float(xrange[1])
                if math.isfinite(hi) and hi > 0:
                    xmax = hi
                if math.isfinite(lo) and lo > 0:
                    xmin = lo
            except (TypeError, ValueError):
                pass
        ax_decay.set_xlim(xmin, xmax)
    else:
        if xrange and len(xrange) == 2 and xrange[1] <= default_xmax * 1.5:
            ax_decay.set_xlim(xrange[0], xrange[1])
        else:
            ax_decay.set_xlim(0, default_xmax * 1.02)

    if yrange and len(yrange) == 2:
        try:
            ax_decay.set_ylim(10 ** float(yrange[0]), 10 ** float(yrange[1]))
        except (TypeError, ValueError):
            pass

    png_path = out_base + ".png"
    pdf_path = out_base + ".pdf"
    fig.savefig(png_path, dpi=EXPORT_DPI)
    fig.savefig(pdf_path)
    plt.close(fig)
    return png_path, pdf_path


# ----------------------------------------------------------------------
# Dash app
# ----------------------------------------------------------------------
def build_app(info, defaults, initial_cfg):
    app = Dash(__name__)
    app.title = "Decay Plot Explorer"

    app.layout = html.Div([
        html.Div([
            html.H3("Decay Plot Explorer", style={"margin": "0 0 6px 0"}),
            html.Span(f"File: {os.path.basename(info['filepath'])}",
                      style={"color": "#666", "fontSize": "12px"}),
        ], style={"padding": "8px 12px", "borderBottom": "1px solid #ddd"}),

        html.Div([
            # --- Left controls ---
            html.Div([
                html.H4("Traces"),
                dcc.Checklist(
                    id="vis-traces",
                    options=[
                        {"label": " Data",  "value": "raw"},
                        {"label": " Fit",   "value": "fit"},
                        {"label": " IRF",   "value": "irf"},
                        {"label": " Residuals", "value": "residual"},
                    ],
                    value=[v for v, on in
                           [("raw", initial_cfg["visible"].get("raw", True)),
                            ("fit", initial_cfg["visible"].get("fit", True)),
                            ("irf", initial_cfg["visible"].get("irf", False)),
                            ("residual", initial_cfg["visible"].get("residual", True))]
                           if on],
                    labelStyle={"display": "block", "margin": "4px 0"},
                ),

                html.H4("X axis", style={"marginTop": "14px"}),
                dcc.Checklist(
                    id="log-x",
                    options=[{"label": " Log X", "value": "log"}],
                    value=["log"] if initial_cfg.get("log_x") else [],
                    labelStyle={"display": "block"},
                ),
                html.Label("Time unit:", style={"marginTop": "8px", "display": "block"}),
                dcc.Dropdown(
                    id="time-unit",
                    options=[{"label": u, "value": u} for u in TIME_UNIT_CHOICES],
                    value=initial_cfg.get("time_unit", "auto"),
                    clearable=False, style={"width": "120px"},
                ),

                html.H4("Baseline offsets", style={"marginTop": "14px"}),
                html.Label("Data offset:"),
                dcc.Slider(id="off-raw", min=-20000, max=20000, step=100,
                           value=int(initial_cfg["offsets"].get("raw", 0)),
                           marks={-20000: "-20k", 0: "0", 20000: "20k"},
                           tooltip={"placement": "bottom"}),
                html.Label("Fit offset:", style={"marginTop": "6px"}),
                dcc.Slider(id="off-fit", min=-20000, max=20000, step=100,
                           value=int(initial_cfg["offsets"].get("fit", 0)),
                           marks={-20000: "-20k", 0: "0", 20000: "20k"},
                           tooltip={"placement": "bottom"}),
                html.Label("IRF offset:", style={"marginTop": "6px"}),
                dcc.Slider(id="off-irf", min=-20000, max=20000, step=100,
                           value=int(initial_cfg["offsets"].get("irf", 0)),
                           marks={-20000: "-20k", 0: "0", 20000: "20k"},
                           tooltip={"placement": "bottom"}),

                html.H4("Legend position", style={"marginTop": "14px"}),
                html.Label("X (0=left, 1=right):"),
                dcc.Slider(id="legend-x", min=0, max=1, step=0.01,
                           value=float(initial_cfg.get("legend_pos", {}).get("x", 0.97)),
                           marks={0: "0", 0.5: "0.5", 1: "1"},
                           tooltip={"placement": "bottom"}),
                html.Label("Y (0=bottom, 1=top):", style={"marginTop": "6px"}),
                dcc.Slider(id="legend-y", min=0, max=1, step=0.01,
                           value=float(initial_cfg.get("legend_pos", {}).get("y", 0.97)),
                           marks={0: "0", 0.5: "0.5", 1: "1"},
                           tooltip={"placement": "bottom"}),
                html.Div(style={"fontSize": "11px", "color": "#888",
                                "marginTop": "4px"},
                         children="Tip: you can also drag the legend box directly on the plot."),

                html.H4("Text box position", style={"marginTop": "14px"}),
                html.Label("X (0=left, 1=right):"),
                dcc.Slider(id="text-x", min=0, max=1, step=0.01,
                           value=float(initial_cfg.get("text_pos", {}).get("x", 0.05)),
                           marks={0: "0", 0.5: "0.5", 1: "1"},
                           tooltip={"placement": "bottom"}),
                html.Label("Y (0=bottom, 1=top):", style={"marginTop": "6px"}),
                dcc.Slider(id="text-y", min=0, max=1, step=0.01,
                           value=float(initial_cfg.get("text_pos", {}).get("y", 0.05)),
                           marks={0: "0", 0.5: "0.5", 1: "1"},
                           tooltip={"placement": "bottom"}),
                html.Div(style={"fontSize": "11px", "color": "#888",
                                "marginTop": "4px"},
                         children="Tip: you can also drag the tau/β text box directly on the plot."),

                html.H4("Annotation", style={"marginTop": "14px"}),
                html.Label("τ subscript:"),
                dcc.Input(id="tau-sub", type="text", value=defaults.get("tau_sub", "p"),
                          style={"width": "60px", "marginLeft": "6px"}),
                html.Br(),
                html.Label("λ_ex (nm):", style={"marginTop": "6px"}),
                dcc.Input(id="ex-lam", type="text", value=defaults.get("ex", ""),
                          style={"width": "80px", "marginLeft": "6px"}),
                html.Br(),
                html.Label("λ_emi (nm):", style={"marginTop": "6px"}),
                dcc.Input(id="emi-lam", type="text", value=defaults.get("emi", ""),
                          style={"width": "80px", "marginLeft": "6px"}),

                html.Div(style={"height": "20px"}),
                html.Button("Save publication PNG/PDF", id="export-btn",
                            n_clicks=0,
                            style={"padding": "8px 14px", "fontSize": "14px",
                                   "background": "#003399", "color": "white",
                                   "border": "none", "cursor": "pointer",
                                   "marginTop": "14px"}),
                html.Div(id="export-status",
                         style={"marginTop": "8px", "fontSize": "12px", "color": "#007700"}),
            ], style={
                "width": "260px", "float": "left", "padding": "12px",
                "borderRight": "1px solid #ddd", "height": "100vh",
                "overflowY": "auto", "boxSizing": "border-box",
            }),

            # --- Right: plot + hidden state holder ---
            html.Div([
                dcc.Graph(id="main-graph", config={
                    "displaylogo": False,
                    "toImageButtonOptions": {"format": "png", "filename": "decay_preview",
                                             "scale": 2},
                    "modeBarButtonsToAdd": ["drawline"],
                }, style={"width": "100%", "height": "100vh"}),
                dcc.Store(id="view-state", data=json.dumps(initial_cfg)),
            ], style={"marginLeft": "260px", "padding": "0", "height": "100vh"}),
        ]),
    ], style={"fontFamily": "Arial, sans-serif"})

    # --- Callbacks ---
    @app.callback(
        Output("view-state", "data"),
        Input("vis-traces", "value"),
        Input("log-x", "value"),
        Input("time-unit", "value"),
        Input("off-raw", "value"),
        Input("off-fit", "value"),
        Input("off-irf", "value"),
        Input("legend-x", "value"),
        Input("legend-y", "value"),
        Input("text-x", "value"),
        Input("text-y", "value"),
        Input("tau-sub", "value"),
        Input("ex-lam", "value"),
        Input("emi-lam", "value"),
        Input("main-graph", "relayoutData"),
        State("view-state", "data"),
    )
    def update_state(traces, log_x, time_unit, off_raw, off_fit, off_irf,
                     legend_x, legend_y, text_x, text_y,
                     tau_sub, ex, emi, relayout, state_json):
        cfg = json.loads(state_json) if state_json else {}
        cfg["visible"] = {
            "raw":      "raw" in (traces or []),
            "fit":      "fit" in (traces or []),
            "irf":      "irf" in (traces or []),
            "residual": "residual" in (traces or []),
        }
        cfg["log_x"] = bool(log_x)
        cfg["time_unit"] = time_unit or "auto"
        cfg["offsets"] = {"raw": float(off_raw or 0),
                          "fit": float(off_fit or 0),
                          "irf": float(off_irf or 0)}
        cfg["legend_pos"] = {"x": float(legend_x if legend_x is not None else 0.97),
                              "y": float(legend_y if legend_y is not None else 0.97)}
        cfg["text_pos"]   = {"x": float(text_x if text_x is not None else 0.05),
                              "y": float(text_y if text_y is not None else 0.05)}
        cfg["text_params"] = {
            "tau": defaults.get("tau", ""),
            "tau_unit": defaults.get("tau_unit", "ns"),
            "beta": defaults.get("beta", ""),
            "chi2": defaults.get("chi2", ""),
            "tau_sub": tau_sub or "",
            "ex": ex or "",
            "emi": emi or "",
        }

        # Capture zoom, legend drag, and annotation drag from relayoutData
        if relayout:
            # --- Legend drag: Plotly emits "legend.x" / "legend.y" ---
            if "legend.x" in relayout:
                try:
                    cfg["legend_pos"]["x"] = float(relayout["legend.x"])
                except (TypeError, ValueError):
                    pass
            if "legend.y" in relayout:
                try:
                    cfg["legend_pos"]["y"] = float(relayout["legend.y"])
                except (TypeError, ValueError):
                    pass

            # --- Annotation (tau/beta text box) drag ---
            # Plotly emits "annotations[i].x" / "annotations[i].y" (paper-fraction
            # when xref="paper"). The tau/beta text box is the SECOND annotation
            # added to the figure: index 0 is the "Weighted Residuals" label
            # inside the residual panel; index 1 is our draggable text box.
            for ax_key, ay_key in (("annotations[1].x", "annotations[1].y"),
                                   ("annotations[0].x", "annotations[0].y")):
                if ax_key in relayout:
                    try:
                        cfg["text_pos"]["x"] = float(relayout[ax_key])
                    except (TypeError, ValueError):
                        pass
                if ay_key in relayout:
                    try:
                        cfg["text_pos"]["y"] = float(relayout[ay_key])
                    except (TypeError, ValueError):
                        pass

            # --- X axis zoom ---
            xr = None
            for k in ("xaxis.range[0]", "xaxis2.range[0]"):
                if k in relayout and f"{k[:-2]}[1]" in relayout:
                    xr = [relayout[k], relayout[f"{k[:-2]}[1]"]]
                    break
            if xr is None and "xaxis.range" in relayout:
                xr = list(relayout["xaxis.range"])
            if xr is not None:
                if cfg["log_x"]:
                    try:
                        xr = [10 ** float(v) for v in xr]
                    except (TypeError, ValueError):
                        pass
                cfg["xrange"] = xr

            # --- Y axis zoom ---
            yr = None
            for k in ("yaxis.range[0]", "yaxis2.range[0]"):
                if k in relayout and f"{k[:-2]}[1]" in relayout:
                    yr = [relayout[k], relayout[f"{k[:-2]}[1]"]]
                    break
            if yr is None and "yaxis.range" in relayout:
                yr = list(relayout["yaxis.range"])
            if yr is not None:
                cfg["yrange"] = yr

        # Persist config to disk next to the .xlsx (auto-save on every change)
        save_config(info["filepath"], cfg)
        return json.dumps(cfg)

    @app.callback(
        Output("main-graph", "figure"),
        Input("view-state", "data"),
    )
    def render(state_json):
        cfg = json.loads(state_json) if state_json else {}
        fig, _, _, _ = build_plotly(info, cfg)
        return fig

    @app.callback(
        Output("export-status", "children"),
        Input("export-btn", "n_clicks"),
        State("view-state", "data"),
        prevent_initial_call=True,
    )
    def do_export(n, state_json):
        cfg = json.loads(state_json) if state_json else {}
        base_dir = os.path.dirname(os.path.abspath(info["filepath"]))
        stem = os.path.splitext(os.path.basename(info["filepath"]))[0]
        suffix = "_log" if cfg.get("log_x") else ""
        out_base = os.path.join(base_dir, f"{stem}_DecayResidual{suffix}")
        try:
            png, pdf = export_matplotlib(info, cfg, out_base)
        except Exception as e:
            return f"Export failed: {e}"
        return f"Saved:\n  {png}\n  {pdf}"

    return app


# ----------------------------------------------------------------------
# File picker (no CLI args)
# ----------------------------------------------------------------------
def pick_file_dialog():
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception:
        return None
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    path = filedialog.askopenfilename(
        title="Select Fit Results Excel",
        filetypes=[("Excel Files", "*.xlsx *.xls *.xlsm"), ("All Files", "*.*")],
    )
    try:
        root.destroy()
    except Exception:
        pass
    return path or None


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------
def main():
    args = [a for a in sys.argv[1:] if a != "--"]
    tau_sub_arg = None
    if args and not args[-1].lower().endswith((".xlsx", ".xls", ".xlsm")) \
            and os.path.sep not in args[-1] and "." not in args[-1]:
        tau_sub_arg = args.pop()
    filepath = " ".join(args) if args else pick_file_dialog()
    if not filepath:
        print("Usage: python decay_plot_interactive.py <fit_export.xlsx> [tau_sub]")
        print("  (or run with no arguments to pick the file via dialog)")
        sys.exit(1)
    if not os.path.isfile(filepath):
        print(f"File not found: {filepath}")
        sys.exit(1)

    info = load_fit_curve(filepath)
    info["filepath"] = filepath
    defaults = read_params_defaults(filepath)
    if tau_sub_arg:
        defaults["tau_sub"] = tau_sub_arg

    # Base defaults
    initial_cfg = {
        "visible":  {"raw": True, "fit": True, "irf": bool(info["irf_col"]), "residual": True},
        "offsets":  {"raw": 0.0, "fit": 0.0, "irf": 0.0},
        "colors":   {"raw": COLOR_DATA, "fit": COLOR_FIT, "irf": COLOR_IRF, "residual": COLOR_RES},
        "widths":   {"raw": 2.0, "fit": 2.5, "irf": 1.5, "residual": RESIDUAL_LINE_W},
        "log_x":    False,
        "time_unit": "auto",
        "xrange":   None,
        "yrange":   None,
        "legend_pos": {"x": 0.97, "y": 0.97},
        "text_pos":   {"x": 0.05, "y": 0.05},
        "text_params": {
            "tau": defaults.get("tau", ""),
            "tau_unit": defaults.get("tau_unit", "ns"),
            "beta": defaults.get("beta", ""),
            "chi2": defaults.get("chi2", ""),
            "tau_sub": defaults.get("tau_sub", "p"),
        },
    }

    # Override with saved config if present (per-dataset, beside the .xlsx)
    saved = load_config(filepath)
    if saved:
        for k in ("visible", "offsets", "legend_pos", "text_pos", "text_params"):
            if k in saved and isinstance(saved[k], dict):
                initial_cfg[k].update(saved[k])
        for k in ("log_x", "time_unit", "xrange", "yrange"):
            if k in saved:
                initial_cfg[k] = saved[k]
        print(f"[config] loaded saved plot config: {_config_path(filepath)}")
    else:
        print(f"[config] no saved config found; using defaults. "
              f"Settings will auto-save to {_config_path(filepath)}")

    app = build_app(info, defaults, initial_cfg)
    port = 8050
    print(f"\n>>> Opening interactive explorer on http://127.0.0.1:{port}")
    print("    Close this terminal when done.")
    webbrowser.open(f"http://127.0.0.1:{port}")
    app.run(debug=False, port=port, use_reloader=False)


if __name__ == "__main__":
    main()
