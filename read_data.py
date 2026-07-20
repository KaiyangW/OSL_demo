"""Minimal CSV reader for the demo.

Reads a TCSPC decay CSV with two numeric columns (Time, Counts).
Handles common instrument-export quirks:

* metadata header lines (FluOracle-style ``Labels,...``, ``Type,...``,
  ``XAxis,Time(ns)``, etc.) before the actual data starts
* BOM / cp1252 / latin-1 encodings
* trailing empty columns
* scientific-notation counts (``0.00000000E+0``)
* comment lines starting with ``#``

Returns a pandas DataFrame with columns ["Time", "Counts"], or None on
failure.
"""

from __future__ import annotations

import os
from typing import Optional

import pandas as pd

_ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin-1")


def read_decay_csv(path: str) -> Optional[pd.DataFrame]:
    """Load a 2-column (Time, Counts) decay CSV.

    Auto-skips header/metadata rows and keeps only rows where BOTH the
    first and second columns are numeric. Returns None on failure.
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

        if df.shape[1] < 2:
            continue

        # Drop trailing empty columns (often introduced by a trailing comma)
        # by removing columns that are entirely NaN.
        df = df.dropna(axis=1, how="all")

        if df.shape[1] < 2:
            continue

        # Take the first two columns as (time, counts)
        c0 = df.iloc[:, 0]
        c1 = df.iloc[:, 1]

        # Convert to numeric; keep only rows where BOTH are valid numbers.
        c0_num = pd.to_numeric(c0, errors="coerce")
        c1_num = pd.to_numeric(c1, errors="coerce")
        valid = c0_num.notna() & c1_num.notna()

        if valid.sum() < 4:
            # Not enough numeric rows in this encoding; try the next one.
            continue

        out = pd.DataFrame({
            "Time":   c0_num[valid].to_numpy(dtype=float),
            "Counts": c1_num[valid].to_numpy(dtype=float),
        }).reset_index(drop=True)

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
        print("head:")
        print(df.head())
        print("tail:")
        print(df.tail())
