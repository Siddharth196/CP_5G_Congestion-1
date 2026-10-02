#!/usr/bin/env python3
"""
harmonise_tractor.py — TRACTOR Multi-UE logs -> canonical schema (canonical.py)

    python analysis/harmonise_tractor.py data/TRACTOR/logs/Multi-UE data/canonical/tractor.csv

What it does
------------
  1. Reads every <IMSI>_metrics.csv (one UE each, a row every 250 ms) under
     Trial0..Trial3/<condition>/. The single 19-column file
     (Trial0/embb1/1010123456002) has no slicing columns and is skipped.
  2. Aligns the UEs of a condition on common 250 ms bins
     (bin = Timestamp // 250 ms, counted from the condition's first slot).
  3. Computes cell-level quantities over ALL UEs before filtering:
     cell_granted_prbs (sum of per-UE grants; Simu5G only reports this) and
     n_ue_active.
  4. Keeps only slicing_enabled == 0 (unless --keep-slicing). With slicing on,
     the 14-PRB slice cap pins granted/requested near 0.29 regardless of load:
     that is configuration, not congestion.
  5. Adds traffic_class from the condition name (Trial0) or the trace list in
     output_<condition>.txt (Trials 2-3); Trial1 has no list -> "unknown".
  6. Derives dl_served_bytes (tx_brate x 250 ms) and dl_offered_bytes
     (served + growth of the DL buffer since the previous bin), the same
     demand/supply pair the simulator provides, so the simulator-style label
     can be checked against the true PRB label on real data (labels.py).

Column mapping (TRACTOR -> canonical)
-------------------------------------
  sum_granted_prbs (summed per cell)  cell_granted_prbs -> cell_util
  dl_cqi                              dl_cqi
  ul_sinr                             ul_sinr
  tx_brate downlink [Mbps]            dl_mbps
  rx_brate uplink [Mbps]              ul_mbps
  rx_errors uplink (%)                ul_error_pct
  sum_requested / sum_granted_prbs    requested_prbs / granted_prbs (label)
  tx_errors downlink (%)              dl_error_pct (always 0 in TRACTOR)
  dl_buffer / ul_buffer [bytes]       dl_buffer_bytes / ul_buffer_bytes
"""

import argparse
import glob
import os
import re
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from canonical import BIN_S, FEATURES, PRB_CEILING, to_canonical, traffic_class  # noqa: E402

BIN_MS = int(BIN_S * 1000)

RENAME = {
    "tx_brate downlink [Mbps]": "dl_mbps",
    "rx_brate uplink [Mbps]": "ul_mbps",
    "rx_errors uplink (%)": "ul_error_pct",
    "tx_errors downlink (%)": "dl_error_pct",
    "dl_buffer [bytes]": "dl_buffer_bytes",
    "ul_buffer [bytes]": "ul_buffer_bytes",
    "sum_requested_prbs": "requested_prbs",
    "sum_granted_prbs": "granted_prbs",
}
TRACE_RE = re.compile(r"raw/(\S+?)\.csv for (\d+)")


def read_traces(root):
    """(trial, condition, imsi) -> trace name, from output_<condition>.txt."""
    traces = {}
    for f in glob.glob(os.path.join(root, "*", "*", "output_*.txt")):
        trial, cond = os.path.normpath(f).split(os.sep)[-3:-1]
        with open(f) as fh:
            for line in fh:
                m = TRACE_RE.search(line)
                if m:
                    traces[(trial, cond.lower(), int(m.group(2)))] = m.group(1)
    return traces


def load(root):
    frames, skipped = [], []
    for f in sorted(glob.glob(os.path.join(root, "*", "*", "*_metrics.csv"))):
        trial, cond, name = os.path.normpath(f).split(os.sep)[-3:]
        d = pd.read_csv(f)
        d = d.loc[:, [c for c in d.columns if not c.startswith("Unnamed")]]
        if "slicing_enabled" not in d:
            skipped.append(f"{trial}/{cond}/{name}")
            continue
        d["trial"], d["condition"] = trial, cond.lower()
        d["imsi"] = int(name.split("_")[0])
        frames.append(d)
    if not frames:
        sys.exit(f"No *_metrics.csv with slicing columns under {root}")
    for s in skipped:
        print(f"  skipped {s}: no slicing columns (19-column schema)")
    return pd.concat(frames, ignore_index=True)


def align(t):
    """Common 250 ms bins per condition; one row per UE and bin."""
    t["slot"] = (t["Timestamp"] // BIN_MS).astype("int64")
    t["bin"] = t["slot"] - t.groupby(["trial", "condition"])["slot"].transform("min")
    before = len(t)
    t = (t.sort_values(["trial", "condition", "imsi", "Timestamp"])
          .drop_duplicates(["trial", "condition", "imsi", "bin"], keep="last"))
    if len(t) < before:
        print(f"  merged {before - len(t):,} rows that fell into an already used bin")
    return t


def add_cell_level(t):
    """Sum over every UE in the cell, before any filtering."""
    g = t.groupby(["trial", "condition", "bin"])
    t["cell_granted_prbs"] = g["granted_prbs"].transform("sum")
    t["n_ue_active"] = g["imsi"].transform("nunique")
    return t


def add_bytes(t):
    """DL bytes served in the bin, and bytes that arrived (served + buffer growth)."""
    t = t.sort_values(["trial", "condition", "imsi", "bin"])
    t["dl_served_bytes"] = t["dl_mbps"] * 1e6 / 8 * BIN_S
    ue = t.groupby(["trial", "condition", "imsi"])
    consecutive = ue["bin"].diff() == 1
    growth = ue["dl_buffer_bytes"].diff().where(consecutive)
    t["dl_offered_bytes"] = (t["dl_served_bytes"] + growth).clip(lower=0)
    return t


def harmonise(root, keep_slicing=False):
    print(f"Loading {root} ...")
    t = load(root)
    print(f"  {len(t):,} rows, {t.groupby(['trial', 'condition', 'imsi']).ngroups} UE files, "
          f"{t.groupby(['trial', 'condition']).ngroups} conditions")
    t = t.rename(columns=RENAME)
    t = align(t)
    t = add_cell_level(t)
    t = add_bytes(t)

    traces = read_traces(root)
    key = t[["trial", "condition", "imsi"]].drop_duplicates()
    key["traffic_class"] = [
        traffic_class(c) if tr == "Trial0" else traffic_class(traces.get((tr, c, i), ""))
        for tr, c, i in key.itertuples(index=False)]
    t = t.merge(key, on=["trial", "condition", "imsi"])

    if not keep_slicing:
        n = len(t)
        t = t[t["slicing_enabled"] == 0]
        print(f"  slicing_enabled == 0: kept {len(t):,} of {n:,} rows")

    t["source"] = "tractor"
    t["experiment"] = t["trial"] + "/" + t["condition"]
    t["ue"] = "UE_" + t["imsi"].astype(str)   # a bare IMSI would be read back as an int
    t["t_s"] = t["bin"] * BIN_S
    t["serving_cell"] = 1
    return to_canonical(t)


def report(df):
    print(f"\nrows: {len(df):,}   UEs: {df.groupby(['experiment', 'ue']).ngroups}   "
          f"experiments: {df['experiment'].nunique()}")
    print("rows per trial:", df.groupby("trial").size().to_dict())
    print("traffic class (UE files):",
          df.drop_duplicates(["experiment", "ue"])["traffic_class"].value_counts().to_dict())
    print("\nfill rate of shared features (%):")
    for c in FEATURES:
        print(f"  {c:<14} {100 * df[c].notna().mean():6.2f}")
    over = (df["cell_granted_prbs"] > PRB_CEILING).mean()
    print(f"\ncell_granted_prbs > ceiling ({PRB_CEILING:,}): {over:.2%} of rows "
          f"(UE reports are not slot-synchronous, so a bin can catch slightly more than 250 TTIs)")
    print(f"cell_util p50 {df['cell_util'].median():.3f}  p95 {df['cell_util'].quantile(.95):.3f}")
    demand = df["requested_prbs"] > 0
    print(f"rows with PRB demand: {demand.mean():.1%}   "
          f"granted/requested median {(df.loc[demand, 'granted_prbs'] / df.loc[demand, 'requested_prbs']).median():.3f}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("root", help="TRACTOR logs/Multi-UE directory")
    ap.add_argument("out_csv")
    ap.add_argument("--keep-slicing", action="store_true",
                    help="keep slicing_enabled == 1 rows (not for the experiments)")
    args = ap.parse_args()
    df = harmonise(args.root, args.keep_slicing)
    report(df)
    os.makedirs(os.path.dirname(os.path.abspath(args.out_csv)), exist_ok=True)
    df.to_csv(args.out_csv, index=False)
    print(f"\nwrote {args.out_csv}")


if __name__ == "__main__":
    main()
