"""Interactive Tkinter + Matplotlib GUI for the simplified tail fit.

Workflow:
1. Run this script (optionally pass a decay CSV path as the first argument).
2. A matplotlib window shows the decay in semilog-y, with two buttons.
3. Click "Open Fit Menu" to set xmin/xmax, component taus/betas, fix flags.
4. Click "Run Fit (Enter)" to fit; the fit curve and residuals update live.
5. Click "Save Results (.xlsx)" to export the Excel workbook the plotter reads.

This is a stripped-down demo: only tail mode, no IRF, no PF/DF, no RISC.
"""

from __future__ import annotations

import os
import sys
import ctypes
import tkinter as tk
from tkinter import filedialog, messagebox

import numpy as np
import pandas as pd
import matplotlib
import matplotlib.pyplot as plt
from matplotlib.widgets import Button

import tail_fit
from read_data import read_decay_csv

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

# ----------------------------------------------------------------------
# Global plot style (kept simple; publication styling lives in decay_plot.py)
# ----------------------------------------------------------------------
plt.rcParams.update({
    "figure.dpi":      150,
    "font.size":       10,
    "axes.labelsize":  12,
    "axes.titlesize":  14,
    "xtick.labelsize": 10,
    "ytick.labelsize": 10,
    "lines.linewidth": 1.0,
    "legend.fontsize": 10,
})

# ----------------------------------------------------------------------
# Application state (mirrors the real engine's app_state, reduced)
# ----------------------------------------------------------------------
app_state = {
    "t": None, "data": None, "dt": None,
    "ax": None, "ax_res": None, "fig": None,
    "fit_line": None, "res_line": None,
    "vlines": [], "tk_root": None,
    "fit_results": None, "data_path": None,
}


# ----------------------------------------------------------------------
# Fit dialog (modeless-on-top popup with all the fit controls)
# ----------------------------------------------------------------------
def open_fit_dialog(_event=None):
    dialog = tk.Toplevel(app_state["tk_root"])
    dialog.title("Tail Fit Settings (Stretched Exponential)")
    dialog.geometry("640x720")
    dialog.attributes("-topmost", True)

    cb_font = ("Arial", 11, "bold")
    lbl_font = ("Arial", 10)

    t = app_state["t"]
    data = app_state["data"]

    # Auto xmin: a few points after the peak (typical tail start)
    peak_idx = int(np.argmax(data))
    start_idx = min(len(data) - 1, peak_idx + 5)
    auto_xmin = float(t[start_idx])
    auto_xmax = float(t.max() * 0.98)

    tk.Label(dialog, text="Fit Range (ns)", font=("Arial", 11, "bold")).grid(
        row=0, column=0, columnspan=6, pady=(10, 5))

    tk.Label(dialog, text="X Min:", font=lbl_font).grid(row=1, column=0, sticky="e", padx=5)
    entry_xmin = tk.Entry(dialog, width=12)
    entry_xmin.grid(row=1, column=1, pady=2, sticky="w")
    entry_xmin.insert(0, f"{auto_xmin:.2f}")

    tk.Label(dialog, text="X Max:", font=lbl_font).grid(row=2, column=0, sticky="e", padx=5)
    entry_xmax = tk.Entry(dialog, width=12)
    entry_xmax.grid(row=2, column=1, pady=2, sticky="w")
    entry_xmax.insert(0, f"{auto_xmax:.1f}")

    tk.Label(dialog, text="Step (ns):", font=lbl_font).grid(row=1, column=2, sticky="e", padx=5)
    entry_step = tk.Entry(dialog, width=8)
    entry_step.grid(row=1, column=3, pady=2, sticky="w")
    entry_step.insert(0, "1.0")

    tk.Label(dialog, text="Initial Guesses (leave a Tau blank to omit that component)",
             font=("Arial", 11, "bold")).grid(row=3, column=0, columnspan=6, pady=(15, 5))

    unit_options = ["ns", "us", "ms", "s"]
    unit_mult = {"ns": 1.0, "us": 1e3, "ms": 1e6, "s": 1e9}

    var_fix_t = [tk.BooleanVar(), tk.BooleanVar(), tk.BooleanVar(), tk.BooleanVar()]
    var_fix_b = [tk.BooleanVar(), tk.BooleanVar(), tk.BooleanVar(), tk.BooleanVar()]
    unit_vars = [tk.StringVar(), tk.StringVar(), tk.StringVar(), tk.StringVar()]
    unit_vars[0].set("ns"); unit_vars[1].set("us")
    unit_vars[2].set("us"); unit_vars[3].set("ms")

    default_taus = ["10", "", "", ""]
    default_betas = ["0.8", "0.8", "0.8", "1.0"]

    entry_taus, entry_betas = [], []
    for i in range(4):
        r = 4 + i
        tk.Label(dialog, text=f"Tau {i+1}:", font=lbl_font).grid(row=r, column=0, sticky="e", padx=5)
        e_t = tk.Entry(dialog, width=12)
        e_t.grid(row=r, column=1, pady=4)
        e_t.insert(0, default_taus[i])
        entry_taus.append(e_t)
        tk.OptionMenu(dialog, unit_vars[i], *unit_options).grid(row=r, column=2, padx=2, sticky="w")
        tk.Checkbutton(dialog, text="Fix", variable=var_fix_t[i], font=cb_font).grid(
            row=r, column=3, sticky="w", padx=(0, 10))

        tk.Label(dialog, text=f"Beta {i+1}:", font=lbl_font).grid(row=r, column=4, sticky="e", padx=5)
        e_b = tk.Entry(dialog, width=8)
        e_b.grid(row=r, column=5, pady=4)
        e_b.insert(0, default_betas[i])
        entry_betas.append(e_b)
        tk.Checkbutton(dialog, text="Fix", variable=var_fix_b[i], font=cb_font).grid(
            row=r, column=6, sticky="w")

    def execute_fit(_event=None):
        try:
            xmin = float(entry_xmin.get())
            xmax = float(entry_xmax.get())
        except ValueError:
            messagebox.showerror("Error", "xmin/xmax must be numeric.", parent=dialog)
            return

        taus, fixed_t, betas, fixed_b = [], [], [], []
        for i in range(4):
            val_t = entry_taus[i].get().strip()
            if not val_t:
                continue
            try:
                taus.append(float(val_t) * unit_mult[unit_vars[i].get()])
                betas.append(float(entry_betas[i].get().strip() or "0.8"))
                fixed_t.append(var_fix_t[i].get())
                fixed_b.append(var_fix_b[i].get())
            except ValueError:
                messagebox.showerror("Error", f"Bad numeric value in component {i+1}.", parent=dialog)
                return

        if not taus:
            messagebox.showerror("Error", "Provide at least one Tau guess.", parent=dialog)
            return

        num_exp = len(taus)
        try:
            result = tail_fit.run_tail_fit(
                app_state["t"], app_state["data"], xmin, xmax,
                taus, fixed_t, betas, fixed_b, num_exp,
            )
        except Exception as e:
            messagebox.showerror("Fit Error", str(e), parent=dialog)
            return

        app_state["fit_results"] = result
        update_plot(result, xmin, xmax)
        update_info_text(result)

    def step_xmin(direction):
        try:
            cur = float(entry_xmin.get())
            step = float(entry_step.get())
            entry_xmin.delete(0, tk.END)
            entry_xmin.insert(0, f"{cur + direction * step:.3f}")
            execute_fit()
        except ValueError:
            pass

    tk.Button(dialog, text="<", command=lambda: step_xmin(-1),
              font=("Arial", 10, "bold"), bg="lightgray").grid(row=1, column=4, padx=2)
    tk.Button(dialog, text=">", command=lambda: step_xmin(1),
              font=("Arial", 10, "bold"), bg="lightgray").grid(row=1, column=5, padx=2)

    tk.Button(dialog, text="Run Fit (Enter)", command=execute_fit,
              bg="lightblue", font=("Arial", 11, "bold")).grid(
        row=9, column=0, columnspan=7, pady=20, sticky="ew")
    dialog.bind("<Return>", execute_fit)
    entry_taus[0].focus_set()


# ----------------------------------------------------------------------
# Plot / info updates
# ----------------------------------------------------------------------
def update_plot(result, xmin, xmax):
    ax = app_state["ax"]
    ax_res = app_state["ax_res"]
    fig = app_state["fig"]

    t = app_state["t"]
    data = app_state["data"]
    fit_mask = (t >= xmin) & (t <= xmax)
    t_plot = t[fit_mask]

    # Reconstruct the full model so the fit line draws over the whole window
    # (fit_results.curve_df already has the masked values; recompute for full x)
    # but only the masked region was fit, so we draw only that to be honest.
    model_plot = result.curve_df["Fitted Data"].to_numpy()
    weighted_res = result.curve_df["Weighted Residuals"].to_numpy()

    if app_state["fit_line"] is not None:
        app_state["fit_line"].remove()
    app_state["fit_line"], = ax.plot(
        t_plot, model_plot, color="magenta", linewidth=1.5, label="Fit")

    if app_state["res_line"] is not None:
        app_state["res_line"].remove()
    app_state["res_line"], = ax_res.plot(
        t_plot, weighted_res, color="limegreen", linewidth=1, alpha=0.8)

    for v in app_state["vlines"]:
        v.remove()
    app_state["vlines"] = [
        ax.axvline(xmin, color="red", linewidth=1, alpha=0.8),
        ax.axvline(xmax, color="red", linewidth=1, alpha=0.8),
        ax_res.axvline(xmin, color="red", linewidth=1, alpha=0.8),
        ax_res.axvline(xmax, color="red", linewidth=1, alpha=0.8),
    ]

    x_margin = (xmax - xmin) * 0.05
    ax.set_xlim(xmin - x_margin, xmax + x_margin)
    y_data_win = data[fit_mask]
    if len(y_data_win) > 0:
        y_max = float(np.max(y_data_win))
        pos = y_data_win[y_data_win > 0]
        y_min = float(np.min(pos)) if pos.size else 1.0
        ax.set_ylim(max(0.5, y_min * 0.5), y_max * 1.5)

    max_res = float(np.max(np.abs(weighted_res))) if weighted_res.size else 1.0
    ax_res.set_ylim(-max(5, max_res * 1.2), max(5, max_res * 1.2))

    fig.canvas.draw_idle()


def update_info_text(result):
    ax_info = app_state.get("ax_info")
    if ax_info is None:
        return
    ax_info.clear()
    ax_info.axis("off")
    lines = [f"X2_R: {result.chi_sq:.3f}"]
    for i, c in enumerate(result.components):
        lines.append(f"C{i+1}: t={tail_fit.auto_format_time(c.tau)}, b={c.beta:.2f}, %={c.rel_percent:.1f}")
        lines.append(f"  num_ave={tail_fit.auto_format_time(c.num_ave)}")
        lines.append(f"  int_ave={tail_fit.auto_format_time(c.int_ave)}")
    ax_info.text(0.0, 1.0, "\n".join(lines),
                 transform=ax_info.transAxes,
                 fontsize=9, verticalalignment="top",
                 family="monospace")
    app_state["fig"].canvas.draw_idle()


# ----------------------------------------------------------------------
# Save results to Excel (same column layout the real engine produces,
# so decay_plot.py can consume it)
# ----------------------------------------------------------------------
def save_results(_event=None):
    result = app_state.get("fit_results")
    if result is None:
        messagebox.showwarning("Warning", "No fit results. Run a fit first.")
        return

    path = filedialog.asksaveasfilename(
        title="Save Fit Results",
        defaultextension=".xlsx",
        filetypes=[("Excel Files", "*.xlsx"), ("All Files", "*.*")],
    )
    if not path:
        return

    try:
        t_full = app_state["t"]
        data_full = app_state["data"]

        # Normalize raw decay to 15000 counts peak (same convention as real engine)
        data_peak = float(np.max(data_full))
        norm_factor = 15000.0 / data_peak if data_peak > 0 else 1.0
        plot_counts = data_full * norm_factor

        df_full = pd.DataFrame({
            "Full_Time (ns)":  t_full,
            "Raw_Counts":      data_full,
            "Plot_Counts":     plot_counts,
        })

        df_fit = result.curve_df.copy()
        # Apply the same normalization to the fitted curve so it overlays correctly
        if "Fitted Data" in df_fit.columns:
            df_fit["Plot_Fitted Data"] = df_fit["Fitted Data"] * norm_factor
        df_fit.columns = [f"Fit_{c}" if c != "Time (ns)" else "Fit_Time (ns)" for c in df_fit.columns]
        df_fit = df_fit.reset_index(drop=True)
        df_export = pd.merge(
            df_full, df_fit,
            left_on="Full_Time (ns)", right_on="Fit_Time (ns)", how="left",
        )

        with pd.ExcelWriter(path) as writer:
            params_df = result.params_df.copy()
            params_df.to_excel(writer, sheet_name="Parameters", index=False)
            df_export.to_excel(writer, sheet_name="Fit_Curve", index=False)

        base, _ = os.path.splitext(path)
        app_state["fig"].savefig(base + ".png", dpi=400, bbox_inches="tight")
        messagebox.showinfo("Success",
            f"Excel saved:\n{path}\n\nPreview PNG saved beside it.\n\n"
            f"Now run:\n  python decay_plot.py \"{path}\"")
    except Exception as e:
        messagebox.showerror("Save Error", f"Failed to save:\n{e}")


# ----------------------------------------------------------------------
# Data loading and main figure setup
# ----------------------------------------------------------------------
def run_data_loading(data_path):
    if not data_path:
        data_path = filedialog.askopenfilename(
            title="Select Decay Data (CSV)",
            filetypes=[("CSV Files", "*.csv"), ("All Files", "*.*")],
        )
    if not data_path:
        return

    data_path = os.path.abspath(data_path)
    app_state["data_path"] = data_path

    df = read_decay_csv(data_path)
    if df is None:
        messagebox.showerror("Read Error", f"Cannot read:\n{os.path.basename(data_path)}")
        return

    t = df["Time"].to_numpy(dtype=float)
    data = df["Counts"].to_numpy(dtype=float)
    dt = float(np.median(np.diff(t)))

    app_state["t"] = t
    app_state["data"] = data
    app_state["dt"] = dt

    # Build the figure: left info column + main decay + residual panel
    plt.style.use("dark_background")
    fig = plt.figure(figsize=(10, 7))
    gs = fig.add_gridspec(2, 2, width_ratios=[1, 3], height_ratios=[3, 1],
                          hspace=0.05, wspace=0.05)
    ax_info = fig.add_subplot(gs[:, 0])
    ax      = fig.add_subplot(gs[0, 1])
    ax_res  = fig.add_subplot(gs[1, 1], sharex=ax)

    ax_info.axis("off")

    ax.semilogy(t, data, color="cyan", linewidth=1, alpha=0.6, label="Decay Data")
    ax.set_ylabel("Intensity (Counts)")
    ax.set_title(f"Tail Fitting: {os.path.basename(data_path)}")
    ax.grid(True, which="both", linestyle="solid", color="gray", alpha=0.3)
    ax.tick_params(labelbottom=False)
    ax.legend(loc="upper right")

    ax_res.axhline(0, color="gray", linestyle="--")
    ax_res.set_xlabel("Time (ns)")
    ax_res.set_ylabel("Residuals")
    ax_res.grid(True, which="both", linestyle="solid", color="gray", alpha=0.3)

    ax.set_xlim(t.min(), t.max())
    valid = data[data > 0]
    if valid.size:
        ax.set_ylim(float(np.min(valid)) * 0.5, float(np.max(data)) * 2.0)

    app_state["fig"] = fig
    app_state["ax"] = ax
    app_state["ax_res"] = ax_res
    app_state["ax_info"] = ax_info

    # Buttons at the bottom
    ax_btn_fit  = plt.axes([0.30, 0.02, 0.18, 0.045])
    ax_btn_save = plt.axes([0.55, 0.02, 0.18, 0.045])
    btn_fit  = Button(ax_btn_fit,  "Open Fit Menu",
                      color=(1, 1, 1, 0.35), hovercolor=(1, 1, 1, 0.35))
    btn_save = Button(ax_btn_save, "Save Results (.xlsx)",
                      color=(1, 1, 1, 0.35), hovercolor=(1, 1, 1, 0.35))
    btn_fit.label.set_color("black")
    btn_save.label.set_color("black")
    btn_fit.on_clicked(open_fit_dialog)
    btn_save.on_clicked(save_results)
    app_state["btn_fit"] = btn_fit
    app_state["btn_save"] = btn_save

    print("\nPlot generated. Click 'Open Fit Menu' at the bottom to start fitting.")
    plt.show()


def main():
    arg_path = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1].lower().endswith(".csv") else None

    root = tk.Tk()
    root.withdraw()
    app_state["tk_root"] = root
    root.after(100, lambda: run_data_loading(arg_path))
    try:
        root.mainloop()
    finally:
        pass


if __name__ == "__main__":
    main()
