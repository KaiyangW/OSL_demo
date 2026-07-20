"""Minimal CSV reader for the demo.

Reads a TCSPC decay CSV with two numeric columns (Time, Counts).
Tries common encodings and skips non-numeric header lines automatically.
Returns a pandas DataFrame with columns ["Time", "Counts"].
"""

from __future__ import annotations

import os
from typing import Optional

import numpy as np
import pandas as pd

_ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin-1")


def read_decay_csv(path: str) -> Optional[pd.DataFrame]:
    """Load a 2-column (Time, Counts) decay CSV.

    Auto-skips header/metadata lines. Returns None on failure.
    """
    if not os.path.isfile(path):
        print(f"[read_data] file not found: {path}")
        return None

    last_err = None
    for enc in _ENCODINGS:
        try:
            df = pd.read_csv(path, encoding=enc, header=None, comment="#")
        except Exception as e:
            last_err = e
            continue

        # Keep only fully-numeric rows
        df = df.apply(pd.to_numeric, errors="coerce").dropna()
        if df.shape[1] < 2:
            continue

        # Take first two numeric columns
        out = df.iloc[:, :2].copy()
        out.columns = ["Time", "Counts"]
        out = out.reset_index(drop=True)

        if len(out) >= 4:
            return out

    print(f"[read_data] could not parse {path}: {last_err}")
    return None


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python read_data.py <decay.csv>")
        raise SystemExit(1)
    df = read_decay_csv(sys.argv[1])
    if df is not None:
        print(f"OK: {len(df)} rows, t in [{df['Time'].min():.3g}, {df['Time'].max():.3g}]")
        print(df.head())
