"""Publication-style plotter for the demo's TCSPC fit exports.

Reads the Excel workbook produced by ``fit_gui.py`` (sheets ``Parameters``
and ``Fit_Curve``) and produces a two-panel figure:

  - top:    decay + fit on a semilog-y axis
  - bottom: weighted residuals, shared x-axis

The figure is saved as 600 dpi PNG and PDF next to the input file.

This is a simplified version of the private research plotter. It removes
the interactive Plotly/Dash explorer and the baseline-shift (offset)
controls; it keeps the journal-style tick/axis settings and the tau/beta
annotation box.
"""

from __future__ import annotations

import math
import os
import re
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np
import pandas as pd

# ----------------------------------------------------------------------
# Style constants (replicate the private PlotUtils export settings)
# ----------------------------------------------------------------------
FONT_FAMILY      = "Arial"
FONT_SIZE        = 12
EXPORT_DPI       = 600
EXPORT_WIDTH_PX  = 670
DECAY_DATA_H_PX  = 600
RESIDUAL_DATA_H  = 170
PANEL_GAP_PX     = 20
MARGIN_LEFT_PX   = 90
MARGIN_RIGHT_PX  = 40
MARGIN_TOP_PX    = 40
MARGIN_BOTTOM_PX = 90
TICK_WIDTH       = 1.0
MAJOR_TICK_LEN   = 8
MINOR_TICK_LEN   = 4
AXES_LINEWIDTH   = 1.0
RESIDUAL_LABEL_COLOR = "#8B0000"
RESIDUAL_LINE_WIDTH  = 0.7

# Colour palette for the demo (matches the private tool defaults)
COLOR_DATA = "#003399"
COLOR_FIT  = "#CC0000"
COLOR_RES  = "#000000"

TIME_UNIT_DIVISORS = {"ns": 1, "us": 1e3, "ms": 1e6, "s": 1e9}


def setup_style():
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


# ----------------------------------------------------------------------
# Parameter sheet reading (tau/beta/chi2 defaults)
# ----------------------------------------------------------------------
def _parse_formatted_time(text):
    text = str(text or "").strip()
    if not text:
        return "", ""
    m = re.match(r"^([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)\s*(\S+)$", text)
    if not m:
        return text, ""
    aliases = {"us": "us", "us": "us"}
    unit = m.group(2)
    return m.group(1), unit


def read_params_defaults(filepath):
    defaults = {"chi2": "", "tau": "", "tau_unit": "ns",
                "beta": "", "tau_sub": ""}
    try:
        df = pd.read_excel(filepath, sheet_name="Parameters")
    except Exception:
        return defaults
    if df is None or df.empty:
        return defaults

    param_col = value_col = fmt_col = None
    for c in df.columns:
        cl = str(c).strip().lower()
        if cl in ("parameter", "param"):
            param_col = c
        elif cl == "value":
            value_col = c
        elif cl in ("formatted_value", "formatted value"):
            fmt_col = c
    if param_col is None or value_col is None:
        if len(df.columns) < 2:
            return defaults
        param_col, value_col = df.columns[0], df.columns[1]

    lookup = {}
    for _, row in df.iterrows():
        pname = str(row[param_col]).strip()
        val = row[value_col]
        fmt = row[fmt_col] if fmt_col is not None and fmt_col in row.index else None
        if pd.notna(val) or (fmt is not None and pd.notna(fmt) and str(fmt).strip()):
            lookup[pname] = {"value": val, "formatted": fmt}

    def fmt_scalar(v):
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return ""
        if isinstance(v, float):
            if v == int(v):
                return str(int(v))
            return f"{v:.6g}"
        return str(v).strip()

    chi2 = lookup.get("Reduced Chi-Squared")
    if chi2:
        defaults["chi2"] = fmt_scalar(chi2["value"])

    # Prefer Int_Ave_Tau_2 if a two-component fit, else Int_Ave_Tau_1
    for key in ("Int_Ave_Tau_2 (ns)", "Int_Ave_Tau_1 (ns)"):
        entry = lookup.get(key)
        if not entry:
            continue
        fmt = entry.get("formatted")
        if fmt is not None and pd.notna(fmt) and str(fmt).strip():
            val, unit = _parse_formatted_time(fmt)
            if val:
                defaults["tau"] = val
                defaults["tau_unit"] = unit or "ns"
                break
        val = entry.get("value")
        if val is not None and not (isinstance(val, float) and pd.isna(val)):
            defaults["tau"] = fmt_scalar(val)
            defaults["tau_unit"] = "ns"
            break

    # Matching beta
    for key in ("Beta_Stretching_2", "Beta_Stretching_1"):
        entry = lookup.get(key)
        if entry:
            defaults["beta"] = fmt_scalar(entry["value"])
            break

    return defaults


# ----------------------------------------------------------------------
# Data loading
# ----------------------------------------------------------------------
def load_fit_curve(filepath):
    df = pd.read_excel(filepath, sheet_name="Fit_Curve")
    if "Full_Time (ns)" not in df.columns:
        raise ValueError("Missing Full_Time (ns) column in Fit_Curve sheet.")

    y_cols = [c for c in df.columns
              if c not in ("Full_Time (ns)", "Fit_Time (ns)")
              and pd.api.types.is_numeric_dtype(df[c])]
    data_col = next((c for c in y_cols if c in ("Plot_Counts", "Raw_Counts")), None)
    fit_col  = next((c for c in y_cols if c in ("Fit_Plot_Fitted Data", "Fit_Fitted Data")), None)
    res_col  = "Fit_Weighted Residuals" if "Fit_Weighted Residuals" in df.columns else None
    fit_time_col = "Fit_Time (ns)" if "Fit_Time (ns)" in df.columns else "Full_Time (ns)"

    if data_col is None or fit_col is None:
        raise ValueError("Fit_Curve sheet missing decay/fit columns.")

    return {
        "df": df,
        "time_col": "Full_Time (ns)",
        "fit_time_col": fit_time_col,
        "data_col": data_col,
        "fit_col":  fit_col,
        "res_col":  res_col,
        "t_max_ns": float(df["Full_Time (ns)"].max()),
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
# Annotation text
# ----------------------------------------------------------------------
def annotation_latex(params):
    lines = []
    tau  = params.get("tau", "")
    unit = params.get("tau_unit", "ns")
    beta = params.get("beta", "")
    chi2 = params.get("chi2", "")
    sub  = params.get("tau_sub", "")
    if tau:
        sub_latex = r"_{\mathrm{" + sub + r"}} " if sub else " "
        unit_latex = {"ns": r"\ \mathrm{ns}", "us": r"\ \mathrm{us}",
                      "ms": r"\ \mathrm{ms}", "s": r"\ \mathrm{s}"}.get(unit, r"\ \mathrm{ns}")
        lines.append(rf"$\tau{sub_latex}= {tau}{unit_latex}$")
    if beta:
        lines.append(rf"$\beta = {beta}$")
    if chi2:
        lines.append(rf"$\chi^2 = {chi2}$")
    return "\n".join(lines)


# ----------------------------------------------------------------------
# Plotting
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


def plot_publication(filepath, out_dir=None, tau_sub="p",
                     ex_lambda="", emi_lambda=""):
    """Produce the publication PNG + PDF next to the input Excel file."""
    setup_style()

    info = load_fit_curve(filepath)
    df = info["df"]
    t_max_ns = info["t_max_ns"]
    divisor, unit = resolve_time_unit(t_max_ns)

    params = read_params_defaults(filepath)
    if tau_sub:
        params["tau_sub"] = tau_sub
    if ex_lambda:
        params["ex"] = ex_lambda
    if emi_lambda:
        params["emi"] = emi_lambda

    # ----- Figure layout: top decay (semilog y), bottom residuals (shared x) -----
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

    # ----- Decay panel -----
    t_full = df[info["time_col"]].to_numpy()
    y_data = df[info["data_col"]].to_numpy()
    ax_decay.plot(t_full / divisor, y_data,
                  color=COLOR_DATA, linewidth=2.0, label="Data")

    fit_time = df[info["fit_time_col"]].to_numpy()
    fit_y    = df[info["fit_col"]].to_numpy()
    # Only plot the fit where it exists (the fit window); NaNs elsewhere
    fit_mask = ~np.isnan(fit_y)
    ax_decay.plot(fit_time[fit_mask] / divisor, fit_y[fit_mask],
                  color=COLOR_FIT, linewidth=2.5, label="Fit")

    ax_decay.set_yscale("log")
    ax_decay.set_ylabel("Counts", fontsize=FONT_SIZE)
    ax_decay.yaxis.set_major_formatter(ticker.LogFormatterMathtext())
    ax_decay.tick_params(axis="x", which="both", labelbottom=False)
    # Top/right spines visible, no ticks on top/right
    for spine in ax_decay.spines.values():
        spine.set_visible(True)
        spine.set_linewidth(AXES_LINEWIDTH)
    ax_decay.tick_params(axis="x", which="both", direction="in", top=False)
    ax_decay.tick_params(axis="y", which="both", direction="in", right=False)
    ax_decay.tick_params(which="major", length=MAJOR_TICK_LEN)
    ax_decay.tick_params(which="minor", length=MINOR_TICK_LEN)

    leg = ax_decay.legend(loc="upper right", handlelength=1.8, handletextpad=0.5)
    for text, line in zip(leg.get_texts(), leg.get_lines()):
        text.set_color(line.get_color())

    # Annotation box (tau, beta, chi2)
    ann = annotation_latex(params)
    if ann:
        ax_decay.text(0.05, 0.05, ann, transform=ax_decay.transAxes,
                      fontsize=FONT_SIZE, verticalalignment="top",
                      horizontalalignment="left")

    # ----- Residual panel -----
    if info["res_col"]:
        res_y = df[info["res_col"]].to_numpy()
        res_mask = ~np.isnan(res_y)
        ax_res.plot(fit_time[res_mask] / divisor, res_y[res_mask],
                    color=COLOR_RES, linewidth=RESIDUAL_LINE_WIDTH)
        max_abs = float(np.nanmax(np.abs(res_y))) if res_mask.any() else 1.0
        ylimit = nice_residual_limit(max_abs * 1.03)
    else:
        ylimit = 1.0
    ax_res.axhline(0, color="#aaaaaa", linewidth=1, linestyle="--")
    ax_res.set_xlabel(f"Time ({unit})", fontsize=FONT_SIZE)
    ax_res.set_ylabel("")
    ax_res.set_ylim(-ylimit, ylimit)
    y_ticks = [-ylimit, 0.0, ylimit] if ylimit > 0 else [-1.0, 0.0, 1.0]
    ax_res.yaxis.set_major_locator(ticker.FixedLocator(y_ticks))
    ax_res.yaxis.set_major_formatter(
        ticker.FuncFormatter(lambda v, _: format_residual_tick(v)))
    ax_res.yaxis.set_minor_locator(ticker.NullLocator())
    ax_res.xaxis.set_major_formatter(ticker.FormatStrFormatter("%.0f"))
    ax_res.tick_params(axis="x", which="both", direction="in", top=False,
                       labelsize=FONT_SIZE, width=TICK_WIDTH)
    ax_res.tick_params(axis="y", which="both", direction="in", right=False,
                       labelsize=FONT_SIZE, width=TICK_WIDTH)
    ax_res.tick_params(which="major", length=MAJOR_TICK_LEN)
    for spine in ax_res.spines.values():
        spine.set_visible(True)
        spine.set_linewidth(AXES_LINEWIDTH)
    ax_res.text(0.97, 0.97, "Weighted Residuals",
                transform=ax_res.transAxes, fontsize=FONT_SIZE,
                color=RESIDUAL_LABEL_COLOR,
                verticalalignment="top", horizontalalignment="right")

    # X limits: span full data range
    ax_decay.set_xlim(0, t_max_ns / divisor * 1.02)

    # ----- Save -----
    base_dir = out_dir or os.path.dirname(os.path.abspath(filepath))
    stem = os.path.splitext(os.path.basename(filepath))[0]
    png_path = os.path.join(base_dir, f"{stem}_DecayResidual.png")
    pdf_path = os.path.join(base_dir, f"{stem}_DecayResidual.pdf")
    fig.savefig(png_path, dpi=EXPORT_DPI, bbox_inches=None)
    fig.savefig(pdf_path, bbox_inches=None)
    plt.close(fig)
    print(f"Saved:\n  {png_path}\n  {pdf_path}")
    return png_path, pdf_path


# ----------------------------------------------------------------------
# CLI entry
# ----------------------------------------------------------------------
def main():
    if len(sys.argv) < 2:
        print("Usage: python decay_plot.py <fit_export.xlsx> [tau_sub]")
        print("  e.g. python decay_plot.py decay_fit.xlsx p")
        sys.exit(1)
    filepath = sys.argv[1]
    tau_sub = sys.argv[2] if len(sys.argv) > 2 else "p"
    if not os.path.isfile(filepath):
        print(f"File not found: {filepath}")
        sys.exit(1)
    plot_publication(filepath, tau_sub=tau_sub)


if __name__ == "__main__":
    main()
