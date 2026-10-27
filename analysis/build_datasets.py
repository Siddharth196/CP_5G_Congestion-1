#!/usr/bin/env python3
"""
build_datasets.py — labelled, normalised train/test sets for experiments E1-E6

    python analysis/build_datasets.py --sim "results/canonical/*.csv" \
        --tractor data/canonical/tractor.csv --out data/datasets \
        [--srsran "data/canonical/srsran/*.csv"]

Inputs are canonical tables (parse_vectors2.py / harmonise_tractor.py). Both
sources are labelled by labels.py with the same threshold, hysteresis and
horizon: TRACTOR on granted/requested PRBs (the true label), the simulator on
served/offered DL bytes (it emits no requested-PRB statistic). srsRAN
testbed tables, when given, are labelled on bytes like the simulator.

Experiments (from the project plan)
  E1  sim -> sim            train: sim runs except held-out seeds; test: held-out seeds
  E2  sim -> real           train: all sim; test: TRACTOR test conditions (zero-shot)
  E3  sim + k% real -> real train: all sim + k% of the TRACTOR pool (whole UE series);
                            test: TRACTOR test conditions; k = 5, 10, 20
  E4  real -> real          train: TRACTOR pool; test: TRACTOR test conditions
  E5  real 4-6 UEs -> 9-10  train: Trials 1-3 multi4-6; test: Trials 1-3 multi9-10
  E6  sim -> srsRAN         train: all sim; test: the srsRAN testbed (zero-shot);
                            only with --srsran
  E6_matched                train: the OneCell* testbed-matched sim runs only;
                            test: srsRAN; only with --srsran and OneCell* runs

OneCell* runs (one 52-PRB cell, 3-4 UEs) never enter E1-E5, so those stay
identical whether or not the matched runs exist.

E2, E3 and E4 share one TRACTOR test set, a fixed ~30 % of conditions per
trial, so E4 - E2 is measured on identical data. Splits never cut through a
UE series (sim: whole runs; TRACTOR: whole conditions or UE series).

Each experiment directory holds train.csv.gz, test.csv.gz, scaler.json and
manifest.json. f_<feature> columns are the shared features standardised with
the TRAIN set's mean and std (missing values forward-filled within each UE
series, then set to the train mean, i.e. 0 after scaling). Rows whose onset
horizon falls outside the series have onset = NaN; drop them when training
on onset.

Every manifest records prb_capacity_per_bin per source: the divisor its
cell_util was computed with, recovered from cell_granted_prbs / cell_util.
A source scaled with a capacity outside canonical.PRB_CAPACITY (e.g. a
52-PRB srsRAN cell divided by 12,500) stops the build.
"""

import argparse
import glob
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from canonical import FEATURES, IDENTITY, PRB_CAPACITY  # noqa: E402
from labels import HORIZON, HYSTERESIS, SERIES, THRESHOLD, label  # noqa: E402

SEED = 7
TEST_SHARE = 0.3
E3_SHARES = [0.05, 0.10, 0.20]
E5_TRAIN = {"multi4", "multi5", "multi6"}
E5_TEST = {"multi9", "multi10"}
LABELS = ["ratio", "starved", "onset"]
ONECELL = "OneCell"       # config-name prefix of the testbed-matched sim runs
SRSRAN_REQUIRED = IDENTITY + FEATURES + ["dl_offered_bytes", "dl_served_bytes", "cell_granted_prbs"]


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
    return label(sim, "bytes", threshold=THRESHOLD).assign(label_basis="bytes")


def load_srsran(pattern):
    """srsRAN testbed tables, delivered by the integration team in the canonical schema."""
    files = sorted(glob.glob(pattern))
    if not files:
        sys.exit(f"No srsRAN tables matched {pattern}")
    s = pd.concat([pd.read_csv(f, low_memory=False, dtype={"ue": str}) for f in files],
                  ignore_index=True)
    missing = [c for c in SRSRAN_REQUIRED if c not in s]
    if missing:
        sys.exit(f"srsRAN tables lack canonical columns {missing} (see HANDOFF.md, E6).")
    if set(s["source"]) != {"srsran"}:
        sys.exit(f"srsRAN tables must have source == 'srsran', found {sorted(set(s['source']))}")
    if (s["slicing_enabled"] != 0).any():
        sys.exit("srsRAN tables contain slicing_enabled != 0 rows.")
    if s["dl_offered_bytes"].isna().all() or s["dl_served_bytes"].isna().all():
        sys.exit("srsRAN tables need dl_offered_bytes (UDP rate x 0.25 s) and "
                 "dl_served_bytes (received at the UE's app) for the bytes label.")
    if s.duplicated(["experiment", "ue", "bin"]).any():
        sys.exit("srsRAN tables have more than one row per (experiment, ue, bin).")
    for c in s.columns:
        if c not in IDENTITY + ["label_basis"] and s[c].dtype == object:
            s[c] = pd.to_numeric(s[c], errors="coerce")
    return label(s, "bytes", threshold=THRESHOLD).assign(label_basis="bytes")


def capacity(df):
    """
    PRB capacity per bin each source's cell_util was scaled with, as
    {source: int, or a sorted list if the source mixes cells}. Raises
    ValueError for a capacity canonical.PRB_CAPACITY does not allow.
    """
    out = {}
    for src, g in df.groupby("source"):
        ok = g["cell_util"].gt(0) & g["cell_granted_prbs"].notna()
        caps = np.unique(np.rint(g.loc[ok, "cell_granted_prbs"] / g.loc[ok, "cell_util"])).astype(int).tolist()
        bad = set(caps) - PRB_CAPACITY.get(src, set())
        if bad:
            raise ValueError(f"source {src}: cell_util was divided by {sorted(bad)}; "
                             f"allowed {sorted(PRB_CAPACITY.get(src, set()))} (250 ms x PRBs)")
        out[src] = caps[0] if len(caps) == 1 else caps or None   # None: cell never busy
    return out


def load_tractor(path):
    t = pd.read_csv(path, low_memory=False)
    if (t["slicing_enabled"] != 0).any():
        sys.exit("TRACTOR table contains slicing_enabled != 0 rows; re-run "
                 "harmonise_tractor.py without --keep-slicing.")
    return label(t, "prb", threshold=THRESHOLD).assign(label_basis="prb")


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
    caps = capacity(pd.concat([train, test]))
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
                                    "tractor": "granted_prbs / requested_prbs",
                                    "srsran": "dl_served_bytes / dl_offered_bytes"}},
                "prb_capacity_per_bin": caps,
                **summary}
    with open(os.path.join(d, "manifest.json"), "w") as fh:
        json.dump(manifest, fh, indent=2)
    print(f"  {name:<8} train {summary['train']['rows']:>8,} rows "
          f"({summary['train']['onsets']:>6,} onsets)   test {summary['test']['rows']:>8,} rows "
          f"({summary['test']['onsets']:>6,} onsets)")


def main():
    global THRESHOLD
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--sim", required=True, help='glob of simulator canonical CSVs, quoted')
    ap.add_argument("--tractor", required=True, help="canonical TRACTOR CSV")
    ap.add_argument("--out", default="data/datasets")
    ap.add_argument("--exclude-configs", default="Tiny,Medium",
                    help="simulator configs left out (smoke tests)")
    ap.add_argument("--threshold", type=float, default=THRESHOLD,
                    help="starvation threshold on the ratio (sensitivity runs: 0.3, 0.7)")
    ap.add_argument("--srsran", default=None,
                    help="glob of srsRAN testbed canonical CSVs, quoted; adds E6 (and E6_matched)")
    args = ap.parse_args()
    THRESHOLD = args.threshold
    rng = np.random.default_rng(SEED)

    print("Labelling simulator runs (bytes basis) ...")
    sim = load_sim(args.sim, set(args.exclude_configs.split(",")))
    matched = sim["condition"].astype(str).str.startswith(ONECELL)
    onecell, sim = sim[matched], sim[~matched]
    if len(onecell):
        print(f"  {onecell['experiment'].nunique()} {ONECELL}* runs kept apart for E6_matched")
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
    if args.srsran:
        print("\nLabelling srsRAN (bytes basis) ...")
        sr = load_srsran(args.srsran)
        print(f"  {sr['experiment'].nunique()} experiments, {len(sr):,} rows, starved {sr['starved'].mean():.1%}")
        write("E6", sim, sr, args.out, "sim -> srsRAN testbed, zero-shot")
        if len(onecell):
            write("E6_matched", onecell, sr, args.out,
                  f"{ONECELL}* testbed-matched sim runs -> srsRAN testbed, zero-shot")
    print(f"\nwrote {args.out}/<experiment>/{{train,test}}.csv.gz, scaler.json, manifest.json")


if __name__ == "__main__":
    main()
