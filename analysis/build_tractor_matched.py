#!/usr/bin/env python3
"""
build_tractor_matched.py — the TRACTOR-matched single-cell sweep as its own dataset.

A separate simulator dataset next to E1-E6, which it does not change: one
cell, 4-10 static UEs (TractorMatched* configs in omnetpp.ini). Use it as an
extra simulator training source and as a held-out simulator test set.

    python analysis/build_tractor_matched.py \
        --sim "results/canonical/TractorMatched*.csv" --out data/datasets_tractor_matched

Writes one folder per PRB setting that has runs:

    TM50/   TractorMatched runs,   50 PRBs, cell_util / 12,500 (TRACTOR)
    TM52/   TractorMatched52 runs, 52 PRBs, cell_util / 13,000 (srsRAN testbed)

each with train.csv.gz, test.csv.gz, scaler.json, manifest.json, plus
summary.csv / summary.md (one row per number of UEs) in the output folder.

Split: the highest seed of every number of UEs is the test set, the rule E1
uses. Whole runs go to one side only.

Labels and features are exactly as in build_datasets.py: bytes label,
threshold 0.5, hysteresis 2 bins, onset 4 bins (1 s) ahead, the six features
forward-filled within each UE series and standardised with train statistics.
Extra columns, none of them a model input:

    n_ues           UEs in the run (4, 6, 8, 10)
    dl_cqi_filled   1 where the canonical dl_cqi was empty (no DL frame
                    reached the UE in that bin), so the dataset value is the
                    forward-filled one or still empty at the start of a series.
                    Diagnostic only: in the simulator it is almost the label,
                    and TRACTOR never has an empty CQI.
    ue_x_m, ue_y_m  static UE position in metres (gNB at 500, 500)
    link_limited    1 for a UE (per run) starved in > 90 % of the bins where
                    it has demand: its own link cannot carry its rate (a far
                    video UE at CQI 3-5 asking 16.8 Mbit/s), so its
                    starvation is not contention. Derived from the label:
                    never an input. Drop these UEs to train on contention.
"""

import argparse
import glob
import json
import os
import re
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_datasets import LABELS, capacity, fill  # noqa: E402
from canonical import FEATURES, IDENTITY  # noqa: E402
from labels import HORIZON, HYSTERESIS, SERIES, THRESHOLD, label  # noqa: E402

SETS = {"TractorMatched": "TM50", "TractorMatched52": "TM52"}
RUN_RE = re.compile(r"-n(\d+)-(\d+)$")      # TractorMatched-n8-3 -> 8 UEs, seed 3
DIAG = ["n_ues", "dl_cqi_filled", "ue_x_m", "ue_y_m", "link_limited"]
LINK_LIMITED = 0.9


def load(pattern, threshold):
    frames = []
    for f in sorted(glob.glob(pattern)):
        d = pd.read_csv(f, low_memory=False)
        cond = str(d["condition"].iloc[0])
        if cond not in SETS:
            continue                     # TractorMatchedCalib / Capacity runs
        m = RUN_RE.search(str(d["experiment"].iloc[0]))
        if not m:
            sys.exit(f"{f}: experiment {d['experiment'].iloc[0]!r} is not <Config>-n<UEs>-<seed>")
        if d["dl_offered_bytes"].isna().all():
            sys.exit(f"{f}: no dl_offered_bytes. Re-export the run with its .sca.")
        if d["ue"].nunique() != int(m.group(1)):
            sys.exit(f"{f}: {d['ue'].nunique()} UEs in the table, {m.group(1)} in the run name")
        d["n_ues"] = int(m.group(1))
        d["dl_cqi_filled"] = d["dl_cqi"].isna().astype(int)
        for c in ["ue_x_m", "ue_y_m"]:
            if c not in d:
                d[c] = np.nan
        frames.append(d)
    if not frames:
        sys.exit(f"No TractorMatched / TractorMatched52 runs matched {pattern}")
    df = pd.concat(frames, ignore_index=True)
    df = label(df, "bytes", threshold=threshold).assign(label_basis="bytes")
    # starved in > LINK_LIMITED of the bins where it has demand: the UE's own
    # link cannot carry its rate (a far video UE at CQI 3-5), so its
    # starvation is not contention. Built from the label: diagnostic only.
    demand = df["ratio"].notna()
    share = df[demand].groupby(SERIES)["starved"].mean().rename("_ue_share")
    df = df.merge(share, left_on=SERIES, right_index=True, how="left")
    df["link_limited"] = (df.pop("_ue_share") > LINK_LIMITED).astype(int)
    return df


def split(df):
    """Highest seed of each (config, n_ues) to test; needs at least two seeds."""
    runs = df[["condition", "n_ues", "trial", "experiment"]].drop_duplicates()
    runs["trial"] = runs["trial"].astype(int)
    test = []
    for (cond, n), g in runs.groupby(["condition", "n_ues"]):
        if g["trial"].nunique() < 2:
            sys.exit(f"{cond} with {n} UEs has one seed only; the split needs two or more")
        test.append(g.loc[g["trial"].idxmax(), "experiment"])
    is_test = df["experiment"].isin(test)
    return df[~is_test], df[is_test], sorted(test)


def summary(df):
    """Per number of UEs, on the labelled rows (dl_cqi as measured, not filled)."""
    rows = []
    for (cond, n), g in df.groupby(["condition", "n_ues"]):
        demand = g["ratio"].notna()
        rows.append({
            "set": SETS[cond], "n_ues": n, "runs": g["experiment"].nunique(),
            "rows": len(g),
            "starved_share": round(float(g["starved"].mean()), 4),
            "starved_share_with_demand": round(float(g.loc[demand, "starved"].mean()), 4),
            "onset_share": round(float(g["onset"].mean()), 5),
            "onsets": int(g["onset"].sum()),
            "onsets_with_demand": int(g.loc[demand, "onset"].sum()),
            "link_limited_ues": int(g[g["link_limited"] == 1].groupby(SERIES).ngroups),
            "starved_share_with_demand_excl_link_limited": round(float(
                g.loc[demand & (g["link_limited"] == 0), "starved"].mean()), 4),
            "onsets_excl_link_limited": int(g.loc[g["link_limited"] == 0, "onset"].sum()),
            "median_dl_cqi": round(float(g["dl_cqi"].median()), 2),
            "median_cell_util": round(float(g["cell_util"].median()), 4),
            "dl_cqi_empty_share": round(float(g["dl_cqi_filled"].mean()), 4),
        })
    return pd.DataFrame(rows)


def markdown(df):
    cells = [[str(c) for c in df.columns]] + [[str(v) for v in r] for r in df.itertuples(index=False)]
    lines = ["| " + " | ".join(r) + " |" for r in cells]
    return "\n".join(lines[:1] + ["|" + "---|" * len(df.columns)] + lines[1:]) + "\n"


def write(name, train, test, out, notes, threshold):
    d = os.path.join(out, name)
    os.makedirs(d, exist_ok=True)
    caps = capacity(pd.concat([train, test]))
    per_run = {e: capacity(g)["simu5g"] for e, g in pd.concat([train, test]).groupby("experiment")}
    train, test = fill(train), fill(test)
    mean, std = train[FEATURES].mean(), train[FEATURES].std().replace(0, 1)
    cols = IDENTITY + FEATURES + [f"f_{c}" for c in FEATURES] + ["label_basis"] + LABELS + DIAG
    parts = {}
    for part, df in [("train", train), ("test", test)]:
        df = df.copy()
        for c in FEATURES:
            df[f"f_{c}"] = ((df[c] - mean[c]) / std[c]).fillna(0.0)
        df[cols].to_csv(os.path.join(d, f"{part}.csv.gz"), index=False)
        parts[part] = {
            "rows": len(df),
            "ue_series": int(df.groupby(SERIES).ngroups),
            "experiments": sorted(df["experiment"].unique().tolist()),
            "n_ues": sorted(int(x) for x in df["n_ues"].unique()),
            "starved_share": round(float(df["starved"].mean()), 4),
            "onsets": int(df["onset"].sum()),
            "onset_share": round(float(df["onset"].mean()), 5),
        }
    with open(os.path.join(d, "scaler.json"), "w") as fh:
        json.dump({"features": FEATURES, "mean": mean.round(6).to_dict(),
                   "std": std.round(6).to_dict(), "fit_on": "train"}, fh, indent=2)
    manifest = {"experiment": name, "notes": notes,
                "label": {"threshold": threshold, "hysteresis_bins": HYSTERESIS,
                          "onset_horizon_bins": HORIZON, "bin_s": 0.25,
                          "basis": {"simu5g": "dl_served_bytes / dl_offered_bytes"}},
                "prb_capacity_per_bin": caps,
                "prb_capacity_per_run": per_run,
                "diagnostic_columns_not_inputs": DIAG,
                **parts}
    with open(os.path.join(d, "manifest.json"), "w") as fh:
        json.dump(manifest, fh, indent=2)
    print(f"  {name}: train {parts['train']['rows']:,} rows ({parts['train']['onsets']:,} onsets), "
          f"test {parts['test']['rows']:,} rows ({parts['test']['onsets']:,} onsets), "
          f"cell_util / {caps['simu5g']}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--sim", default="results/canonical/TractorMatched*.csv",
                    help="glob of the sweep's canonical CSVs, quoted")
    ap.add_argument("--out", default="data/datasets_tractor_matched")
    ap.add_argument("--threshold", type=float, default=THRESHOLD)
    args = ap.parse_args()

    print("Labelling TRACTOR-matched runs (bytes basis) ...")
    df = load(args.sim, args.threshold)
    print(f"  {df['experiment'].nunique()} runs, {len(df):,} rows, starved {df['starved'].mean():.1%}")
    os.makedirs(args.out, exist_ok=True)
    for cond, name in SETS.items():
        sub = df[df["condition"] == cond]
        if sub.empty:
            continue
        train, test, held = split(sub)
        print(f"\n{name} held-out runs: {held}")
        prbs = "50 PRBs, cell_util / 12,500" if name == "TM50" else "52 PRBs, cell_util / 13,000"
        write(name, train, test, args.out,
              f"{cond}: one cell, 4-10 static UEs, {prbs}; test = highest seed of each n_ues",
              args.threshold)
    s = summary(df)
    s.to_csv(os.path.join(args.out, "summary.csv"), index=False)
    with open(os.path.join(args.out, "summary.md"), "w") as fh:
        fh.write(markdown(s))
    print("\n" + s.to_string(index=False))
    print(f"\nwrote {args.out}/<TM50|TM52>/{{train,test}}.csv.gz, scaler.json, manifest.json, summary.*")


if __name__ == "__main__":
    main()
