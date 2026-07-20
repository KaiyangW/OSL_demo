# OSL_demo

A **public demo** of the automated organic-semiconductor-laser (OSL)
photophysics data-processing workflow I developed during my PhD. It exposes
one end-to-end pipeline — **raw TCSPC decay CSV → interactive fit →
publication-grade figure** — so colleagues can experience the look and feel
of the full private research codebase without it being freely downloadable.

If you want the full engine for your own work, please get in touch (see
`Contact` below) — the source is available on reasonable request.

---

## About the full private codebase

The full codebase (private repository `Python_OSL`) is a modular, automated
data-processing suite covering every measurement modality used in my
organic-semiconductor-laser research. Each modality has its own folder with
a dedicated GUI / batch processor, and all of them feed into a shared
publication-plotting layer. Raw data ingestion is unified through a single
shared reader module, so instrument-specific quirks (encodings, header
lines, delimiters, binary formats) are handled once and reused everywhere.

### Workflow map (by measurement type)

```
                    ┌─────────────────────────────────────────┐
                    │  Read_data_unified.py (shared reader)   │
                    │  read_xy / read_grid / read_table /      │
                    │  read_workbook / read_folder / read_auto │
                    └─────────────┬───────────────────────────┘
                                  │
   ┌──────────────────────────────┼──────────────────────────────┐
   │                              │                              │
   ▼                              ▼                              ▼
 ASE threshold               DFB laser threshold           TRPL / TCSPC
 ─────────────               ───────────────────           ─────────────
 raw ASE spectra             raw laser spectra             Edinburgh .FL/.FS
 → PL-background removal     → beam-area calc              → binary→CSV
 → auto piecewise threshold  → auto bilinear threshold      → reconvolution or
 → manual fit override       → manual bilinear override        tail fit
 → batch summary             → manual+auto merged              (stretched exp)
 → ASE_graph.py              → Laser_graph.py                → PF/DF areas,
                                                                RISC rates
                                                              → Decay_graph.py

   ┌──────────────────────────────┬──────────────────────────────┐
   │                              │                              │
   ▼                              ▼                              ▼
 Andor gated iCCD            KIT transient abs             TRPL / PL (steady)
 ────────────────            ──────────────────           ───────────────────
 raw .asc (gate-encoded)     raw .mat (LabVIEW)           RT / 77 K PL spectra
 → gate-width / accum norm   → .mat → CSV grid             → Voigt fitting
 → normalized spectra batch  → interactive viewer          → gamma-sensitivity
 → semilogy decay            → baseline correction         → onset detection
 → Andor2dev.py:             → spectra + kinetics          → PL_onset_runner.py
   heatmaps, kinetics,         → TA_graphs.py                 → Voigt_PL.py
   multi-exp lifetime fits

                              ─────────────────────────────────────
                                          ▼
                              Python plot/ (shared publication layer)
                              ─────────────────────────────────────
                              PlotUtils.py + MatplotlibExport.py
                              → 600 dpi PNG / PDF / EMF
                              → Plotly + Dash interactive explorer
                              → Join_curves.py: multi-curve overlay
                                from a folder manifest
                              → Multi_column_graph.py: error-bar plots
                                from summary tables (PLQY etc.)
                              → Per-dataset JSON config persistence
```

### What the full engine adds on top of this demo

| Capability | Where it lives | What it does |
|---|---|---|
| **IRF reconvolution fitting** | `Recon_fit_process.py` | FFT-based convolution of the model with the measured instrument response function, with shift and scatter terms |
| **Multi-start parallel optimisation** | `fit_multistart.py` | Many tau/beta seed combinations run in parallel; returns the global best fit by reduced chi-squared |
| **Parameter uncertainties** | `fit_uncertainty.py` | Poisson-covariance matrix from the residual Jacobian; delta-method propagation to derived lifetimes |
| **PF / DF area-difference analysis** | `Area_Analysis_Engine.py` | Prompt vs delayed fluorescence separation with optional scatter subtraction and extrapolation compensation |
| **RISC / ISC kinetic-rate calculator** | `risc_calculator_bridge.py`, `RISC_exact_solution_coremath.py` | Exact analytic TADF rate constants from fitted lifetimes and PLQY |
| **Edinburgh binary → CSV converter** | `TRPL/Edin_Ins_file_converter.py` | Reads `.FL` / `.FS` binary exports directly |
| **Plotly + Dash multi-curve explorer** | `Python plot/Join_curves.py`, `PlotUtils.py` | Folder-tree manifest → multi-curve overlay with persistent per-dataset JSON config, EMF export, baseline shift, log-x toggle |
| **Other modalities** | `ASE/`, `DFB devices/`, `Andor_gated_iCCD data/`, `KIT Transient Absorption/`, `TRPL/PL/` | ASE threshold, DFB laser threshold, gated iCCD TRPES, transient absorption, steady-state PL Voigt fitting & onset detection |

These are intentionally withheld from the public demo. If you need any of
them for a collaboration or replication, please ask.

---

## What this demo does

This repository exposes the **TRPL / TCSPC tail-fit → publication plot**
pipeline only. It is intentionally simplified — no reconvolution, no
multi-start, no PF/DF, no RISC, no uncertainty propagation. The math is the
same family of methods; the heavy infrastructure is removed.

```
 raw decay CSV
      │
      ▼
 fit_gui.py              interactive Tk + Matplotlib window
      │                    - semilog-y view of the decay
      │                    - fit-menu dialog: xmin/xmax, tau/beta, fix flags
      │                    - Poisson-weighted least-squares tail fit
      │                    - live fit curve + weighted-residual panel
      │                    - up to 4 stretched-exponential components
      ▼
 *.xlsx                  one workbook:
      │                    - Parameters sheet  (tau, beta, num_ave, int_ave,
      │                                         amplitudes, areas, chi²)
      │                    - Fit_Curve sheet    (time, counts, fit, residuals,
      │                                         normalized plot columns)
      ▼
 decay_graph_demo.py     interactive Plotly + Dash explorer in the browser
      │                    - Data / Fit / IRF / Residual trace toggles
      │                    - log-x toggle, time-unit selector (ns/µs/ms/s)
      │                    - per-trace baseline offset sliders
      │                    - **draggable legend and tau/β annotation box**
      │                    - editable τ subscript and λ_ex / λ_emi fields
      │                    - "Save publication PNG/PDF" button re-renders
      │                      the current view as a 600 dpi Matplotlib figure
      │                    - **plot config auto-saves to JSON** beside the
      │                      .xlsx and reloads on next open
      ▼
 *_DecayResidual.png     600 dpi publication figure
 *_DecayResidual.pdf     (+ PDF) next to the .xlsx
```

The interactive plotter is the same architecture as the private research
tool's `Decay_graph.py` — just trimmed of multi-file overlay, EMF export,
and a few custom features. The static 600 dpi export logic mirrors the
private `PlotUtils.py` / `MatplotlibExport.py` publication settings.

---

## Quick start

1. **Install dependencies** (Python 3.9+):

   ```bash
   pip install -r requirements.txt
   ```

2. **Use the bundled demo data** (`demo_data_4CzIPN_mCP_decay.csv`, a
   4CzIPN-in-mCP TCSPC decay) or put your own two-column (Time, Counts)
   CSV in the repository root or `data/`. The reader auto-skips
   FluOracle-style metadata headers and trailing empty columns.

3. **Run the fit GUI**:

   ```bash
   python fit_gui.py demo_data_4CzIPN_mCP_decay.csv
   ```

   - Click `Open Fit Menu` at the bottom of the plot window.
   - Set `X Min` / `X Max`, enter `Tau` / `Beta` guesses (leave a component
     blank to omit it; up to 4 components). Tick `Fix` to hold a parameter.
   - Click `Run Fit (Enter)` (or press Enter).
   - Click `Save Results (.xlsx)` to export the workbook.

4. **Make the publication figure**:

   ```bash
   python decay_graph_demo.py your_saved.xlsx
   ```

   A browser window opens at `http://127.0.0.1:8050` with the interactive
   explorer. Adjust offsets / log-x / legend / annotation, then click
   **Save publication PNG/PDF** to drop a 600 dpi figure next to the .xlsx.

   Run with no arguments to pop a file picker:

   ```bash
   python decay_graph_demo.py
   ```

   The plot config (visible traces, baseline offsets, legend position,
   text-box position, log-x, time unit, axis ranges, annotation text)
   auto-saves to `{stem}_plot_config.json` beside the .xlsx and reloads
   on the next open, so your tuning is never lost.

---

## Repository layout

```
OSL_demo/
├── README.md
├── LICENSE
├── requirements.txt
├── read_data.py                       # minimal CSV reader (Time, Counts)
├── tail_fit.py                        # simplified tail-fit engine
├── fit_gui.py                         # Tk + Matplotlib interactive GUI
├── decay_graph_demo.py                # Plotly + Dash interactive plotter
│                                        with draggable legend / text box
│                                        and JSON config persistence
├── demo_data_4CzIPN_mCP_decay.csv     # sample TCSPC decay
├── data/                              # place your own decay CSVs here
└── screenshots/                       # add your own screenshots here
```

---

## Why a public demo of a private codebase?

- **Reproducibility**: colleagues reading my papers can run the same
  pipeline on the same kind of data and see what the figures looked like
  before they were finalised.
- **Collaboration**: a self-contained demo is easier to share than a
  sprawling private repo with several modalities worth of code.
- **Credit without free-riding**: the demo lets people evaluate the
  engineering quality and the workflow design, while the full engine —
  which represents years of research-time investment — remains request-only.
- **Academic openness**: I believe the methods should be visible; the
  polished, reusable implementation can be shared on reasonable request.

---

## Contact

**Source code of the full engine is available on reasonable request.**

- Author: Kaiyang Wei
- Email: `weikaiyang.kw@gmail.com`
- ORCID: see [GitHub profile](https://github.com/KaiyangW) (link in bio)

---

## License

This demo code is released under the **MIT License** (see `LICENSE`).
You are free to run, study, and adapt it for academic use.

The full private research engine is **not** covered by this license and
is not distributed from this repository. Please contact the author for
access terms.
