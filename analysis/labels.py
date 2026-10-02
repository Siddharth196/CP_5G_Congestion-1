#!/usr/bin/env python3
"""
labels.py — congestion state and onset labels on the canonical schema

    python analysis/labels.py data/canonical/tractor.csv --basis prb
    python analysis/labels.py results/canonical/UrbanCongestion-0.csv --basis bytes

The same code labels both sources:

  ratio     supply / demand per UE and bin, NaN when there is no demand
              basis "prb"    granted_prbs / requested_prbs      TRACTOR, the true label
              basis "bytes"  dl_served_bytes / dl_offered_bytes the simulator; also
                                                                 computable on TRACTOR
  raw       ratio < threshold; bins without demand count as not starved
  starved   raw with `hysteresis` consecutive bins needed to enter and to leave
  onset     not starved now and starved `horizon` bins later; NaN where that
            bin does not exist (end of a series or a gap)

Why the threshold is 0.5 and not a fitted mixture: the two modes behind the
original 0.415 (0.292 and 0.978) are slicing-on rows, where the 14-PRB slice
cap pins granted/requested near 0.29, against everything else. With
slicing_enabled == 0, which the experiments require, granted/requested has a
single mode near 1.0 and a smooth left tail; there is no valley to fit. 0.5
reads directly as "granted less than half of what was requested"; --report
shows how the label moves for 0.3 / 0.415 / 0.5 / 0.7.

On TRACTOR, --report also scores the simulator-style bytes label against
the true PRB label (precision, recall, Cohen's kappa), which is the check
that justifies using the bytes label on the simulator side.
"""

import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from canonical import BIN_S  # noqa: E402

THRESHOLD = 0.5
HYSTERESIS = 2      # bins to enter and to leave the starved state
HORIZON = 4         # bins ahead for onset (4 x 250 ms = 1 s)
SERIES = ["experiment", "ue"]

BASES = {
    "prb": ("granted_prbs", "requested_prbs"),
    "bytes": ("dl_served_bytes", "dl_offered_bytes"),
}


def ratio(df, basis):
    supply, demand = BASES[basis]
    d = df[demand]
    return (df[supply] / d).where(d > 0)


def hysteresis(raw, bins, k):
    """State machine over one UE's series; a gap in bins resets it."""
    state = np.zeros(len(raw))
    cur = run_in = run_out = 0
    prev_bin = None
    for i, (x, b) in enumerate(zip(raw, bins)):
        if prev_bin is not None and b != prev_bin + 1:
            cur = run_in = run_out = 0
        prev_bin = b
        run_in = run_in + 1 if x else 0
        run_out = run_out + 1 if not x else 0
        if not cur and run_in >= k:
            cur = 1
        elif cur and run_out >= k:
            cur = 0
        state[i] = cur
    return state


def label(df, basis, threshold=THRESHOLD, hyst=HYSTERESIS, horizon=HORIZON, prefix=""):
    """Return df sorted by series and bin, with ratio / starved_raw / starved / onset."""
    df = df.sort_values(SERIES + ["bin"]).reset_index(drop=True)
    r = ratio(df, basis)
    raw = (r < threshold).to_numpy()            # NaN (no demand) -> False
    state = np.empty(len(df))
    for _, idx in df.groupby(SERIES, sort=False).indices.items():
        state[idx] = hysteresis(raw[idx], df["bin"].to_numpy()[idx], hyst)
    g = df.groupby(SERIES, sort=False)
    later = pd.Series(state, index=df.index).groupby([df[c] for c in SERIES], sort=False).shift(-horizon)
    reachable = g["bin"].shift(-horizon) == df["bin"] + horizon
    onset = ((state == 0) & (later == 1)).astype(float).where(reachable)
    df[prefix + "ratio"] = r
    df[prefix + "starved_raw"] = raw.astype(int)
    df[prefix + "starved"] = state.astype(int)
    df[prefix + "onset"] = onset
    return df


def agreement(y_true, y_pred):
    """Precision, recall, F1 and Cohen's kappa of a binary prediction."""
    m = y_true.notna() & y_pred.notna()
    t, p = y_true[m].astype(bool), y_pred[m].astype(bool)
    tp, fp, fn, tn = (t & p).sum(), (~t & p).sum(), (t & ~p).sum(), (~t & ~p).sum()
    n = tp + fp + fn + tn
    prec = tp / (tp + fp) if tp + fp else np.nan
    rec = tp / (tp + fn) if tp + fn else np.nan
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else np.nan
    po = (tp + tn) / n
    pe = ((tp + fp) * (tp + fn) + (fn + tn) * (fp + tn)) / n ** 2
    kappa = (po - pe) / (1 - pe) if pe < 1 else np.nan
    return {"n": int(n), "prevalence": t.mean(), "precision": prec, "recall": rec,
            "f1": f1, "kappa": kappa}


def episodes(df):
    """Lengths (in bins) of contiguous starved runs."""
    lengths = []
    for _, s in df.groupby(SERIES, sort=False)["starved"]:
        v = s.to_numpy()
        edges = np.flatnonzero(np.diff(np.r_[0, v, 0]))
        lengths.extend(edges[1::2] - edges[::2])
    return np.array(lengths)


def report(df, basis, threshold, hyst, horizon):
    has_demand = df["ratio"].notna()
    print(f"\nbasis {basis}  threshold {threshold}  hysteresis {hyst}  horizon {horizon} "
          f"({horizon * BIN_S:g} s)")
    print(f"rows {len(df):,}   with demand {has_demand.mean():.1%}")
    print(f"starved (raw)       {df['starved_raw'].mean():.2%} of rows, "
          f"{df.loc[has_demand, 'starved_raw'].mean():.2%} of rows with demand")
    print(f"starved (hysteresis) {df['starved'].mean():.2%} of rows")
    print(f"onsets @{horizon * BIN_S:g} s       {int(df['onset'].sum()):,} "
          f"({df['onset'].mean():.2%} of rows with a reachable horizon)")
    ep = episodes(df)
    if len(ep):
        print(f"episodes            {len(ep):,}   length median {np.median(ep):.0f} bins, "
              f"p90 {np.percentile(ep, 90):.0f}, max {ep.max()}")
    later = df.groupby(SERIES, sort=False)["starved"].shift(-horizon)
    now = df["starved"] == 1
    print(f"P(starved in {horizon * BIN_S:g} s | starved now) = {later[now].mean():.3f}   "
          f"base rate {later.mean():.3f}")

    print("\nthreshold sensitivity (state with hysteresis / onsets):")
    for thr in [0.3, 0.415, 0.5, 0.7]:
        s = label(df, basis, thr, hyst, horizon, prefix="_s_")
        print(f"  {thr:<6} starved {s['_s_starved'].mean():6.2%}   onsets {int(s['_s_onset'].sum()):>7,}")

    if "n_ue_active" in df and df["n_ue_active"].nunique() > 1:
        tab = df[has_demand].groupby("n_ue_active")["starved"].mean()
        print("\nstarved share of rows with demand, by UEs present:",
              {int(k): round(v, 3) for k, v in tab.items()})

    if basis == "prb" and df[list(BASES["bytes"])].notna().any().all():
        b = label(df, "bytes", threshold, hyst, horizon, prefix="b_")
        print("\nsimulator-style bytes label vs true PRB label on this data:")
        for what in ["starved", "onset"]:
            a = agreement(b[what], b["b_" + what])
            print(f"  {what:<8} n {a['n']:,}  prevalence {a['prevalence']:.3f}  "
                  f"precision {a['precision']:.3f}  recall {a['recall']:.3f}  "
                  f"F1 {a['f1']:.3f}  kappa {a['kappa']:.3f}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("in_csv", help="canonical CSV (harmonise_tractor.py or parse_vectors2.py)")
    ap.add_argument("out_csv", nargs="?", help="write the labelled table here")
    ap.add_argument("--basis", choices=BASES, required=True)
    ap.add_argument("--threshold", type=float, default=THRESHOLD)
    ap.add_argument("--hysteresis", type=int, default=HYSTERESIS)
    ap.add_argument("--horizon", type=int, default=HORIZON, help="bins ahead (4 = 1 s)")
    args = ap.parse_args()

    df = label(pd.read_csv(args.in_csv, low_memory=False), args.basis,
               args.threshold, args.hysteresis, args.horizon)
    report(df, args.basis, args.threshold, args.hysteresis, args.horizon)
    if args.out_csv:
        df.to_csv(args.out_csv, index=False)
        print(f"\nwrote {args.out_csv}")


if __name__ == "__main__":
    main()
