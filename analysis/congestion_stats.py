"""Does a run congest? Summary statistics on a canonical CSV from parse_vectors2.

    python congestion_stats.py results/canonical/UrbanCongestion-0.csv UrbanCongestion

Offered DL per UE is known exactly because the server traffic is CBR, so
served/offered is computed for DL as well as UL. Starvation threshold 0.415 is
the density valley of TRACTOR's granted/requested mixture; onsets use 2-bin
entry hysteresis and a 1 s (4-bin) horizon, as in the project summary.
Produces the table in CHANGES.md §14.
"""
import sys

import numpy as np
import pandas as pd

THRESH, HYST, H = 0.415, 2, 4
d = pd.read_csv(sys.argv[1])
config = sys.argv[2]
d["idx"] = d["ue"].str.slice(3).astype(int)


HDR = 28   # IP (20) + UDP (8): PDCP counts whole IP packets


def dl_offered_mbps(i):
    """Server CBR rate towards UE i as PDCP sees it (Mbit/s).
    Mirrors the *.server.app[...] packetSize / samplingTime lines in
    omnetpp.ini; update both together."""
    if config == "HeavyLoad" and i >= 20:
        size, period = 800, 0.01
    elif config == "LightLoad":
        size, period = 100, 0.02
    elif i < 20:
        size, period = 1400, 0.004
    elif i < 35:
        size, period = 500, 0.02
    else:
        size, period = 40, 0.01
    return (size + HDR) * 8 / period / 1e6


d["dl_ratio"] = d["dl_mbps"] / d["idx"].map(dl_offered_mbps)
d["ul_ratio"] = np.where(d["ul_offered_mbps_simonly"] > 0,
                         d["ul_mbps"] / d["ul_offered_mbps_simonly"], np.nan)
d = d[d["bin"] >= 4]          # skip the first second (attach, ramp-up)


def onsets(ratio):
    """2-bin entry/exit hysteresis, then count unstarved-now / starved-in-H."""
    raw = (ratio < THRESH).astype(float).where(ratio.notna())
    state, cur, run_in, run_out = [], 0, 0, 0
    for x in raw:
        if np.isnan(x):
            state.append(np.nan)
            continue
        run_in = run_in + 1 if x == 1 else 0
        run_out = run_out + 1 if x == 0 else 0
        if cur == 0 and run_in >= HYST:
            cur = 1
        elif cur == 1 and run_out >= HYST:
            cur = 0
        state.append(cur)
    s = pd.Series(state, index=ratio.index)
    return int(((s == 0) & (s.shift(-H) == 1)).sum()), float(np.nanmean(s)) if s.notna().any() else np.nan


util = d.drop_duplicates(["bin", "serving_cell"])["cell_granted_prbs"] / 12500
print(f"=== {config}: {d['ue'].nunique()} UEs, {d['bin'].nunique()} bins (t >= 1 s)")
print(f"cell DL utilisation   p50 {util.quantile(.5):.2f}  p95 {util.quantile(.95):.2f}  "
      f"max {util.max():.2f}  bins>0.9: {(util > 0.9).mean():.1%}")
print(f"dl_cqi                p5 {d['dl_cqi'].quantile(.05):.1f}  p50 {d['dl_cqi'].median():.1f}  "
      f"p95 {d['dl_cqi'].quantile(.95):.1f}")
print(f"latency (ms)          p50 {1e3*d['latency_simonly'].median():.1f}  "
      f"p95 {1e3*d['latency_simonly'].quantile(.95):.1f}  max {1e3*d['latency_simonly'].max():.1f}")
for side in ["dl", "ul"]:
    r = d[f"{side}_ratio"]
    per_ue = d.groupby("ue")[f"{side}_ratio"].apply(onsets)
    n_on = sum(x[0] for x in per_ue)
    starved = np.nanmean([x[1] for x in per_ue])
    print(f"{side.upper()} served/offered     p5 {r.quantile(.05):.2f}  p50 {r.median():.2f}   "
          f"UE-bins < {THRESH}: {(r < THRESH).mean():.1%}   starved (hyst): {starved:.1%}   "
          f"onsets @1s: {n_on}")
by_group = d.assign(group=pd.cut(d["idx"], [-1, 19, 34, 49], labels=["UE 0-19", "UE 20-34", "UE 35-49"]))
print("DL served/offered by UE group (median):",
      by_group.groupby("group", observed=True)["dl_ratio"].median().round(2).to_dict())
