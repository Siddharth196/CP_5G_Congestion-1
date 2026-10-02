#!/usr/bin/env python3
"""
tests.py — checks for the data pipeline. No test framework needed:

    python analysis/tests.py                 # unit tests (seconds)
    python analysis/tests.py data/datasets   # + checks on built datasets

1. parser      parse_vectors2.py on a synthetic CSV-R export with known
               ground truth (handover, idle LTE stack, starved uplink, ...)
2. labels      hysteresis and onset on hand-made series
3. harmonise   bin alignment, cell sums and byte derivation on tiny TRACTOR files
4. datasets    no UE series in both train and test, E2/E3/E4 share one test
               set, E3 real rows come from the pool, scaling fitted on train
"""

import csv
import glob
import json
import os
import subprocess
import sys
import tempfile

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from canonical import COLUMNS, FEATURES  # noqa: E402
from labels import label  # noqa: E402

FAILS = []


def check(name, cond):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")
    if not cond:
        FAILS.append(name)


# --------------------------------------------------------------------------- 1
def synthetic_export(path):
    """4 s, 2 UEs, 2 gNBs; vector names exactly as the corrected ini records."""
    N, END = "UrbanCongestionCluster", 4.0
    rows = [{"type": "runattr", "attrname": "configname", "attrvalue": "Synthetic"},
            {"type": "runattr", "attrname": "repetition", "attrvalue": "3"}]

    def vec(module, name, t, v):
        rows.append({"type": "vector", "module": f"{N}.{module}", "name": name,
                     "vectime": " ".join(f"{x:.6f}" for x in t),
                     "vecvalue": " ".join(f"{x:g}" for x in v)})

    def series(start, step, value):
        t = np.arange(start, END, step)
        return t, (value(np.arange(len(t))) if callable(value) else np.full(len(t), value))

    def par(module, name, value):
        rows.append({"type": "param", "module": f"{N}.{module}", "name": name, "value": value})

    vec("macroGnb.cellularNic.mac", "avgServedBlocksDl:vector", *series(0.0005, 0.001, 30))
    vec("microGnb1.cellularNic.mac", "avgServedBlocksDl:vector", *series(0.0005, 0.001, 10))
    t, v = series(0.0005, 0.001, 5)
    vec("macroGnb.cellularNic.mac", "avgServedBlocksUl:vector", t[t < 2.0], v[t < 2.0])
    t = np.concatenate([[0.0], np.arange(0.0, END, 0.1)])        # 0 = not attached at t=0
    vec("ue[0].cellularNic.nrPhy", "servingCell:vector", t, np.where(t < 2.1, 1, 2) * (np.arange(len(t)) > 0))
    vec("ue[0].cellularNic.phy", "servingCell:vector", t, np.zeros(len(t)))   # idle LTE stack
    vec("ue[1].cellularNic.nrPhy", "servingCell:vector", t, 1 * (np.arange(len(t)) > 0))
    vec("ue[0].cellularNic.nrPhy", "averageCqiDl:vector", *series(0.02, 0.04, 14))
    vec("ue[1].cellularNic.nrPhy", "averageCqiDl:vector", *series(0.02, 0.04, 9))
    vec("ue[0].cellularNic.nrChannelModel[0]", "measuredSinrUl:vector", *series(0.02, 0.04, 12))
    vec("ue[0].cellularNic.pdcp", "sentPacketToUpperLayer:vector(packetBytes)", *series(0.001, 0.005, 1400))
    vec("ue[0].cellularNic.pdcp", "receivedPacketFromUpperLayer:vector(packetBytes)", *series(0.002, 0.02, 100))
    vec("ue[0].cellularNic.nrRlc.um", "sentPacketToLowerLayer:vector(packetBytes)", *series(0.003, 0.04, 100))
    vec("ue[0].cellularNic.nrMac", "harqErrorRateDl:vector", *series(0.004, 0.008, lambda i: i % 2))
    vec("ue[0].app[0]", "cbrFrameDelay:vector", *series(0.011, 0.005, 0.010))
    vec("ue[0].app[0]", "cbrReceivedBytes:vector", *series(0.011, 0.005, 700))   # half of 1400 B / 5 ms
    for k, v in {"typename": '"simu5g.apps.cbr.CbrSender"', "destAddress": '"ue[0]"',
                 "packetSize": "1400B", "samplingTime": "0.005s", "startTime": "0.001s",
                 "finishTime": "0s"}.items():
        par("server.app[0]", k, v)
    cols = ["run", "type", "module", "name", "attrname", "attrvalue", "value", "vectime", "vecvalue"]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})


def test_parser(tmp):
    print("1. parser")
    raw, out = os.path.join(tmp, "raw.csv"), os.path.join(tmp, "out.csv")
    synthetic_export(raw)
    p = subprocess.run([sys.executable, os.path.join(HERE, "parse_vectors2.py"), raw, out],
                       capture_output=True, text=True)
    check("parser exits cleanly", p.returncode == 0)
    if p.returncode:
        print(p.stderr[-2000:])
        return
    d = pd.read_csv(out)
    u0, u1 = d[d.ue == "UE_0"].set_index("bin"), d[d.ue == "UE_1"].set_index("bin")
    before, after = range(0, 8), range(8, 16)
    check("canonical column order", list(d.columns) == COLUMNS)
    check("identity from run attributes", (d.experiment == "Synthetic-3").all() and (d.trial == 3).all())
    check("idle LTE servingCell=0 ignored, cell 1 then 2",
          (u0.loc[before, "serving_cell"] == 1).all() and (u0.loc[after, "serving_cell"] == 2).all())
    check("cell DL allocation joined via serving cell",
          (u0.loc[before, "cell_granted_prbs"] == 7500).all() and (u0.loc[after, "cell_granted_prbs"] == 2500).all())
    check("cell_util = cell_granted_prbs / 12,500", np.allclose(u0["cell_util"], u0["cell_granted_prbs"] / 12500))
    check("missing UL allocation counts as 0", (u0.loc[after, "cell_granted_prbs_ul"] == 0).all())
    check("per-UE granted_prbs stays empty", d["granted_prbs"].isna().all())
    check("dl_mbps from PDCP = 2.24", np.allclose(u0["dl_mbps"], 2.24))
    check("ul_mbps is served (~0.02), not offered (~0.04)",
          abs(u0["ul_mbps"].mean() - 0.02) < 0.002 and abs(u0["ul_offered_mbps_simonly"].mean() - 0.04) < 0.002)
    check("dl_offered_bytes from app parameters (50 x 1400 B)", (u0.loc[1:14, "dl_offered_bytes"] == 70000).all())
    check("dl_served_bytes from cbrReceivedBytes (half)", (u0.loc[1:14, "dl_served_bytes"] == 35000).all())
    check("HARQ error as percent (~50)", abs(u0["dl_error_pct"].mean() - 50) < 2)
    check("latency from cbrFrameDelay", np.allclose(u0["latency_simonly"], 0.010))
    check("traffic class from packet size", (u0["traffic_class"] == "embb").all())
    check("UE without traffic has dl_mbps 0", (u1["dl_mbps"] == 0).all())


# --------------------------------------------------------------------------- 2
def test_labels():
    print("2. labels")
    # supply/demand pattern for one UE: ok ok BAD ok BAD BAD BAD ok ok BAD BAD ok ok ok
    starved = [0, 0, 1, 0, 1, 1, 1, 0, 0, 1, 1, 0, 0, 0]
    n = len(starved)
    df = pd.DataFrame({"experiment": "x", "ue": "a", "bin": range(n),
                       "requested_prbs": 100.0,
                       "granted_prbs": [30.0 if s else 100.0 for s in starved]})
    out = label(df, "prb", threshold=0.5, hyst=2, horizon=2)
    # entry needs 2 starved bins in a row, exit needs 2 ok bins in a row
    expect_state = [0, 0, 0, 0, 0, 1, 1, 1, 0, 0, 1, 1, 0, 0]
    check("hysteresis on entry and exit", out["starved"].tolist() == expect_state)
    onset = out["onset"].tolist()
    check("onset = not starved now, starved 2 bins later",
          onset[3] == 1 and onset[8] == 1 and onset[0] == 0 and onset[5] == 0)
    check("onset NaN where horizon leaves the series", np.isnan(onset[-1]) and np.isnan(onset[-2]))
    nodemand = df.assign(requested_prbs=0.0)
    check("no demand is never starved", label(nodemand, "prb")["starved"].sum() == 0)
    gap = df.drop(index=[4]).reset_index(drop=True)          # bin 4 missing
    g = label(gap, "prb", threshold=0.5, hyst=2, horizon=2)
    check("a gap in bins resets the state machine", g.loc[g.bin == 5, "starved"].item() == 0)
    check("onset NaN when bin t+H is missing", np.isnan(g.loc[g.bin == 2, "onset"].item()))


# --------------------------------------------------------------------------- 3
def test_harmonise(tmp):
    print("3. harmonise")
    from harmonise_tractor import harmonise
    root = os.path.join(tmp, "Multi-UE")
    cond = os.path.join(root, "Trial2", "multi2")
    os.makedirs(cond)
    header = ("Timestamp,num_ues,IMSI,RNTI,,slicing_enabled,slice_id,slice_prb,power_multiplier,"
              "scheduling_policy,,dl_mcs,dl_n_samples,dl_buffer [bytes],tx_brate downlink [Mbps],"
              "tx_pkts downlink,tx_errors downlink (%),dl_cqi,,ul_mcs,ul_n_samples,ul_buffer [bytes],"
              "rx_brate uplink [Mbps],rx_pkts uplink,rx_errors uplink (%),ul_rssi,ul_sinr,phr,,"
              "sum_requested_prbs,sum_granted_prbs,,dl_pmi,dl_ri,ul_n,ul_turbo_iters")
    for imsi, offset, slicing in [(1010123456002, 3, [0, 0, 0, 1]), (1010123456003, 130, [0, 0, 0, 0])]:
        with open(os.path.join(cond, f"{imsi}_metrics.csv"), "w") as f:
            f.write(header + "\n")
            for k in range(4):
                ts = 1_000_000 + offset + 250 * k
                buf = 1000 * k
                f.write(f"{ts},2,{imsi},70,,{slicing[k]},0,0,1,0,,5,10,{buf},0.032,4,0,9,,5,10,0,0.1,1,2,0,12,30,,"
                        f"{100 + k},{50 + k},,0,0,0,1\n")
    with open(os.path.join(cond, "output_multi2.txt"), "w") as f:
        f.write("Using trace: ./raw/URLLC_06_12.csv for 1010123456002\n"
                "Using trace: ./raw/embb_2.csv for 1010123456003\n")
    t = harmonise(root)
    check("slicing-on row removed", len(t) == 7)
    check("UEs aligned on common bins", sorted(t["bin"].unique().tolist()) == [0, 1, 2, 3])
    b1 = t[t.bin == 1]
    check("cell_granted_prbs sums both UEs", (b1["cell_granted_prbs"] == 51 + 51).all())
    check("n_ue_active counts both UEs", (b1["n_ue_active"] == 2).all())
    check("traffic class from trace list", set(t["traffic_class"]) == {"urllc", "embb"})
    check("UE ids are prefixed strings", set(t["ue"]) == {"UE_1010123456002", "UE_1010123456003"})
    s = t[(t.ue == "UE_1010123456003") & (t.bin == 2)].iloc[0]
    check("dl_served_bytes = Mbps x 250 ms", abs(s["dl_served_bytes"] - 1000) < 1e-6)
    check("dl_offered_bytes = served + buffer growth", abs(s["dl_offered_bytes"] - 2000) < 1e-6)


# --------------------------------------------------------------------------- 4
def test_datasets(root):
    print(f"4. datasets in {root}")
    exps = sorted(d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d)))
    check("all experiments present",
          {"E1", "E2", "E3_k05", "E3_k10", "E3_k20", "E4", "E5"} <= set(exps))
    key = lambda df: set(zip(df.experiment, df.ue))
    tests = {}
    for e in exps:
        tr = pd.read_csv(os.path.join(root, e, "train.csv.gz"), low_memory=False)
        te = pd.read_csv(os.path.join(root, e, "test.csv.gz"), low_memory=False)
        tests[e] = te
        check(f"{e}: no UE series in both train and test", not (key(tr) & key(te)))
        z = tr[[f"f_{c}" for c in FEATURES]]
        check(f"{e}: train features standardised (|mean| < 0.05, std ~ 1)",
              (z.mean().abs() < 0.05).all() and ((z.std() - 1).abs() < 0.1).all())
        check(f"{e}: labels binary", set(tr["starved"].unique()) <= {0, 1}
              and set(tr["onset"].dropna().unique()) <= {0, 1})
        with open(os.path.join(root, e, "manifest.json")) as fh:
            m = json.load(fh)
        check(f"{e}: manifest row counts match files",
              m["train"]["rows"] == len(tr) and m["test"]["rows"] == len(te))
        if e == "E2":
            check("E2: trains on simulator only, tests on TRACTOR only",
                  set(tr.source) == {"simu5g"} and set(te.source) == {"tractor"})
        if e == "E4":
            pool = key(tr)
        if e == "E5":
            check("E5: train 4-6 UE conditions, test 9-10",
                  set(tr.condition) <= {"multi4", "multi5", "multi6"} and set(te.condition) <= {"multi9", "multi10"})
    shared = [tests[e] for e in ["E2", "E3_k05", "E3_k10", "E3_k20", "E4"]]
    check("E2/E3/E4 share an identical test set", all(key(t) == key(shared[0]) for t in shared))
    for e in ["E3_k05", "E3_k10", "E3_k20"]:
        tr = pd.read_csv(os.path.join(root, e, "train.csv.gz"), usecols=["source", "experiment", "ue"],
                         dtype={"ue": str})
        check(f"{e}: real rows come from the E4 pool", key(tr[tr.source == "tractor"]) <= pool)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        test_parser(tmp)
        test_labels()
        test_harmonise(tmp)
    if len(sys.argv) > 1:
        test_datasets(sys.argv[1])
    print(f"\n{'ALL PASSED' if not FAILS else f'{len(FAILS)} FAILED: ' + '; '.join(FAILS)}")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
