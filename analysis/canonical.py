"""
Canonical schema shared by the simulator (parse_vectors2.py) and TRACTOR
(harmonise_tractor.py). One row per UE per 250 ms bin.

Column groups
-------------
identity       where the row comes from and who it is about
FEATURES       the six model inputs that exist in BOTH sources, in the same
               units (see the mapping table in README.md)
label inputs   per-UE demand and supply, used by labels.py:
                 requested_prbs / granted_prbs   TRACTOR only (true label)
                 dl_offered_bytes / dl_served_bytes   both sources
extras         source-specific columns, never model inputs for transfer
"""

BIN_S = 0.25                          # TRACTOR's E2 reporting period
TTI_S = 0.001                         # numerology 0 -> 1 ms slots in both sources
TTIS_PER_BIN = int(BIN_S / TTI_S)     # 250
N_PRB = 50                            # 10 MHz carrier in both sources
PRB_CEILING = TTIS_PER_BIN * N_PRB    # 12,500 RBs per cell per bin

IDENTITY = [
    "source",           # "simu5g" | "tractor"
    "experiment",       # sim: run name (UrbanCongestion-0); TRACTOR: Trial2/multi6
    "trial",            # sim: seed; TRACTOR: Trial0..Trial3
    "condition",        # sim: config name; TRACTOR: condition folder (multi6, embb2, ...)
    "ue",               # sim: UE_<index>; TRACTOR: UE_<IMSI> (always a string)
    "bin",              # 250 ms bin index from the start of the experiment
    "t_s",              # bin start time in seconds
    "n_ue_active",      # UEs with a row in this experiment and bin
    "traffic_class",    # embb | mmtc | urllc | unknown
    "slicing_enabled",  # 0 for every row used in the experiments
    "serving_cell",     # sim: gNB MacNodeId 1..4; TRACTOR: always 1 (single cell)
]

# Shared model inputs. cell_util is cell_granted_prbs / PRB_CEILING: Simu5G
# only reports the cell's allocation, so TRACTOR's per-UE grants are summed
# over all UEs in the cell before dividing.
FEATURES = ["cell_util", "dl_cqi", "ul_sinr", "dl_mbps", "ul_mbps", "ul_error_pct"]

LABEL_INPUTS = ["requested_prbs", "granted_prbs", "dl_offered_bytes", "dl_served_bytes"]

EXTRAS = [
    "cell_granted_prbs",        # raw form of cell_util
    "cell_granted_prbs_ul",     # sim only
    "dl_buffer_bytes", "ul_buffer_bytes",   # TRACTOR only
    "ul_offered_mbps_simonly",
    "dl_error_pct",             # dead in TRACTOR (always 0)
    "latency_simonly", "rlc_delay_simonly", "mac_delay_simonly",
    "dl_sinr_simonly", "harq_attempts_simonly",
]

COLUMNS = IDENTITY + FEATURES + LABEL_INPUTS + EXTRAS


def traffic_class(name):
    """Map a trace or condition name (embb2, URLLC_06_12, urll_2, ...) to a class."""
    n = str(name).lower()
    for prefix, cls in [("embb", "embb"), ("mmtc", "mmtc"), ("urll", "urllc")]:
        if n.startswith(prefix):
            return cls
    return "unknown"


def to_canonical(df):
    """Add any missing canonical columns (as NaN) and order them."""
    df = df.copy()
    if "cell_granted_prbs" in df:
        df["cell_util"] = df["cell_granted_prbs"] / PRB_CEILING
    for c in COLUMNS:
        if c not in df:
            df[c] = float("nan")
    return df[COLUMNS].sort_values(["experiment", "ue", "bin"]).reset_index(drop=True)
