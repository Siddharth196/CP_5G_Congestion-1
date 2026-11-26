#!/usr/bin/env python3
"""
calibrate_tractor_matched.py — choose the rate scale k and sessions per UE.

Reads the TractorMatchedCalib runs (one seed of every n x k x sess) after
export_all.sh, labels them exactly as the datasets do, and prints one row
per (sess, k, n). The goal from the task:

    starvation rises clearly with the number of UEs: low at 4 UEs, tens of
    percent at 8-10, with a useful number of onsets in every configuration.

A candidate (sess, k) passes when, with starvation measured over the rows
where the UE has downlink demand:
    starved(4) <= --max-low, starved(8) >= --min-8, starved(10) >= --min-10,
    starved never falls as n grows, and every n has >= --min-onsets onsets.
Among passing candidates, always-on traffic (sess = 1) is preferred, then
the one whose starved(10) is closest to --target-10.

    python analysis/calibrate_tractor_matched.py \
        --runs "results/tractor_matched_calib/canonical/TractorMatchedCalib-*.csv"
"""

import argparse
import glob
import os
import re
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from labels import THRESHOLD, label  # noqa: E402

NAME_RE = re.compile(r"-n(\d+)-k([\d.]+)-s(\d+)-\d+$")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--runs", default="results/tractor_matched_calib/canonical/TractorMatchedCalib-*.csv")
    ap.add_argument("--max-low", type=float, default=0.10)
    ap.add_argument("--min-8", type=float, default=0.10)
    ap.add_argument("--min-10", type=float, default=0.20)
    ap.add_argument("--target-10", type=float, default=0.30)
    ap.add_argument("--min-onsets", type=int, default=10)
    ap.add_argument("--out", default=None, help="also write the table as CSV")
    args = ap.parse_args()

    rows = []
    for f in sorted(glob.glob(args.runs)):
        d = pd.read_csv(f, low_memory=False)
        m = NAME_RE.search(str(d["experiment"].iloc[0]))
        if not m:
            continue
        d = label(d, "bytes", threshold=THRESHOLD)
        demand = d["ratio"].notna()
        cell = d.drop_duplicates("bin")
        rows.append({
            "sess": int(m.group(3)), "k": float(m.group(2)), "n": int(m.group(1)),
            "offered_mbps": round(d.groupby("bin")["dl_offered_bytes"].sum().mean() * 8 / 0.25 / 1e6, 1),
            "starved_demand": round(float(d.loc[demand, "starved"].mean()), 3),
            "starved_all": round(float(d["starved"].mean()), 3),
            "onsets": int(d["onset"].sum()),
            "onsets_demand": int(d.loc[demand, "onset"].sum()),
            "cqi_med": round(float(d["dl_cqi"].median()), 1),
            "util_med": round(float(cell["cell_util"].median()), 3),
        })
    if not rows:
        sys.exit(f"No calibration runs matched {args.runs}")
    t = pd.DataFrame(rows).sort_values(["sess", "k", "n"]).reset_index(drop=True)
    print(t.to_string(index=False))
    if args.out:
        t.to_csv(args.out, index=False)

    # several seeds: judge the mean over seeds, onsets by the weakest seed
    agg = (t.groupby(["sess", "k", "n"], as_index=False)
           .agg(seeds=("onsets", "size"), starved_demand=("starved_demand", "mean"),
                onsets=("onsets", "min"), onsets_mean=("onsets", "mean"),
                cqi_med=("cqi_med", "median"), util_med=("util_med", "median")))
    if (agg["seeds"] > 1).any():
        print("\nmean over seeds (onsets: weakest seed / mean):")
        print(agg.round(3).to_string(index=False))
    verdicts = []
    for (sess, k), g in agg.groupby(["sess", "k"]):
        s = g.set_index("n")["starved_demand"]
        if not {4, 8, 10} <= set(s.index):
            continue
        ok = {
            "low at 4": s[4] <= args.max_low,
            "8 UEs": s[8] >= args.min_8,
            "10 UEs": s[10] >= args.min_10,
            "rises with n": s.is_monotonic_increasing,
            "onsets": (g["onsets"] >= args.min_onsets).all(),
        }
        verdicts.append({"sess": sess, "k": k, "pass": all(ok.values()),
                         "failed": ", ".join(c for c, v in ok.items() if not v) or "-",
                         "starved_10": s[10]})
    v = pd.DataFrame(verdicts)
    print("\n" + v.drop(columns="starved_10").to_string(index=False))
    good = v[v["pass"]].copy()
    if good.empty:
        print("\nNo (sess, k) meets every criterion; widen the k grid or relax the thresholds.")
        sys.exit(2)
    good["prefer"] = (good["sess"] != 1).astype(int)
    good["dist"] = (good["starved_10"] - args.target_10).abs()
    best = good.sort_values(["prefer", "dist"]).iloc[0]
    print(f"\nchosen: sess = {int(best.sess)}, k = {best.k:g} "
          f"(Urban rates x {best.k:g}: video {2.8 * best.k:g}, web {0.2 * best.k:g}, "
          f"IoT {0.032 * best.k:g} Mbit/s per UE while a session is on)")
    print(f"set in omnetpp.ini [Config TractorMatched]:  k=${{k={best.k:g}}} sess=${{sess={int(best.sess)}}}")


if __name__ == "__main__":
    main()
