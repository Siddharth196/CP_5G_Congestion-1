#!/usr/bin/env python3
"""
build_datasets.py — labelled, normalised train/test sets for experiments E1-E5

    python analysis/build_datasets.py --sim "results/canonical/*.csv" \
        --tractor data/canonical/tractor.csv --out data/datasets

Inputs are canonical tables (parse_vectors2.py / harmonise_tractor.py). Both
sources are labelled by labels.py with the same threshold, hysteresis and
horizon: TRACTOR on granted/requested PRBs (the true label), the simulator on
served/offered DL bytes (it emits no requested-PRB statistic).

Experiments (from the project plan)
  E1  sim -> sim            train: sim runs except held-out seeds; test: held-out seeds
  E2  sim -> real           train: all sim; test: TRACTOR test conditions (zero-shot)
  E3  sim + k% real -> real train: all sim + k% of the TRACTOR pool (whole UE series);
                            test: TRACTOR test conditions; k = 5, 10, 20
  E4  real -> real          train: TRACTOR pool; test: TRACTOR test conditions
  E5  real 4-6 UEs -> 9-10  train: Trials 1-3 multi4-6; test: Trials 1-3 multi9-10

E2, E3 and E4 share one TRACTOR test set, a fixed ~30 % of conditions per
trial, so E4 - E2 is measured on identical data. Splits never cut through a
UE series (sim: whole runs; TRACTOR: whole conditions or UE series).

Each experiment directory holds train.csv.gz, test.csv.gz, scaler.json and
manifest.json. f_<feature> columns are the shared features standardised with
the TRAIN set's mean and std (missing values forward-filled within each UE
series, then set to the train mean, i.e. 0 after scaling). Rows whose onset
horizon falls outside the series have onset = NaN; drop them when training
on onset.
"""

import argparse
import glob
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from canonical import FEATURES, IDENTITY  # noqa: E402
from labels import HORIZON, HYSTERESIS, SERIES, THRESHOLD, label  # noqa: E402

SEED = 7
TEST_SHARE = 0.3
E3_SHARES = [0.05, 0.10, 0.20]
E5_TRAIN = {"multi4", "multi5", "multi6"}
E5_TEST = {"multi9", "multi10"}
LABELS = ["ratio", "starved", "onset"]


def load_sim(pattern, exclude):
    files = sorted(glob.glob(pattern))
    frames = []
    for f in files:
        d = pd.read_csv(f, low_memory=False)
        if d["condition"].iloc[0] in exclude:
            continue
        if d["dl_offered_bytes"].isna().all():
            sys.exit(f"{f}: no dl_offered_bytes. Re-export the run with its .sca "
                     f"(analysis/export_all.sh does this).")
        frames.append(d)
    if not frames:
        sys.exit(f"No simulator runs matched {pattern}")
    sim = pd.concat(frames, ignore_index=True)
    return label(sim, "bytes").assign(label_basis="bytes")


def load_tractor(path):
    t = pd.read_csv(path, low_memory=False)
    if (t["slicing_enabled"] != 0).any():
        sys.exit("TRACTOR table contains slicing_enabled != 0 rows; re-run "
                 "harmonise_tractor.py without --keep-slicing.")
    return label(t, "prb").assign(label_basis="prb")


def split_sim(sim):
    """Hold out the highest seed of every config that has at least two seeds."""
    runs = sim[["condition", "trial", "experiment"]].drop_duplicates()
    runs["seed"] = pd.to_numeric(runs["trial"], errors="coerce")
    test = set()
    for _, g in runs.groupby("condition"):
        if g["seed"].nunique() >= 2:
            test.add(g.loc[g["seed"].idxmax(), "experiment"])
    if not test:
        sys.exit("E1 needs at least one config with two or more seeds.")
    is_test = sim["experiment"].isin(test)
    return sim[~is_test], sim[is_test], sorted(test)


def split_tractor(t, rng):
    """Fixed share of conditions per trial as the shared real test set."""
    conds = t[["trial", "experiment"]].drop_duplicates().sort_values("experiment")
    test = []
    for _, g in conds.groupby("trial"):
        k = max(1, round(TEST_SHARE * len(g)))
        test += list(rng.choice(g["experiment"].to_numpy(), size=k, replace=False))
    is_test = t["experiment"].isin(test)
    return t[~is_test], t[is_test], sorted(test)


def sample_series(pool, share, rng):
    """Whole UE series from the pool until `share` of its rows is reached."""
    series = pool.groupby(SERIES, sort=True).size().reset_index(name="n")
    series = series.iloc[rng.permutation(len(series))]
    take = series[series["n"].cumsum() <= share * len(pool)]
    if take.empty:
        take = series.iloc[:1]
    keys = take.set_index(SERIES).index
    return pool[pool.set_index(SERIES).index.isin(keys)]


def fill(df):
    """Forward-fill features within each UE series (reports are periodic)."""
    df = df.sort_values(SERIES + ["bin"]).copy()
    df[FEATURES] = df.groupby(SERIES, sort=False)[FEATURES].ffill()
    return df


def write(name, train, test, out, notes):
    d = os.path.join(out, name)
    os.makedirs(d, exist_ok=True)
    train, test = fill(train), fill(test)
    mean, std = train[FEATURES].mean(), train[FEATURES].std().replace(0, 1)
    cols = IDENTITY + FEATURES + [f"f_{c}" for c in FEATURES] + ["label_basis"] + LABELS
    summary = {}
    for part, df in [("train", train), ("test", test)]:
        df = df.copy()
        for c in FEATURES:
            df[f"f_{c}"] = ((df[c] - mean[c]) / std[c]).fillna(0.0)
        df[cols].to_csv(os.path.join(d, f"{part}.csv.gz"), index=False)
        summary[part] = {
            "rows": len(df),
            "ue_series": int(df.groupby(SERIES).ngroups),
            "experiments": sorted(df["experiment"].unique().tolist()),
            "sources": df["source"].value_counts().to_dict(),
            "starved_share": round(float(df["starved"].mean()), 4),
            "onsets": int(df["onset"].sum()),
            "onset_share": round(float(df["onset"].mean()), 5),
        }
    with open(os.path.join(d, "scaler.json"), "w") as fh:
        json.dump({"features": FEATURES, "mean": mean.round(6).to_dict(),
                   "std": std.round(6).to_dict(), "fit_on": "train"}, fh, indent=2)
    manifest = {"experiment": name, "notes": notes,
                "label": {"threshold": THRESHOLD, "hysteresis_bins": HYSTERESIS,
                          "onset_horizon_bins": HORIZON, "bin_s": 0.25,
                          "basis": {"simu5g": "dl_served_bytes / dl_offered_bytes",
                                    "tractor": "granted_prbs / requested_prbs"}},
                **summary}
    with open(os.path.join(d, "manifest.json"), "w") as fh:
        json.dump(manifest, fh, indent=2)
    print(f"  {name:<8} train {summary['train']['rows']:>8,} rows "
          f"({summary['train']['onsets']:>6,} onsets)   test {summary['test']['rows']:>8,} rows "
          f"({summary['test']['onsets']:>6,} onsets)")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--sim", required=True, help='glob of simulator canonical CSVs, quoted')
    ap.add_argument("--tractor", required=True, help="canonical TRACTOR CSV")
    ap.add_argument("--out", default="data/datasets")
    ap.add_argument("--exclude-configs", default="Tiny,Medium",
                    help="simulator configs left out (smoke tests)")
    args = ap.parse_args()
    rng = np.random.default_rng(SEED)

    print("Labelling simulator runs (bytes basis) ...")
    sim = load_sim(args.sim, set(args.exclude_configs.split(",")))
    print(f"  {sim['experiment'].nunique()} runs, {len(sim):,} rows, starved {sim['starved'].mean():.1%}")
    print("Labelling TRACTOR (PRB basis) ...")
    tr = load_tractor(args.tractor)
    print(f"  {tr['experiment'].nunique()} conditions, {len(tr):,} rows, starved {tr['starved'].mean():.1%}")

    sim_train, sim_test, sim_test_runs = split_sim(sim)
    pool, real_test, real_test_conds = split_tractor(tr, rng)
    print(f"\nE1 held-out runs: {sim_test_runs}")
    print(f"Shared TRACTOR test conditions: {real_test_conds}\n")

    write("E1", sim_train, sim_test, args.out, "sim -> sim, held-out seeds")
    write("E2", sim, real_test, args.out, "sim -> real, zero-shot")
    for share in E3_SHARES:
        real = sample_series(pool, share, rng)
        write(f"E3_k{int(share * 100):02d}", pd.concat([sim, real]), real_test, args.out,
              f"all sim + {share:.0%} of the TRACTOR pool rows (whole UE series)")
    write("E4", pool, real_test, args.out, "real -> real, upper bound")
    e5 = tr[tr["trial"].isin(["Trial1", "Trial2", "Trial3"])]
    write("E5", e5[e5["condition"].isin(E5_TRAIN)], e5[e5["condition"].isin(E5_TEST)],
          args.out, "real 4-6 UEs -> real 9-10 UEs (Trials 1-3)")
    print(f"\nwrote {args.out}/<experiment>/{{train,test}}.csv.gz, scaler.json, manifest.json")


if __name__ == "__main__":
    main()
