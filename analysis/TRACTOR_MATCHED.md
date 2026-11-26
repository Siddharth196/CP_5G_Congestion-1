# TRACTOR-matched single-cell sweep

This is a separate simulator dataset next to E1–E6, and it changes none of
them:
- **What it is:** one cell with 4–10 static UEs, like TRACTOR, in place of
  the urban scenario's ~42 UEs on one macro cell.
- **What it is for:** an extra simulator training source and a held-out
  simulator test set, whose graph size and contention pattern match the real
  data.

## What stays untouched

- **Configs:** every existing config in `omnetpp.ini` is unchanged; the new
  ones are appended.
- **Results:** every existing `results/*.vec/.sca` and
  `results/canonical/*.csv` is unchanged.
- **E1–E6:** `build_datasets.py` leaves every `TractorMatched*` run out, and
  `tests.py` checks this.
- **Parser:** two changes, and neither alters the existing runs. The rebuild
  check is in the results section.
  - A UE's offered bytes are summed over all the sender apps that serve it.
    Every existing config has one app per UE, so nothing changes for them.
  - `ue_x_m` / `ue_y_m` are written only when every UE is static, so the
    columns never appear for the existing (moving) runs.

## Design

| | |
|---|---|
| Network | `OneCellTestbed.ned`: one gNB (`macroGnb`, `serving_cell` 1), no micro cells, no corner interference cells, no handover |
| Radio | 50 PRBs, 15 kHz SCS (numerology 0, 1 ms slots), 10 MHz at 2 GHz, PF scheduler. `cell_util` = Σ `avgServedBlocksDl` over 250 ms ÷ 12,500, the TRACTOR denominator |
| 52-PRB set | `TractorMatched52`: 52 PRBs at 1842.5 MHz (band 3, the srsRAN testbed), `cell_util` ÷ 13,000, for E6_matched |
| gNB power | 15 dBm (see calibration) |
| UEs | n = 4, 6, 8, 10, static. Even UE indices "near" (within ±200 m of the gNB in x and y), odd ones "far" (250–400 m in x, ±250 m in y: 250–470 m). New placement per seed |
| Downlink traffic | The UrbanCongestion classes by UE index u. u mod 3 = 0: video, 1400 B. 1: web, 500 B. 2: IoT, 40 B. Constant rate, the Urban rates (2.8 / 0.2 / 0.032 Mbit/s) × k, with **k = 6**: 16.8 / 1.2 / 0.192 Mbit/s |
| Sessions | Each UE's downlink runs as `sess` constant-rate sessions (see below) |
| Uplink | As in Urban: a 100 B / 20 ms stream, plus the 50–65 s burst (500 B / 5 ms; IoT 100 B / 100 ms, 50–70 s) |
| Runs | 120 s, seeds 0–4 per n |

### Why sessions

- **The problem:** with static UEs and an always-on constant load, each UE
  settles into a fixed state within a second or two. It is starved for the
  whole run or never, so there are almost no onsets.
- **The fix:** each UE gets one session per 30 s slot. A session starts 0–10
  s into its slot and ends 0–10 s before the slot ends, so it lasts 10–30 s.
  Sessions start and stop at random times per UE and seed. Other UEs then
  cross the starvation threshold when a session joins, and recover when one
  leaves.
- **Still constant-rate:** each session is a plain `CbrSender` with fixed
  `startTime` / `finishTime`. The parser already computes offered bytes from
  those parameters, so the bytes label stays exact. The only parser change is
  summing over a UE's apps (above).
- **Rows with no demand:** bins where a UE has no session have
  `dl_offered_bytes` = 0, so the ratio is empty and the row counts as not
  starved (the existing rule). The summary reports starvation over all rows
  and over rows with demand.
- **Calibration also tries `sess` = 1** (always on), so the choice rests on
  data.

## Calibration

All calibration output is in `results/tractor_matched_calib/`, never in
`results/`, so none of it reaches a dataset.

### 1. gNB power (`TractorMatchedCapacity`)

**Setup:** 10 UEs, each offered 50 Mbit/s, so the cell is saturated. One
placement, with the far UEs then at 300–480 m. 8 s runs at each power.

| gNB power | Cell DL throughput | Median CQI, all rows | Near UEs (median CQI) | Far UEs |
|---|---|---|---|---|
| 46 dBm (the urban macro) | 53.9 Mbit/s | — | 13–15 | 13–14 |
| 30 dBm | 44.2 | — | 12–15 | 8–14 |
| 20 dBm | 41.2 | 11 | 9–14 | 4–11 |
| **15 dBm** | **33.7** | **7** | **7–14** | **2–9** |
| 10 dBm | 27.9 | 8 | 5–14 | 2–8 |

- **Chosen: 15 dBm.** Its median CQI (7) is closest to TRACTOR's (~7.5),
  and the CQI still spreads from near to far UEs.
- At 46 dBm every UE sits at CQI 13–15 in a 500 m cell.
- A full 30 s run at 15 dBm confirms it: 33.7 Mbit/s (p5–p95: 32.4–34.9),
  `cell_util` 0.84.

### 2. Round 1: k = 1–5, sess = 1 and 4, one seed

(`round1/`, far UEs at 300–480 m)

**No (sess, k) passed. Two findings:**
- **Always-on traffic (sess = 1) gives almost no onsets.** Most
  configurations have 0, the best has 35. Starved shares are whole numbers
  of UEs (25 %, 12.5 %, 30 %), meaning a UE is starved for the whole run or
  never.
- **UEs beyond ~500 m (CQI 2–3) starve at any load.** At k = 1 with 4 Mbit/s
  offered, three such UEs got nothing. That is coverage loss, not
  contention.

**Changes for round 2:**
- Sessions fixed at 4.
- Far UEs moved in to 250–400 m in x and ±250 m in y (250–470 m from the
  gNB).
- Two seeds instead of one.
- k range raised to 2–6.

### 3. Round 2: k = 2–6, sess = 4, two seeds

Mean over the 2 seeds; starvation is over rows with downlink demand.

| k | 4 UEs | 6 UEs | 8 UEs | 10 UEs | Onsets per run (4 / 6 / 8 / 10 UEs, mean) |
|---|---|---|---|---|---|
| 2 | 0 % | 0 % | 0 % | 8.0 % | 0 / 0 / 0 / 16 |
| 3 | 0 % | 0 % | 0 % | 11.9 % | 0 / 0 / 0 / 38 |
| 4 | 0 % | 0 % | 0 % | 26.0 % | 0 / 0 / 0 / 56 |
| 5 | 0 % | 7.0 % | 10.2 % | 32.7 % | 0 / 8 / 16 / 105 |
| **6** | **0 %** | **7.8 %** | **20.9 %** | **39.1 %** | **0 / 8 / 32 / 73** |

**Chosen: k = 6, sess = 4.** Per UE while a session is on, that is video
16.8, web 1.2 and IoT 0.192 Mbit/s.
- It is the only k where starvation rises clearly with n and reaches tens
  of percent at 8–10 UEs.
- **It misses one criterion: 4 UEs never starve**, so the 4-UE runs have no
  onsets. They are a no-congestion baseline instead. Getting a little
  starvation at 4 UEs would need k ≈ 7–8, which would push 10 UEs to ~45–50
  % and make the runs slower. Agreed with the team: use k = 6.
- The median CQI is 10–11 here, above TRACTOR's ~7.5. Moving the far UEs in
  to avoid coverage loss raised it.

The full tables are in `calibration.csv` and `round1/calibration.csv`.
Reproduce them with `python analysis/calibrate_tractor_matched.py`.

## Commands

Windows (this laptop: Smart App Control blocks the built `5g_simulation.exe`,
so `run_opp` runs the same model through OMNeT++'s `opp_run`), in OMNeT++'s
shell. On Linux, set `RUNNER=run`.

```bash
# 1. capacity and calibration (into a separate folder, never results/)
sh run_opp -c TractorMatchedCapacity -r 0 --result-dir=results/tractor_matched_calib
sh analysis/run_sweep.sh TractorMatchedCalib results/tractor_matched_calib 4
sh analysis/export_all.sh results/tractor_matched_calib
python analysis/calibrate_tractor_matched.py
# 2. set k and sess in [Config TractorMatched] (now k=6, sess=4), then the sweep:
#    40 runs, 4 at a time, the 10-UE runs of each config first
sh analysis/run_sweep.sh "TractorMatched TractorMatched52" results 4
sh analysis/export_all.sh results $(cd results && ls TractorMatched*.vec | sed 's/\.vec$//')
# 3. the dataset
python analysis/build_tractor_matched.py --sim "results/canonical/TractorMatched*.csv" \
    --out data/datasets_tractor_matched
python analysis/tests.py
```

## Outputs

- **`results/canonical/TractorMatched-n<UEs>-<seed>.csv`** and
  **`TractorMatched52-…`**: the canonical schema, one row per UE per 250 ms
  bin, with `serving_cell`, `dl_offered_bytes` and `dl_served_bytes`, plus
  `ue_x_m` / `ue_y_m`. Each has a `.report.txt` from the parser.
- **`data/datasets_tractor_matched/TM50/`** and **`TM52/`**: `train.csv.gz`,
  `test.csv.gz`, `scaler.json`, `manifest.json`.
  - **Split:** the test set is the highest seed (4) of each n, the E1 rule.
    Whole runs go to one side only.
  - **Columns:** as E1–E6, plus the diagnostic `n_ues`, `dl_cqi_filled`,
    `ue_x_m` and `ue_y_m`. **None of them is a model input.**
  - **`dl_cqi` is forward-filled** exactly as in E1–E6.
    `dl_cqi_filled = 1` marks bins whose canonical `dl_cqi` was empty.
  - **Manifest:** `prb_capacity_per_bin` and `prb_capacity_per_run` give the
    `cell_util` denominator (12,500 or 13,000).
- **`data/datasets_tractor_matched/summary.csv` / `summary.md`**: one row
  per set and n.

## Results

### TM50: 50 PRBs, `cell_util` ÷ 12,500

20 runs: 5 seeds × n = 4, 6, 8, 10; 67,226 UE-bins.

| | Train | Test |
|---|---|---|
| Runs | seeds 0–3 | seed 4 of each n |
| Rows | 53,781 | 13,445 |
| Onsets | 440 | 130 |

| UEs | Starved, all rows | Starved, rows with demand | Same, without link-limited UEs | Onsets (with demand) | Onsets without link-limited UEs | Link-limited UEs | Median CQI | Median `cell_util` |
|---|---|---|---|---|---|---|---|---|
| 4 | 9.3 % | 13.4 % | 4.7 % | 48 (18) | 16 | 2 | 12 | 0.73 |
| 6 | 7.6 % | 11.0 % | 8.0 % | 64 (40) | 48 | 1 | 9 | 0.71 |
| 8 | 12.9 % | 18.5 % | 16.6 % | 152 (106) | 136 | 1 | 9 | 0.81 |
| 10 | 22.7 % | 32.7 % | 16.6 % | 306 (168) | 128 | 11 | 9 | 0.78 |

### TM52: 52 PRBs at 1842.5 MHz, `cell_util` ÷ 13,000

20 runs, 67,225 UE-bins.

| | Train | Test |
|---|---|---|
| Runs | seeds 0–3 | seed 4 of each n |
| Rows | 53,780 | 13,445 |
| Onsets | 483 | 163 |

| UEs | Starved, all rows | Starved, rows with demand | Same, without link-limited UEs | Onsets (with demand) | Onsets without link-limited UEs | Link-limited UEs | Median CQI | Median `cell_util` |
|---|---|---|---|---|---|---|---|---|
| 4 | 2.8 % | 4.0 % | 4.0 % | 16 (7) | 16 | 0 | 10 | 0.81 |
| 6 | 9.4 % | 13.5 % | 10.7 % | 89 (64) | 73 | 1 | 10 | 0.78 |
| 8 | 10.5 % | 15.1 % | 6.3 % | 142 (88) | 78 | 4 | 11 | 0.79 |
| 10 | 18.9 % | 27.2 % | 16.2 % | 399 (287) | 287 | 7 | 10 | 0.79 |

**TM52 matches the task better than TM50.** Starvation rises steadily from
4 % at 4 UEs, and fewer UEs are link-limited. The likely reason is the
lower carrier (1842.5 vs 2000 MHz), which gives less path loss and higher CQI
at the cell edge.

Per-set and per-n numbers, including the share of empty CQI, are in
`data/datasets_tractor_matched/summary.csv`.

### Read before using

**1. Some starvation is a UE's own link, not contention.**
- **What happens:** a far video UE at CQI 3–5 asks for 16.8 Mbit/s, which
  its link cannot carry even with the cell to itself. It is starved in about
  99 % of the bins where its session runs.
- **Its onsets are session starts.** They show up as onsets of 4 labelled
  bins at each of its 4 session starts, which is why many runs have exactly
  16 or 32 onsets.
- **Share of starved rows from such UEs:**

  | UEs | Share |
  |---|---|
  | 4 | 68 % |
  | 6 | 29 % |
  | 8 | 12 % |
  | 10 | 60 % |

- **The flag:** `link_limited` = 1 marks a UE (per run) starved in more than
  90 % of its demand bins. Drop those UEs to train or score on contention
  only.
- **At 10 UEs the flag is conservative.** It catches 11 UEs, and some of them
  are starved throughout because the cell is overloaded, which is
  contention.
- **How a redesign would remove this:** a smaller far ring or a lower video
  rate. That means another calibration and sweep; it was not done, by
  agreement.

**2. Starvation rises with n, but not strictly from 4 to 6 UEs.** Rows with
demand go 13.4 → 11.0 → 18.5 → 32.7 %.
- At 4 UEs, 3 of the 5 placements put the far video UE where it is
  link-limited.
- Without link-limited UEs the trend is 4.7 → 8.0 → 16.6 → 16.6 %.

**3. `dl_cqi_filled` is about 30 % here.**
- **Why so high:** a UE gets no downlink frames between its sessions, so its
  CQI is empty in those bins.
- **Not a starvation signal here.** This is different from the urban runs,
  where an empty CQI meant starvation. Here it mostly means the session is
  off.
- Keep it out of the inputs as before.

**4. Median CQI is 9–12, above TRACTOR's ~7.5.** This comes from bringing the
far UEs in to avoid coverage loss (calibration round 1).

### Checks

- **Unit tests:** `tests.py`, ALL PASSED. Section 5b tests the builder:
  held-out seed per n, `dl_cqi_filled`, forward-fill, divisor per run,
  `link_limited`, and the summary.
- **Parser:** re-parsing the existing `LightLoad-0` run with the changed
  parser gives a byte-identical canonical CSV.
- **Kept out of E1–E6:** `build_datasets.py` leaves `TractorMatched*` out,
  and tests.py section 5 checks it.
- **E1–E5 rebuild:** rebuilt from `results/canonical/*.csv` with the 40 new
  runs present, and **identical** to `data/datasets` in every train/test
  file (decompressed content), scaler and manifest. The build log reports
  "40 TractorMatched* runs left out".
- **Delivered datasets:** `python analysis/tests.py data/datasets` gives ALL
  PASSED (211 checks), including "no TractorMatched* runs" in every E*
  folder.
