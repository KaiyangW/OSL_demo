"""Simplified tail-fitting engine for TCSPC decays.

This is a reduced version of the private research engine. It performs a
multi-component stretched-exponential tail fit by Poisson-weighted
least-squares, without reconvolution, multi-start, uncertainty propagation,
PF/DF area analysis, or RISC calculations.

Mathematical model
------------------
For time t >= xmin (the tail start):

    model(t) = bkg + sum_i  B_i * exp( -( (t - xmin) / tau_i )^beta_i )

For t < xmin, the model returns bkg only.

Residuals are Poisson-weighted:
    r_i = (model_i - data_i) / sqrt(max(data_i, 1))

Optimisation uses scipy.optimize.least_squares with soft_l1 robust loss and
box constraints. tau and beta can be fixed by giving an epsilon-tight bound.

Parameter layout
----------------
    params = [bkg, B1, tau1, beta1, B2, tau2, beta2, ...]

Component lifetimes (reported per component)
--------------------------------------------
    num_ave = (tau/beta) * Gamma(1/beta)        # 1st moment, <t>
    int_ave = tau * Gamma(2/beta) / Gamma(1/beta)  # intensity-averaged
    area    = B * num_ave
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List

import numpy as np
import pandas as pd
from scipy.optimize import least_squares
from scipy.special import gamma


# ----------------------------------------------------------------------
# Model and residuals
# ----------------------------------------------------------------------
def multi_exp_tail(params, t, xmin, num_exp):
    """Multi-component stretched-exponential tail model.

    params = [bkg, B1, tau1, beta1, B2, tau2, beta2, ...]
    """
    bkg = params[0]
    model = np.full_like(t, bkg, dtype=float)

    mask = t >= xmin
    if not np.any(mask):
        return model

    t_decay = t[mask] - xmin
    decay = np.zeros_like(t_decay)
    for i in range(num_exp):
        B_i   = params[1 + i * 3]
        tau_i = params[2 + i * 3]
        beta_i = params[3 + i * 3]
        decay += B_i * np.exp(-np.power(t_decay / tau_i, beta_i))

    model[mask] += decay
    return model


def residuals(params, t, data, fit_mask, xmin, num_exp):
    """Poisson-weighted residuals on the fit window."""
    model = multi_exp_tail(params, t, xmin, num_exp)
    m = model[fit_mask]
    d = data[fit_mask]
    w = 1.0 / np.sqrt(np.maximum(d, 1.0))
    return (m - d) * w


# ----------------------------------------------------------------------
# Result container
# ----------------------------------------------------------------------
@dataclass
class Component:
    B: float
    tau: float
    beta: float
    num_ave: float
    int_ave: float
    area: float
    rel_percent: float
    is_phos: bool = False


@dataclass
class FitResult:
    x: np.ndarray
    chi_sq: float
    components: List[Component] = field(default_factory=list)
    curve_df: "pd.DataFrame" = None
    params_df: "pd.DataFrame" = None


# ----------------------------------------------------------------------
# Auto-formatting helper
# ----------------------------------------------------------------------
def auto_format_time(tau_ns):
    try:
        val = float(tau_ns)
        if val == 0:
            return "0.00 ns"
        elif val < 1e3:
            return f"{val:.2f} ns"
        elif val < 1e6:
            return f"{val / 1e3:.2f} us"
        elif val < 1e9:
            return f"{val / 1e6:.2f} ms"
        else:
            return f"{val / 1e9:.2f} s"
    except (ValueError, TypeError):
        return tau_ns


# ----------------------------------------------------------------------
# Main fit driver
# ----------------------------------------------------------------------
def run_tail_fit(t, data, xmin, xmax,
                 initial_taus, fixed_t_flags,
                 initial_betas, fixed_b_flags,
                 num_exp):
    """Run a single-start tail fit.

    Returns a FitResult dataclass with parameters, components, fit curve
    DataFrame, and a parameters DataFrame ready for Excel export.
    """
    fit_mask = (t >= xmin) & (t <= xmax)
    if not np.any(fit_mask):
        raise ValueError("Empty fit window (xmin/xmax out of data range).")

    max_counts = float(np.max(data[fit_mask]))
    bkg_guess  = float(np.min(data[fit_mask]))

    p0 = [bkg_guess]
    lower = [0.0]
    upper = [max_counts]

    epsilon = 1e-6
    for i in range(num_exp):
        amp_guess = max(1.0, max_counts / (10 ** i))
        tau_guess = float(initial_taus[i])
        beta_guess = float(initial_betas[i])

        p0.extend([amp_guess, tau_guess, beta_guess])
        lower.extend([0.0, 0.01, 0.3])
        upper.extend([np.inf, np.inf, 1.0])

        if fixed_t_flags[i]:
            lower[-2] = max(0.01, tau_guess - epsilon)
            upper[-2] = tau_guess + epsilon
        if fixed_b_flags[i]:
            lower[-1] = max(0.3, beta_guess - epsilon)
            upper[-1] = min(1.0, beta_guess + epsilon)

    res = least_squares(
        residuals, p0, bounds=(lower, upper),
        args=(t, data, fit_mask, xmin, num_exp),
        loss="soft_l1", f_scale=1.0, x_scale="jac",
        ftol=1e-8, xtol=1e-8, max_nfev=2000,
    )

    model_full = multi_exp_tail(res.x, t, xmin, num_exp)
    res_sq = (data[fit_mask] - model_full[fit_mask]) ** 2
    variance = np.maximum(data[fit_mask], 1.0)
    dof = max(1, len(data[fit_mask]) - len(p0))
    chi_sq = float(np.sum(res_sq / variance) / dof)

    bkg = res.x[0]

    components = []
    total_area = 0.0
    for i in range(num_exp):
        B_i   = float(res.x[1 + i * 3])
        tau_i = float(res.x[2 + i * 3])
        beta_i = float(res.x[3 + i * 3])
        num_ave = float((tau_i / beta_i) * gamma(1.0 / beta_i))
        int_ave = float(tau_i * gamma(2.0 / beta_i) / gamma(1.0 / beta_i))
        area_i  = B_i * num_ave
        total_area += area_i
        components.append(Component(
            B=B_i, tau=tau_i, beta=beta_i,
            num_ave=num_ave, int_ave=int_ave,
            area=area_i, rel_percent=0.0, is_phos=False,
        ))

    if total_area <= 0:
        total_area = 1e-9
    for c in components:
        c.rel_percent = (c.area / total_area) * 100.0

    # Sort components by tau (ascending), keep display order 1-based
    components = sorted(components, key=lambda c: c.tau)

    # --- Build Fit_Curve sheet (mirrors the real engine's columns) ---
    t_plot      = t[fit_mask]
    model_plot  = model_full[fit_mask]
    weighted_res = (data[fit_mask] - model_plot) / np.sqrt(np.maximum(data[fit_mask], 1.0))
    curve_df = pd.DataFrame({
        "Time (ns)":             t_plot,
        "Counts":                data[fit_mask],
        "Fitted Data":           model_plot,
        "Residuals":             data[fit_mask] - model_plot,
        "Weighted Residuals":    weighted_res,
    })

    # --- Build Parameters sheet ---
    params_list = [
        {"Parameter": "Reduced Chi-Squared", "Value": chi_sq},
        {"Parameter": "Background",          "Value": bkg},
    ]
    for i, c in enumerate(components):
        params_list.extend([
            {"Parameter": f"Tau_{i+1} (ns)",             "Value": c.tau},
            {"Parameter": f"Beta_Stretching_{i+1}",      "Value": c.beta},
            {"Parameter": f"Num_Ave_Tau_{i+1} (ns)",     "Value": c.num_ave},
            {"Parameter": f"Int_Ave_Tau_{i+1} (ns)",     "Value": c.int_ave},
            {"Parameter": f"Amplitude_{i+1}",            "Value": c.B},
            {"Parameter": f"Relative_Area_{i+1} (%)",    "Value": c.rel_percent},
        ])
    # Also add a Formatted_Value column mirroring the real engine
    formatted = []
    for row in params_list:
        name = str(row["Parameter"])
        val  = row["Value"]
        if "tau" in name.lower() or "Ave_Tau" in name:
            formatted.append(auto_format_time(val))
        else:
            formatted.append(val)
    params_df = pd.DataFrame(params_list)
    params_df["Formatted_Value"] = formatted

    return FitResult(
        x=res.x, chi_sq=chi_sq, components=components,
        curve_df=curve_df, params_df=params_df,
    )


# Helper for callers: format a value/error pair
def format_val_err(val, err, formatter=auto_format_time):
    if err is None or not np.isfinite(err) or err == 0.0:
        return formatter(val)
    return f"{formatter(val)} +- {formatter(err)}"
