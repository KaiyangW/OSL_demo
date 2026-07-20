# OSL_demo

A minimal, **publicly available** demonstration of the TCSPC tail-fitting +
publication-plotting workflow used in my organic-semiconductor-laser (OSL)
photophysics research. It is intentionally **stripped down** — it contains
only the tail-fit path with no reconvolution, no PF/DF area analysis, no
RISC kinetic-rate calculation, no multi-start, and no uncertainty
propagation. Those features live in the private research codebase.

The goal of this repository is to let colleagues experience the
"raw decay CSV → fit → publication-grade figure" pipeline end-to-end
without giving away the full research tool. If you want the full engine
for your own work, please get in touch (see `Contact` below).

---

## What the demo does

```
 raw decay CSV
      |
      v
 fit_gui.py            interactive Tk + Matplotlib window
      |                 - semilog-y view of the decay
      |                 - fit-menu dialog: xmin/xmax, tau/beta, fix flags
      |                 - Poisson-weighted least-squares tail fit
      |                 - live fit curve + weighted-residual panel
      v
 *.xlsx                one workbook:
      |                   - Parameters sheet  (tau, beta, num_ave, int_ave,
      |                                        amplitudes, areas, chi^2)
      |                   - Fit_Curve sheet    (time, counts, fit, residuals,
      |                                        normalized plot columns)
      v
 decay_plot.py         publication-style matplotlib figure
      |                 - top panel: decay + fit, semilog y
      |                 - bottom panel: weighted residuals, shared x
      |                 - annotation box: tau, beta, chi^2
      v
 *_DecayResidual.png   600 dpi PNG (+ PDF) next to the .xlsx
 *_DecayResidual.pdf
```

---

## Mathematical model

For time `t >= xmin` (the tail start), the model is a sum of
stretched exponentials on top of a constant background:

```
model(t) = bkg + sum_i  B_i * exp( -( (t - xmin) / tau_i )^beta_i )
```

For `t < xmin`, the model returns the background only.

Residuals are Poisson-weighted:

```
r_i = (model_i - data_i) / sqrt(max(data_i, 1))
```

Optimisation: `scipy.optimize.least_squares` with `soft_l1` robust loss
and box constraints. `tau` and `beta` can be fixed by giving an
epsilon-tight bound.

Per-component lifetimes reported in the export:

| quantity  | formula                                       | meaning                |
|-----------|-----------------------------------------------|------------------------|
| `num_ave` | `(tau/beta) * Gamma(1/beta)`                  | 1st moment `<t>`       |
| `int_ave` | `tau * Gamma(2/beta) / Gamma(1/beta)`         | intensity-averaged tau |
| `area`    | `B * num_ave`                                 | component area         |

Goodness of fit: reduced chi-squared

```
chi^2 = sum( (data - model)^2 / max(data, 1) ) / (N - P)
```

---

## Quick start

1. **Install dependencies** (Python 3.9+):

   ```bash
   pip install -r requirements.txt
   ```

2. **Put a decay CSV in `data/`** (two columns: Time, Counts). Header and
   metadata lines are auto-skipped. If you don't have your own data,
   contact me and I will share a sample decay.

3. **Run the fit GUI**:

   ```bash
   python fit_gui.py data/your_decay.csv
   ```

   - Click `Open Fit Menu` at the bottom of the plot window.
   - Set `X Min` / `X Max`, enter `Tau` / `Beta` guesses (leave the
     component blank to omit it; up to 4 components).
   - Click `Run Fit (Enter)` (or press Enter).
   - Click `Save Results (.xlsx)` to export the workbook.

4. **Make the publication figure**:

   There are two plotters, pick the one you want:

   **Quick static export** (no interaction, single command, 600 dpi PNG + PDF):

   ```bash
   python decay_plot.py path/to/your_saved.xlsx
   ```

   Files written beside the `.xlsx`:

   - `your_saved_DecayResidual.png` (600 dpi)
   - `your_saved_DecayResidual.pdf`

   Optional second argument sets the tau subscript label (`p`, `d`,
   `pho`, ...) shown in the annotation box:

   ```bash
   python decay_plot.py your_saved.xlsx p
   ```

   **Interactive Plotly explorer** (browser-based, then 600 dpi export):

   ```bash
   pip install plotly dash   # extra deps for this mode
   python decay_plot_interactive.py path/to/your_saved.xlsx
   ```

   A browser window opens at `http://127.0.0.1:8050` with:

   - Data / Fit / IRF / Residual trace toggles
   - Log-x toggle, time-unit selector (ns/µs/ms/s/auto)
   - **Per-trace baseline offset sliders** (Data / Fit / IRF) — useful when
     comparing curves that overlap visually
   - Annotation box: tau, beta, chi² (read from the Parameters sheet), plus
     editable tau subscript and λ_ex / λ_emi fields
   - "Save publication PNG/PDF" button — re-renders the current view
     (including offsets, log-x, and zoom) as a 600 dpi Matplotlib figure
     next to the `.xlsx` file.

   No EMF export is included in the demo.

---

## Repository layout

```
OSL_demo/
├── README.md
├── requirements.txt
├── read_data.py                  # minimal CSV reader (Time, Counts)
├── tail_fit.py                   # simplified tail-fit engine (single least_squares)
├── fit_gui.py                    # Tk + Matplotlib interactive GUI (tail only)
├── decay_plot.py                 # matplotlib publication plotter (static, no baseline shift)
├── decay_plot_interactive.py     # Plotly + Dash interactive plotter (with baseline offsets)
├── data/                         # place your decay CSV here
└── screenshots/                  # GUI/figure screenshots (add your own)
```

---

## What is NOT included (and why)

The full private research tool adds, on top of this demo:

- **IRF reconvolution fitting** (FFT-based, with shift and scatter terms)
- **Multi-start parallel optimization** for global-minimum robustness
- **Poisson-covariance parameter uncertainties** with delta-method
  propagation to derived lifetimes
- **PF / DF area-difference analysis** (prompt vs delayed fluorescence
  separation, with optional scatter subtraction and extrapolation)
- **RISC / ISC kinetic-rate calculator** bridge (Excel + Python exact
  analytic solver for TADF rate constants)
- **Plotly/Dash interactive explorer** with baseline-shift, log-x toggle,
  time-unit selector, and 600 dpi publication export from the current view
  (this is `decay_plot_interactive.py` in the demo, included)
- **EMF export** for editable Word/PowerPoint figures

These are intentionally withheld from the public demo. If you need any of
them for a collaboration or replication, please ask.

---

## Contact

**Source code of the full engine is available on reasonable request.**

- Author: Kaiyang Wei
- Email: weikaiyang.kw@gmail.com

---

## License

This demo code is released under the **MIT License** (see `LICENSE`).
You are free to run, study, and adapt it for academic use.

The full private research engine is **not** covered by this license and
is not distributed from this repository. Please contact the author for
access terms.
