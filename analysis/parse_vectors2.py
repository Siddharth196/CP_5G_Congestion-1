#!/usr/bin/env python3
"""
parse_vectors2.py  (v2 — statistic names verified against the model)

Converts an OMNeT++/Simu5G vector export into the canonical schema shared with
the TRACTOR dataset, so both feed the same model code.

What changed from parse_vectors.py
----------------------------------
  1. gNB rows are KEPT and joined onto UEs by serving cell. avgServedBlocksDl
     is a gNB-side statistic; the old UE-only filter deleted every row of it,
     which is why Avg_PRB_Usage was empty.
  2. Throughput comes from PDCP/RLC packetBytes counters summed per bin, not
     from rlcThroughputDl, which is a running average (total bytes / elapsed
     time) and cannot respond to congestion.
  3. Bins are 250 ms, matching TRACTOR's E2 reporting period.
  4. Statistic names verified against the Simu5G 1.4.3 sources:
       - macBufferSize      does not exist anywhere in Simu5G 1.4.3
       - requested-PRB      does not exist; only avgServedBlocks* is emitted
       - app-layer delay    is cbrFrameDelay: app[0] is Simu5G's CbrReceiver,
                            which has no endToEndDelay (that belongs to INET
                            apps such as UdpSink)
       - rcvdSinrDl         is on channelModel, not phy
       - measuredSinrUl     is emitted on the UE's channel model, not the gNB's
       - harqErrorRate*     is emitted on the UE's MAC, 0 = ACK / 1 = NACK
       - averageCqiDl       is on phy AND nrPhy (dual stack)
  5. Serving_Cell values in this model are 1..4, not 0..3.

Revised with the corrected omnetpp.ini
--------------------------------------
  6. UE events from the idle LTE stack are dropped. In standalone mode the NR
     stack carries everything; a single stray LTE servingCell = 0 at t=0 was
     enough to forward-fill an unknown cell until the UE's first handover and
     lose its cell allocation for all of those bins.
  7. granted_prbs stays EMPTY for the simulation. avgServedBlocksDl is the
     cell total and Simu5G has no per-UE grant, so it is written only to
     cell_granted_prbs (and cell_granted_prbs_ul). Filling the per-UE column
     with the cell total made it mean different things in the two sources.
  8. ul_mbps is SERVED uplink (RLC PDUs sent into grants), like TRACTOR's
     rx_brate. The old source, PDCP receivedPacketFromUpperLayer, is OFFERED
     load: it does not fall when the UE is starved. It is kept as
     ul_offered_mbps_simonly.
  9. dl_error_pct / ul_error_pct are percentages (NACK share x 100); they used
     to be fractions in a column named _pct.
 10. avgServedBlocksUl is emitted only when the UL scheduler runs, so a bin
     with no sample means 0 RBs, not missing data.
 11. servingCell is emitted every 100 ms, and 0 means "not attached" (e.g.
     at t=0, before the first association). It is treated as missing and
     forward-filled, never mapped to a cell. The idle LTE phy emits 0 for the
     whole run, which is what item 6 removes.

Usage
-----
    opp_scavetool export results/UrbanCongestion-0.vec -o raw.csv -F CSV-R
    python parse_vectors2.py raw.csv sim_canon.csv --run UrbanCongestion-0
"""

import argparse
import re
import sys

import numpy as np
import pandas as pd

BIN_S = 0.25                          # matches TRACTOR's 250 ms reporting
TTI_S = 0.001                         # numerology 0 -> 1 ms slots
TTIS_PER_BIN = int(BIN_S / TTI_S)     # 250
N_PRB = 50                            # **.numBands = 50
PRB_CEILING = TTIS_PER_BIN * N_PRB    # 12500, the ceiling TRACTOR also shows

# ---------------------------------------------------------------------------
# Statistic base name -> canonical KPI.
# Keys are matched against the part of the export's `name` column before the
# first colon, so both "averageCqiDl:vector" and
# "sentPacketToUpperLayer:vector(packetBytes)" resolve correctly.
# ---------------------------------------------------------------------------
SIGNALS = {
    # --- channel quality (UE side) ---
    "averageCqiDl":                 "dl_cqi",
    "averageCqiUl":                 "ul_cqi",
    "rcvdSinrDl":                   "dl_sinr_simonly",
    "measuredSinrUl":               "ul_sinr",
    "servingCell":                  "serving_cell",

    # --- scheduler allocation (gNB side) ---
    "avgServedBlocksDl":            "granted_prbs",
    "avgServedBlocksUl":            "granted_prbs_ul",

    # --- errors (UE side, 0/1 per HARQ feedback) ---
    "harqErrorRateDl":              "dl_error_pct",
    "harqErrorRateUl":              "ul_error_pct",
    "harqTxAttemptsDl":             "harq_attempts_simonly",

    # --- delay: simulation-only, no TRACTOR counterpart ---
    "cbrFrameDelay":                "latency_simonly",
    "rlcDelayDl":                   "rlc_delay_simonly",
    "macDelayDl":                   "mac_delay_simonly",

    # --- diagnosis only: confirm the running-average behaviour, never a feature ---
    "rlcThroughputDl":              "_rlc_rate_diag",
}

# Byte counters mean different things on different modules, so they are
# mapped by (statistic, module) instead. Each emission is one packet's size.
# Anything not listed (e.g. the gNB's PDCP counters) is ignored.
PACKET_SIGNALS = [
    # DL served: bytes PDCP delivers up to the UE's IP layer
    ("sentPacketToUpperLayer",       re.compile(r"\.ue\[\d+\]\.cellularNic\.pdcp$"),      "_dl_bytes"),
    # UL served: RLC PDUs the UE actually sends into its grants
    ("sentPacketToLowerLayer",       re.compile(r"\.ue\[\d+\]\.cellularNic\.nrRlc\.um$"), "_ul_bytes"),
    # UL offered: bytes the UE's IP layer hands to PDCP (unaffected by starvation)
    ("receivedPacketFromUpperLayer", re.compile(r"\.ue\[\d+\]\.cellularNic\.pdcp$"),      "_ul_offered_bytes"),
]
PACKET_STATS = {stat for stat, _, _ in PACKET_SIGNALS}

AGG = {
    "dl_cqi":                 "mean",
    "ul_cqi":                 "mean",
    "dl_sinr_simonly":        "mean",
    "ul_sinr":                "mean",
    "serving_cell":           "last",
    "granted_prbs":           "sum",   # summed over the bin, like TRACTOR
    "granted_prbs_ul":        "sum",
    "_dl_bytes":              "sum",
    "_ul_bytes":              "sum",
    "_ul_offered_bytes":      "sum",
    "dl_error_pct":           "mean",
    "ul_error_pct":           "mean",
    "harq_attempts_simonly":  "mean",
    "latency_simonly":        "mean",
    "rlc_delay_simonly":      "mean",
    "mac_delay_simonly":      "mean",
    "_rlc_rate_diag":         "last",
}

# Serving_Cell is 1..4 in this model, confirmed from dataset_urban_congestion.csv
# (MacNodeIds are handed out in NED declaration order; bgCells come after.)
CELL_ID_TO_NAME = {1: "macroGnb", 2: "microGnb1", 3: "microGnb2", 4: "microGnb3"}

UE_RE = re.compile(r"\.ue\[(\d+)\]")
GNB_RE = re.compile(r"\.((?:macro|micro)Gnb\d*)\b")
NR_RE = re.compile(r"\.(nrMac|nrPhy|nrRlc|nrChannelModel)\b")
LTE_RE = re.compile(r"\.(mac|phy|rlc|channelModel)\b")


def base_name(name):
    return str(name).split(":")[0]


def kpi_for(base, module):
    """Canonical KPI for one recorded vector, or None to skip it."""
    if base in PACKET_STATS:
        for stat, mod_re, kpi in PACKET_SIGNALS:
            if stat == base and mod_re.search(str(module)):
                return kpi
        return None
    return SIGNALS.get(base)


def classify(module):
    """Return (node_id, kind, stack) for a module path."""
    mod = str(module)
    if NR_RE.search(mod):
        stack = "nr"
    elif LTE_RE.search(mod):
        stack = "lte"
    else:
        stack = "n/a"          # pdcp, app: not stack-specific
    m = UE_RE.search(mod)
    if m:
        return f"UE_{m.group(1)}", "ue", stack
    g = GNB_RE.search(mod)
    if g:
        return g.group(1), "gnb", stack
    return mod, "other", stack


def load(path):
    keep = []
    for chunk in pd.read_csv(path, chunksize=20000, low_memory=False):
        if "type" not in chunk.columns:
            sys.exit("Expected a 'type' column. Export with -F CSV-R.")
        chunk["_base"] = chunk["name"].map(base_name)
        sel = chunk[(chunk["type"] == "vector")
                    & (chunk["_base"].isin(SIGNALS.keys() | PACKET_STATS))
                    & (chunk["vectime"].notna())]
        if not sel.empty:
            keep.append(sel)
    if not keep:
        sys.exit(
            "No matching vectors with data.\n"
            "Check that the run produced data at all: a .vec file of only a "
            "few KB contains declarations and nothing else.\n"
            "Run the named config explicitly, e.g.  -c UrbanCongestion"
        )
    return pd.concat(keep, ignore_index=True)


def to_floats(s):
    return np.array(str(s).split(), dtype=float)


def explode(raw):
    out = []
    for _, row in raw.iterrows():
        kpi = kpi_for(row["_base"], row.get("module", ""))
        if kpi is None:
            continue
        node, kind, stack = classify(row.get("module", ""))
        t = to_floats(row["vectime"])
        v = to_floats(row["vecvalue"])
        n = min(len(t), len(v))
        if n == 0:
            continue
        out.append(pd.DataFrame({"t": t[:n], "value": v[:n], "kpi": kpi,
                                 "node": node, "kind": kind, "stack": stack}))
    if not out:
        sys.exit("Vectors matched but contained no events.")
    ev = pd.concat(out, ignore_index=True)
    ev["bin"] = np.floor(ev["t"] / BIN_S).astype(int)
    return ev


def report_stacks(ev):
    """Which of the two parallel UE stacks actually fired."""
    ue = ev[ev["kind"] == "ue"]
    if ue.empty:
        return
    counts = ue.groupby("stack").size()
    print("\n  UE stack activity:")
    for s in ("lte", "nr"):
        print(f"    {s:<4} {counts.get(s, 0):>10,} events")
    if counts.get("nr", 0) > counts.get("lte", 0) * 10:
        print("    -> NR stack is live; the LTE modules are idle.")
    elif counts.get("lte", 0) > counts.get("nr", 0) * 10:
        print("    -> LTE stack is live; the NR modules are idle.")
    else:
        print("    -> both stacks active; check for double counting.")


def drop_idle_stack(ev):
    """
    In standalone mode (servingNodeId = 0) the UE's LTE stack is idle. Keep
    its events out of the per-UE KPIs: aggregating them alongside the NR
    stack mixes two different things, and a stray LTE servingCell = 0 is
    forward-filled as the UE's cell.
    """
    ue = ev["kind"] == "ue"
    if not (ue & (ev["stack"] == "nr")).any():
        return ev
    idle = ue & (ev["stack"] == "lte")
    if idle.any():
        print(f"  dropped {int(idle.sum()):,} UE events from the idle LTE stack")
    return ev[~idle]


def pivot(ev):
    pieces = []
    for kpi, how in AGG.items():
        sub = ev[ev["kpi"] == kpi]
        if sub.empty:
            continue
        g = (sub.groupby(["node", "kind", "bin"])["value"]
                .agg(how).reset_index().rename(columns={"value": kpi}))
        pieces.append(g)
    if not pieces:
        sys.exit("No KPI survived aggregation.")
    out = pieces[0]
    for p in pieces[1:]:
        out = out.merge(p, on=["node", "kind", "bin"], how="outer")
    return out


def derive_throughput(df):
    """
    PDCP/RLC counters emit packetBytes once per packet, so the per-bin SUM is
    already bytes-in-bin. No differencing: these are not cumulative counters.
    """
    for src, dst in [("_dl_bytes", "dl_mbps"), ("_ul_bytes", "ul_mbps"),
                     ("_ul_offered_bytes", "ul_offered_mbps_simonly")]:
        if src in df:
            df[dst] = (df[src].fillna(0) * 8.0) / (BIN_S * 1e6)
            df = df.drop(columns=[src])
    return df


def errors_to_pct(df):
    """harqErrorRate* is 0 (ACK) / 1 (NACK) per feedback; mean x 100 = %."""
    for c in ["dl_error_pct", "ul_error_pct"]:
        if c in df:
            df[c] = df[c] * 100.0
    return df


def join_cell_to_ue(df):
    """Attach gNB-side allocation to each UE via its serving cell."""
    ue = df[df["kind"] == "ue"].copy()
    gnb = df[df["kind"] == "gnb"].copy()

    cell_cols = [c for c in ["granted_prbs", "granted_prbs_ul",
                             "mac_delay_simonly"]
                 if c in gnb.columns]

    if gnb.empty or not cell_cols:
        print("  NOTE: no gNB-side rows. cell_granted_prbs will stay empty. "
              "Check that avgServedBlocksDl recorded on macroGnb/microGnb*.")
        ue["cell_granted_prbs"] = np.nan
        return ue

    cell = (gnb[["node", "bin"] + cell_cols]
            .rename(columns={"node": "cell",
                             **{c: f"cell_{c}" for c in cell_cols}}))

    if "serving_cell" in ue.columns:
        ue = ue.sort_values(["node", "bin"])
        # MacNodeId 0 = not attached: carry the last real cell instead
        ue["serving_cell"] = ue["serving_cell"].where(ue["serving_cell"] != 0)
        ue["serving_cell"] = ue.groupby("node")["serving_cell"].ffill()
        unknown = sorted({int(v) for v in ue["serving_cell"].dropna()}
                         - CELL_ID_TO_NAME.keys())
        if unknown:
            print(f"  WARNING: serving_cell values {unknown} have no gNB in "
                  f"CELL_ID_TO_NAME; those rows get no cell allocation.")
        ue["cell"] = ue["serving_cell"].map(
            lambda v: CELL_ID_TO_NAME.get(int(v), f"cell{int(v)}")
            if pd.notna(v) else np.nan)
    else:
        print("  NOTE: servingCell not recorded; assuming a single cell.")
        ue["cell"] = gnb["node"].iloc[0]

    ue = ue.merge(cell, on=["cell", "bin"], how="left")

    # UL allocation is emitted only when the UL scheduler runs: no sample in
    # a bin means no RBs, as long as the UE's cell is known.
    if "cell_granted_prbs_ul" in ue:
        known = ue["cell"].notna()
        ue.loc[known, "cell_granted_prbs_ul"] = (
            ue.loc[known, "cell_granted_prbs_ul"].fillna(0))

    # No per-UE allocation statistic exists, so the cell totals stay in the
    # cell_* columns and granted_prbs / granted_prbs_ul stay empty. TRACTOR's
    # per-UE grants are comparable only after summing over UEs per bin.
    # macDelayDl is sim-only and has no per-UE source either: carried as the
    # cell value.
    if "cell_mac_delay_simonly" in ue:
        ue["mac_delay_simonly"] = ue["cell_mac_delay_simonly"]
    return ue


def finalise(ue, run_name):
    ue = ue.rename(columns={"node": "ue"})
    ue["source"] = "simu5g"
    ue["experiment"] = run_name
    ue["segment"] = 0
    ue["slice"] = "unknown"

    # Not emitted by Simu5G; columns kept so the schema matches TRACTOR and
    # downstream code needs no special case.
    ue["requested_prbs"] = np.nan
    ue["granted_prbs"] = np.nan
    ue["dl_buffer_bytes"] = np.nan
    ue["ul_buffer_bytes"] = np.nan

    conc = ue.groupby("bin")["ue"].nunique().rename("n_ue_active")
    ue = ue.merge(conc, on="bin", how="left")

    ordered = ["source", "experiment", "segment", "ue", "bin",
               "n_ue_active", "slice", "serving_cell",
               "dl_buffer_bytes", "ul_buffer_bytes",
               "requested_prbs", "granted_prbs",
               "cell_granted_prbs", "cell_granted_prbs_ul",
               "dl_cqi", "ul_cqi", "ul_sinr", "dl_mbps", "ul_mbps",
               "ul_offered_mbps_simonly",
               "dl_error_pct", "ul_error_pct",
               "latency_simonly", "rlc_delay_simonly", "mac_delay_simonly",
               "dl_sinr_simonly", "harq_attempts_simonly", "_rlc_rate_diag"]
    for c in ordered:
        if c not in ue:
            ue[c] = np.nan
    return ue[ordered].sort_values(["bin", "ue"]).reset_index(drop=True)


def report(df):
    print(f"\nrows: {len(df):,}   UEs: {df['ue'].nunique()}   "
          f"bins: {df['bin'].nunique()}   max concurrency: "
          f"{int(df['n_ue_active'].max()) if df['n_ue_active'].notna().any() else 0}")

    print("\nfill rate per column (%):")
    for c in ["serving_cell", "cell_granted_prbs", "cell_granted_prbs_ul",
              "dl_cqi", "ul_sinr", "dl_mbps", "ul_mbps",
              "ul_offered_mbps_simonly", "dl_error_pct", "ul_error_pct",
              "latency_simonly"]:
        pct = 100 * df[c].notna().mean()
        flag = "   <-- EMPTY" if pct < 1 else ""
        print(f"  {c:<24} {pct:6.2f}{flag}")
    print("  granted_prbs             expected empty (no per-UE grant in Simu5G)")
    print("  dl_buffer_bytes          expected empty (no statistic in Simu5G)")
    print("  requested_prbs           expected empty (no statistic in Simu5G)")
    print("  mac_delay_simonly        expected empty (macDelayDl never emitted)")

    cells = df.dropna(subset=["serving_cell"]).sort_values(["ue", "bin"])
    if not cells.empty:
        ho = cells.groupby("ue")["serving_cell"].apply(
            lambda s: int((s.diff().fillna(0) != 0).sum()))
        print(f"\nhandovers per UE: median {ho.median():.0f}  max {ho.max()}  "
              f"total {ho.sum()}   cells: "
              f"{sorted(int(c) for c in cells['serving_cell'].unique())}")

    # one value per (cell, bin): UE rows repeat the cell total, which would
    # weight busy cells by how many UEs they serve
    g = df.drop_duplicates(["serving_cell", "bin"])["cell_granted_prbs"]
    if g.notna().any():
        mx = g.max()
        print(f"\ncell_granted_prbs max = {mx:,.1f}   (ceiling {PRB_CEILING:,})")
        if mx < PRB_CEILING / 20:
            print(f"  -> looks like a per-interval average rather than a "
                  f"per-bin sum. Scale by the number of emissions per bin "
                  f"(up to {TTIS_PER_BIN}) before comparing with TRACTOR.")
        elif mx > PRB_CEILING * 1.5:
            print("  -> above the ceiling. Check for double counting across "
                  "carriers or stacks.")
        else:
            print("  -> scale matches TRACTOR summed over UEs per bin.")
        util = g / PRB_CEILING
        print(f"  utilisation per cell-bin  p50 {util.quantile(.5):.3f}  "
              f"p95 {util.quantile(.95):.3f}  max {util.max():.3f}")
        if util.max() < 0.5:
            print("  WARNING: the cell never approaches saturation in this "
                  "run. No congestion to predict.")

    both = df[["ul_mbps", "ul_offered_mbps_simonly"]].dropna()
    both = both[both["ul_offered_mbps_simonly"] > 0]
    if not both.empty:
        ratio = both["ul_mbps"] / both["ul_offered_mbps_simonly"]
        print(f"\nUL served / offered per UE-bin: p5 {ratio.quantile(.05):.3f}  "
              f"p50 {ratio.median():.3f}")

    if df["_rlc_rate_diag"].notna().any():
        s = df.groupby("ue")["_rlc_rate_diag"].apply(
            lambda x: x.std() / x.mean() if x.mean() else np.nan)
        print(f"\nrlcThroughputDl per-UE coefficient of variation "
              f"(median {s.median():.3f})")
        if s.median() < 0.3:
            print("  -> confirms running-average behaviour. Correct to have "
                  "excluded it as a feature.")

    if {"latency_simonly", "rlc_delay_simonly"}.issubset(df.columns):
        both = df[["latency_simonly", "rlc_delay_simonly"]].dropna()
        if len(both) > 100:
            r = both.corr().iloc[0, 1]
            print(f"\ncorr(latency, rlc_delay) = {r:.4f}")
            if r > 0.99:
                print("  -> still collinear. Keep only one, and never both "
                      "as model inputs.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("raw_csv")
    ap.add_argument("out_csv")
    ap.add_argument("--run", default="sim")
    args = ap.parse_args()

    print(f"Loading {args.raw_csv} ...")
    raw = load(args.raw_csv)
    print(f"  {len(raw)} vector series matched")

    print("Exploding events ...")
    ev = explode(raw)
    print(f"  {len(ev):,} events across {ev['kpi'].nunique()} KPIs")
    print(f"  node kinds: {ev.groupby('kind')['node'].nunique().to_dict()}")
    report_stacks(ev)
    ev = drop_idle_stack(ev)

    print(f"\nBinning at {BIN_S * 1000:.0f} ms ...")
    wide = pivot(ev)

    print("Deriving throughput from PDCP/RLC byte counters ...")
    wide = derive_throughput(wide)
    wide = errors_to_pct(wide)

    print("Joining cell-level allocation onto UEs ...")
    ue = join_cell_to_ue(wide)

    out = finalise(ue, args.run)
    report(out)

    out.to_csv(args.out_csv, index=False)
    print(f"\nwrote {args.out_csv}")


if __name__ == "__main__":
    main()
