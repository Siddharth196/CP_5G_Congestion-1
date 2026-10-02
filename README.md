# CP_5G_Congestion — data pipeline

Everything up to the models: the OMNeT++/Simu5G scenario
(`UrbanCongestionCluster`), the conversion of its output and of the real
TRACTOR telemetry into one canonical schema, congestion labels, and the
train/test sets for experiments E1–E5.

```
results/<run>.vec + .sca ──export_all.sh──► results/canonical/<run>.csv ──┐
                                                                           ├─ build_datasets.py ─► data/datasets/E1 … E5
TRACTOR logs/Multi-UE ──harmonise_tractor.py──► data/canonical/tractor.csv ┘   (labels.py inside)
```

| File | What it is |
|---|---|
| `omnetpp.ini` | Scenario, traffic and result-recording configuration |
| `UrbanCongestionCluster.ned` | Network: 1 macro + 3 micro gNBs, 4 interference-only background cells, 50 UEs |
| `demo.xml` | IPv4 address plan for the configurator |
| `run` | Launcher that sets the NED path (fixes `Cannot resolve module type 'LteChannelControl'`) |
| `analysis/canonical.py` | The shared schema, constants and feature list |
| `analysis/parse_vectors2.py` | Simulator export → canonical CSV |
| `analysis/export_all.sh` | Export + parse every run in `results/` |
| `analysis/harmonise_tractor.py` | TRACTOR Multi-UE logs → canonical CSV |
| `analysis/labels.py` | Congestion state and onset labels (both sources) |
| `analysis/build_datasets.py` | Labelled, normalised train/test sets for E1–E5 |
| `analysis/tests.py` | Checks for all of the above |
| `CHANGES.md` | What was changed and why, with evidence and results |

Requires OMNeT++ 6.4.0, INET 4.6.0, Simu5G 1.4.3, and Python 3 with
`pandas` and `numpy`.

## 1. Run the simulation (Linux / WSL)

Inside an environment where OMNeT++ is set up and `INET_ROOT` / `SIMU5G_ROOT`
point at the INET and Simu5G checkouts (`opp_env shell` sets all of this):

```bash
make MODE=release INET_PROJ=$INET_ROOT SIMU5G_PROJ=$SIMU5G_ROOT
sh run -c Tiny -r 0                 # 5 s smoke test, a few seconds
sh run -c UrbanCongestion -r 0      # 120 s, 50 UEs, ~40 min
```

`INET_PROJ` / `SIMU5G_PROJ` can be left out if INET and Simu5G sit next to
this directory as `../inet-4.6.0` and `../simu5g-1.4.3`. `run` uses Cmdenv
unless you pass `-u Qtenv`. Every config has `repeat = 10` seeds; pick one
with `-r N`. Results go to `results/<Config>-<rep>.vec/.sca`. Scenarios:
`LightLoad`, `UrbanCongestion`, `GradualCongestion`, `HighMobility`,
`LowMobility`, `HeavyLoad` (CHANGES.md §14 compares them).

## 2. Build the datasets

```bash
# TRACTOR Multi-UE logs only (~77 MB of the 300 MB repository)
git clone --depth 1 --filter=blob:none --sparse https://github.com/genesys-neu/TRACTOR.git data/TRACTOR
git -C data/TRACTOR sparse-checkout set logs/Multi-UE

sh analysis/export_all.sh                       # simulator runs -> results/canonical/
python analysis/harmonise_tractor.py data/TRACTOR/logs/Multi-UE data/canonical/tractor.csv
python analysis/build_datasets.py --sim "results/canonical/*.csv" \
    --tractor data/canonical/tractor.csv --out data/datasets
python analysis/tests.py data/datasets          # should end with ALL PASSED
```

`export_all.sh` exports each `.vec` together with its `.sca` (the server
apps' parameters give each UE's offered load) and skips runs already
converted; pass run names (`sh analysis/export_all.sh results UrbanCongestion-0`)
to convert only those, e.g. while other runs are still being written. Each
step prints a report (fill rates, handovers, utilisation, label statistics);
read it before using the output. `data/` is not versioned.

## Shared features

Only quantities that exist in both sources, in the same units:

| Canonical | TRACTOR | Simu5G | Unit |
|---|---|---|---|
| `cell_util` | Σ over UEs of `sum_granted_prbs` ÷ 12,500 | Σ over the bin of `avgServedBlocksDl` (gNB) ÷ 12,500 | share of the cell's RBs |
| `dl_cqi` | `dl_cqi` | `averageCqiDl` (UE `nrPhy`) | 0–15 |
| `ul_sinr` | `ul_sinr` | `measuredSinrUl` (UE `nrChannelModel`) | dB |
| `dl_mbps` | `tx_brate downlink` | PDCP `sentPacketToUpperLayer` bytes (UE) | Mbit/s |
| `ul_mbps` | `rx_brate uplink` | `nrRlc.um` `sentPacketToLowerLayer` bytes (UE) | Mbit/s |
| `ul_error_pct` | `rx_errors uplink (%)` | `harqErrorRateUl` × 100 (UE `nrMac`) | % |

12,500 = 250 TTIs × 50 PRBs per 250 ms bin in both sources. Simu5G has no
per-UE grant, so TRACTOR's per-UE grants are summed per cell and bin.
Columns ending in `_simonly` (latency, RLC delay, …) have no TRACTOR
counterpart and are never transfer features; `corr(latency, rlc_delay)` is
1.0, so never use both.

## Labels

Same code for both sources (`labels.py`):

- **ratio** — supply ÷ demand per UE and bin. TRACTOR: `granted_prbs ÷
  requested_prbs` (downlink). Simulator: `dl_served_bytes ÷ dl_offered_bytes`
  (it emits no requested-PRB statistic; with constant-rate traffic the
  offered load is known exactly, and bits per PRB cancel in the ratio).
- **starved** — ratio < 0.5, entered and left only after 2 consecutive bins.
- **onset** — not starved now, starved 1 s (4 bins) later.

Only TRACTOR rows with `slicing_enabled == 0` are used. Why 0.5 and not a
fitted mixture, and how the alternatives were checked: CHANGES.md §16–17.

## Datasets (`data/datasets/<experiment>/`)

| Experiment | Train | Test |
|---|---|---|
| E1 | simulator runs | held-out seeds (highest seed of each config with ≥ 2) |
| E2 | all simulator runs | shared TRACTOR test conditions |
| E3_k05/k10/k20 | all simulator runs + 5/10/20 % of the TRACTOR pool | shared TRACTOR test conditions |
| E4 | TRACTOR pool | shared TRACTOR test conditions |
| E5 | TRACTOR Trials 1–3, 4–6 UEs | TRACTOR Trials 1–3, 9–10 UEs |

The shared TRACTOR test set is a fixed ~30 % of the conditions of each
trial, so E2, E3 and E4 are scored on identical data. Splits never cut a UE
series. Each folder has `train.csv.gz`, `test.csv.gz`, `scaler.json` and
`manifest.json` (rows, onsets, which runs/conditions). `f_<feature>` columns
are standardised with the train set's statistics; raw features are kept
alongside. `onset` is empty where the 1 s horizon runs past the end of a
series; drop those rows when training on onset.

## Building natively on Windows

Works with the official `omnetpp-6.4.0-windows-x86_64.7z`: unpack it and
run `mingwenv.cmd` once (it unpacks the clang64 toolchain and opens a shell).
Then, all in that one shell:

1. OMNeT++: `./configure && make MODE=release`
2. INET: `. setenv && make makefiles && make MODE=release`
3. Simu5G: `. setenv -f && make makefiles && make MODE=release`
4. This project: the same `make` and `sh run` commands as on Linux.
   `run` adds the INET and Simu5G DLL folders to `PATH` itself.

INET 4.6.0 needs one fix first, backported from INET master. Without it
Simu5G fails to link on Windows with *undefined symbol
inet::SharedDataManager / inet::CodeFragment* (Linux exports every symbol, so
it only shows up there):

- `src/inet/common/Compat.h`: declare `class INET_API SharedDataManager` and
  `class INET_API CodeFragment`
- `src/inet/common/INETDefs.h`: move `#include "inet/common/Compat.h"` below
  the `#define INET_API` block
- `src/inet/common/Compat.cc` and
  `src/inet/visualizer/base/QueueVisualizerBase.h`: include
  `inet/common/INETDefs.h` instead of `inet/common/Compat.h`

Then rebuild INET and Simu5G.
