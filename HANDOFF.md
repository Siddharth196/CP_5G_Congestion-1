# Handoff: Telecom team → ML team

## The border in one line

**The folders in `data/datasets/` are the border**: seven for E1–E5, plus
E6 and E6_matched once the srsRAN testbed data is in. The telecom team is
responsible for everything that produces them. The ML team is responsible for
everything that reads them. Neither side edits the other side's files: if the
ML team needs different data, it asks, and the telecom team rebuilds.

```
TELECOM SIDE                                    |  ML SIDE
simulator → .vec/.sca → canonical tables        |  windows → models → experiments
TRACTOR logs → canonical table                  |  → metrics → results tables/plots
shared KPIs + label + splits + scaling          |
            ↓                                   |
     data/datasets/E1 … E5   ←──── the border ────→  read-only input
```

## Who owns what

| Topic | Owner | Notes |
|---|---|---|
| Simulator config, runs, new scenarios, more seeds | Telecom | `omnetpp.ini`, `UrbanCongestionCluster.ned` |
| Parsing the simulator and TRACTOR into one schema | Telecom | `parse_vectors2.py`, `harmonise_tractor.py`, `canonical.py` |
| Which six KPIs are inputs, and their units | Telecom | Report §4 |
| The label (threshold 0.5, hysteresis 2, onset 1 s ahead) | Telecom | `labels.py` |
| Train/test splits and train-set scaling | Telecom | `build_datasets.py`, checked by `tests.py` |
| Label variants (threshold 0.3 / 0.7, other horizons) | Telecom builds, ML asks | Rebuilt with `labels.py --threshold` |
| Windowing (10 bins = 2.5 s of history) | ML | See "How to use the data" |
| Baselines, LSTM, TCN, GraphSAGE, hybrid | ML | Report §6.6 |
| Training, tuning, seeds, calibration | ML | Tune only on the training set (see the rules below) |
| Metrics, plots, results tables (E1–E5) | ML | Report §6.7 |
| Ablations: per-source scaling, removing `ul_error_pct` | ML | Use the raw columns; recompute the scaling yourselves |
| Explaining results in telecom terms (why the model fails on X) | Both | Telecom explains the network side |
| Inference time against the near-RT RIC budget | ML measures, telecom states the budget | Budget: under 250 ms per decision |
| Report: Stage 1 sections, limitations | Telecom | |
| Report: Stage 2 results | ML | |

## What the ML team receives

`data/datasets/<E>/`, one folder per experiment (E1, E2, E3_k05, E3_k10,
E3_k20, E4, E5). Each folder holds:

- `train.csv.gz` and `test.csv.gz`: one row per user per 250 ms interval.
- `scaler.json`: the mean and standard deviation used for the `f_*` columns,
  fitted on train only.
- `manifest.json`: row counts, onset counts, which runs or conditions are
  in train and test, and `prb_capacity_per_bin` per source. That is the
  divisor of `cell_util`: 250 slots × PRBs, so 12,500 for 50 PRBs and 13,000
  for 52. The build stops if a source was scaled with the wrong one, and
  `tests.py` checks the field.

`data/` is not on GitHub, so the folder travels as a zip
(`CP_5G_Congestion_datasets.zip`).

### Columns

| Column | Use it as | Meaning |
|---|---|---|
| `f_cell_util`, `f_dl_cqi`, `f_ul_sinr`, `f_dl_mbps`, `f_ul_mbps`, `f_ul_error_pct` | **Model inputs** | The six shared KPIs, standardised with train statistics |
| `cell_util` … `ul_error_pct` | Raw inputs for ablations only | The same six KPIs, unscaled |
| `onset` | **Target** | 1 = not starved now, starved 1 s later. Empty = undefined: drop the row |
| `starved` | Never an input | The current starved state; used for analysis, persistence baseline and episode lengths |
| `ratio` | **Never an input** | Supply ÷ demand; the label is built from it |
| `source`, `experiment`, `ue` | Grouping | One time series = one (`experiment`, `ue`) pair. `source` is `simu5g`, `tractor` or, in E6, `srsran` |
| `bin`, `t_s` | Ordering | Interval index and seconds |
| `serving_cell` | Graph grouping | Users with the same (`experiment`, `serving_cell`, `bin`) share a cell at that moment |
| `traffic_class` | Reporting only | embb / urllc / mmtc / unknown; results per class |
| `n_ue_active` | Reporting only | Users present; used for E5 and per-load results |
| `label_basis` | Reporting only | `prb` (TRACTOR) or `bytes` (simulator, srsRAN) |
| `trial`, `condition`, `slicing_enabled` | Reporting only | `slicing_enabled` is 0 everywhere |

## How to use the data

1. Sort by `experiment`, `ue` and `bin`.
2. **Break each series at gaps.** If two consecutive rows differ by more than
   1 in `bin`, that is a gap, and no window may span it. The simulator has
   no gaps; TRACTOR has a few (153 in the shared test set).
3. Build windows of the last **10 bins** of the six `f_*` columns, and
   predict `onset` at the window's last bin.
4. Drop rows where `onset` is empty.
5. **Never tune on a test set.** For validation, hold out whole series (or
   whole runs or conditions) from the training set.
6. Onsets are rare (0.4–0.6 % of real test rows). Use class weights or
   resampling, and report **precision–recall AUC** as the main score.
7. For E2, E3 and E4 the test set is identical, so the transfer gap is
   E4 − E2 on the same rows.

### Expected sizes (check you loaded the right data)

| Exp. | Train rows | Train onsets | Test rows | Test onsets |
|---|---|---|---|---|
| E1 | 355,737 | 24,693 | 129,809 | 7,207 |
| E2 | 485,546 | 31,900 | 242,347 | 1,026 |
| E3_k05 / k10 / k20 | 500,034 / 516,980 / 557,017 | ≈32,000 | 242,347 | 1,026 |
| E4 | 359,245 | 2,580 | 242,347 | 1,026 |
| E5 | 139,104 | 237 | 124,701 | 1,355 |

### Threshold variants (sensitivity runs)

`data/datasets_t03/` and `data/datasets_t07/` are the same seven
experiments labelled at threshold 0.3 and 0.7 instead of 0.5. They use
**identical splits and rows**; only `ratio < threshold`, and hence
`starved` and `onset`, change. Train on the main sets; repeat the final
models on these to show the result does not hinge on 0.5. These were built
with `build_datasets.py --threshold` and pass `tests.py`.

| Exp. | Test onsets at 0.3 | at 0.5 | at 0.7 |
|---|---|---|---|
| E1 | 7,078 | 7,207 | 7,703 |
| E2 / E3 / E4 | 326 | 1,026 | 1,727 |
| E5 (train / test) | 35 / 822 | 237 / 1,355 | 591 / 2,139 |

E5 at 0.3 has only 35 training onsets, so treat that one as indicative.

## E6: the srsRAN testbed (built, waiting for data)

E6 trains on the same simulator rows as E2 and tests on the integration
team's srsRAN testbed (zero-shot). E6_matched trains only on the
testbed-matched simulator runs (`OneCell*` configs, below) and tests on the
same srsRAN rows. Both use the bytes label (delivered ÷ offered), like the
simulator, because srsRAN reports no requested PRBs.

The code is in place and tested on synthetic tables. When the testbed data
arrives:

```bash
python analysis/build_datasets.py --sim "results/canonical/*.csv" \
    --tractor data/canonical/tractor.csv --out data/datasets \
    --srsran "data/canonical/srsran/*.csv"
python analysis/tests.py data/datasets
```

E1–E5 come out identical. `OneCell*` runs never enter them.

**What the integration team delivers:** one CSV per testbed run, one row per
UE per 250 ms bin, using the canonical column names (`analysis/canonical.py`).

| Column | Value |
|---|---|
| `source` | `srsran` |
| `experiment` | Unique run name, e.g. `srsran/load130-run1` |
| `trial`, `condition` | Run number; load level (e.g. `load130`) |
| `ue` | `UE_<id>` (a string) |
| `bin`, `t_s` | Bin index from the run's start; `bin × 0.25` |
| `n_ue_active`, `traffic_class`, `slicing_enabled`, `serving_cell` | UEs with a row in the bin; `embb`; `0`; `1` |
| `cell_granted_prbs` | Downlink PRBs used, summed over every slot in the bin |
| `cell_util` | `cell_granted_prbs ÷ 13,000` (52 PRBs × 250 slots) |
| `dl_cqi`, `ul_sinr` | Mean of the reports in the bin (`cqi`, `pusch_snr_db`) |
| `dl_mbps`, `ul_mbps` | `dl_brate`, `ul_brate` ÷ 1,000, averaged over the bin |
| `ul_error_pct` | `Σnok ÷ (Σok + Σnok) × 100` over the bin; empty if no uplink transmissions |
| `dl_offered_bytes` | iperf3 UDP rate × 0.25 s ÷ 8 (fixed-rate UDP only, never TCP) |
| `dl_served_bytes` | Bytes received by the UE's iperf3 in the bin (not `dl_brate`) |

A missing bin counts as a gap: leave it out rather than filling it. The
build rejects tables that:
- lack any of these columns;
- have another `source` value;
- have empty offered or served bytes;
- repeat a (`experiment`, `ue`, `bin`); or
- divide `cell_util` by anything other than 13,000.

E6 needs onsets, so total offered load must exceed the cell's capacity
(about 120–150 % of the measured maximum UDP throughput).

**Testbed-matched simulator runs (for E6_matched):**
- **Network and configs:** `OneCellTestbed.ned` and the `OneCellCapacity`,
  `OneCellTestbed` and `OneCellTestbed3` configs in `omnetpp.ini`.
- **Layout:** one gNB, 4 or 3 static UEs, 52 PRBs, band 3, and fixed-rate
  UDP downlink at 60–150 % of capacity.
- **Where to run them:** Linux (Smart App Control blocks the simulator here).
  The steps are in the comment above the configs: measure capacity, set
  `capMbps`, run the sweep, then `export_all.sh`.
- **Parser:** it reads `numBands = 52` from the export and divides by 13,000
  automatically.

## Rules that protect the result

- Inputs are the six `f_*` columns only. Adding `ratio`, `starved`,
  `n_ue_active` or anything ending in `_simonly` breaks the transfer claim or
  leaks the label.
- No real (TRACTOR) data in training for E1 or E2. E3 is the only mixed
  experiment.
- Do not refit the scaler on test data. Per-source scaling is a separate,
  clearly labelled ablation.
- Known before modelling: the simulator has 34 % starved intervals against
  2.2 % in real data, and four of six inputs differ strongly (KS 0.39–0.76).
  A poor E2 result is a finding to report, not a bug to tune away.

## What the telecom team still does after handoff

- Rebuild datasets on request (other thresholds or horizons), and keep
  `tests.py` passing.
- Build E6 once the srsRAN tables arrive (see above).
- Optional: more seeds, a heavier uplink burst, and a Simu5G patch that
  reports requested PRBs. The one-cell testbed-matched configs are written
  and only need running on Linux.
- Answer "why" questions about the network behind ML results.
- Keep the Stage 1 report sections up to date.

**Running new simulations:** Windows 11 Smart App Control blocks the
self-built `5g_simulation.exe` on the original laptop. New runs (more seeds, a
one-cell scenario) need a PC without Smart App Control, or Linux/WSL (see
README §1). Dataset rebuilds need only Python and work anywhere.

## How to ask for a change

Open a GitHub issue (or send a message) saying what is needed and why, for
example "E2 with threshold 0.3". The telecom team rebuilds, runs `tests.py`
and sends a new zip with an updated `manifest.json`.
