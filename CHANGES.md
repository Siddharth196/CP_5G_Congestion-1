# Pipeline changes — Stage 1

Every change below was made because a statistic was verified present or absent
in the model, not because of a guess. Evidence came from two places:

- `opp_scavetool query -f 'type=~vector'` on a real run, giving the full list of
  declared statistics and their exact module paths
- `dataset_urban_congestion.csv`, 60,043 rows over 120 s with 50 UEs, giving
  fill rates and value behaviour

Files changed: `omnetpp.ini`, `parse_vectors2.py`.
Original ini preserved as `omnetpp.ini.bak`.

---

## 1. Statistics that do not exist in Simu5G 1.4.3

Searched the full declared-statistic list. These are **absent**, so no
configuration change can record them:

| Removed line | Reason |
|---|---|
| `**.cellularNic.mac.**.macBufferSize` | No MAC buffer statistic of any name exists. The only `queueLength` in the model is on `pppIf.queue`, which is the wired backhaul interface, not the radio MAC |
| `**.cellularNic.mac.**.droppedPkts` | No such statistic |
| `**.app[0].cbrFrameDelay` | Zero occurrences anywhere. The real app-layer delay statistic is `endToEndDelay` on `ue[*].app[0]` |

There is also **no requested-PRB statistic**. Only `avgServedBlocksDl` and
`avgServedBlocksUl` are emitted.

**Consequence.** Two ingredients of the intended congestion label —
buffer occupancy and requested PRBs — cannot be measured on the simulation
side. The label must either be redefined around quantities that do exist, or
Simu5G must be patched to emit them. This decision is still open and is the
main thing blocking Stage 2.

---

## 2. Patterns that matched nothing

| Old | Problem | New |
|---|---|---|
| `**.cellularNic.mac.**.avgServedBlocksDl` | In OMNeT++, `mac.**.` requires at least one further path element. The statistic sits directly on `<gnb>.cellularNic.mac`, so the pattern never matched | Explicit per-gNB lines for `macroGnb` and `microGnb*`, downlink and uplink |
| `**.cellularNic.phy.rcvdSinrDl` | `rcvdSinrDl` is on `channelModel[*]` and `nrChannelModel[*]`, not on `phy` | Explicit `channelModel` lines |
| `**.cellularNic.phy.measuredSinrUl` | Same. Uplink SINR is measured at the gNB's `channelModel` | Explicit gNB `channelModel` lines |
| `**.cellularNic.mac.**.macDelayDl` | Same wildcard problem | Explicit per-gNB lines |

SINR appeared in the old dataset only because the global
`**.vector-recording = true` caught it regardless of these lines.

---

## 3. Dual protocol stack

UEs carry parallel LTE and NR modules:

```
ue[*].cellularNic.mac        ue[*].cellularNic.nrMac
ue[*].cellularNic.phy        ue[*].cellularNic.nrPhy
ue[*].cellularNic.rlc.um     ue[*].cellularNic.nrRlc.um
ue[*].cellularNic.channelModel[*]   ue[*].cellularNic.nrChannelModel[*]
```

The old config recorded only the non-`nr` modules on UEs. Both are now
recorded, and `parse_vectors2.py` reports the event count for each so the live
stack can be identified from the next run.

---

## 4. Throughput

`rlcThroughputDl` is a running average, total bytes divided by elapsed time.
Evidence from UE_0 in the existing dataset:

```
t=0s   25,150
t=12s  32,247.32
t=24s  32,248.66
t=36s  32,249.11
t=48s  32,249.33
```

It converges and then creeps by hundredths. It cannot respond to congestion,
which is why `Total_Throughput` correlated with nothing. The old parser then
applied `sum` to it, and on the uplink that produced values up to 2.46e9
against a median of 32,256.

**Replacement.** PDCP byte counters, which emit `packetBytes` once per packet:

```
ue[*].cellularNic.pdcp.sentPacketToUpperLayer        -> downlink bytes
ue[*].cellularNic.pdcp.receivedPacketFromUpperLayer  -> uplink bytes
```

Summed per bin, these give bytes-in-bin directly. **Not differenced** — they
are per-packet emissions, not cumulative counters.

`rlcThroughputDl` is still recorded so the parser can print its per-UE
coefficient of variation and confirm the diagnosis for the paper.

---

## 5. Latency leakage

`Avg_Latency` and `Avg_RLC_Delay` correlate at exactly 1.0, with 97.6% of
values bitwise identical and a constant offset of 6.9e-07 s. Feeding one to
predict the other is not forecasting.

Both are still recorded, but both are now suffixed `_simonly` in the canonical
schema and neither is transferable, since TRACTOR carries no latency field.
The parser prints the correlation on every run as a standing check.

---

## 6. Parser structure

| Change | Reason |
|---|---|
| gNB rows kept and joined to UEs by serving cell | `avgServedBlocksDl` is gNB-side. The old UE-only filter deleted every row of it, which is why `Avg_PRB_Usage` was 0.00% filled |
| Bin width 100 ms → 250 ms | Matches TRACTOR's E2 reporting period |
| PRB aggregation is `sum` | TRACTOR's `sum_granted_prbs` is a per-window sum, ceiling 12,500 = 250 TTIs × 50 PRBs. `numBands = 50` and numerology 0 give the same ceiling here |
| Statistic matching on base name | Handles `name:vector` and `name:vector(packetBytes)` alike |
| Cell ID map corrected to 1–4 | `Serving_Cell` values in this model are 1–4, not 0–3 |
| Uplink SINR joined as a cell-level feature | It is measured at the gNB, so it arrives on gNB rows |

`cell_granted_prbs` is a **cell total**, not a per-UE grant. No per-UE
allocation statistic exists. Do not present it as one.

---

## 7. Run-level settings

```ini
repeat = 10                  # was commented out
**.scalar-recording = true   # was false
```

Ten seeds is what makes confidence intervals possible. A single run is an
anecdote.

---

## 8. Broken config sections

`Medium`, `Tiny` and `LightLoad` reduced `server.numApps` below the indices
holding the receiver apps, so `app[50]` (CbrReceiver) and `app[51]` (UdpSink)
stopped existing and all uplink traffic went to closed ports.

| Config | numUEs | numApps | Receivers moved to |
|---|---|---|---|
| Medium | 10 | 12 | app[10], app[11] |
| Tiny | 2 | 4 | app[2], app[3] |
| LightLoad | 20 | 22 | app[20], app[21] |

---

## 9. Known, not fixed

**Zero handovers.** Median handovers per UE is 0 across 120 s, despite
`enableHandover = true`, `dynamicCellAssociation = true` and UEs moving at
1–5 m/s across a 1000 m square. `*.ue[*].servingNodeId = 0` and
`nrServingNodeId = 1` are the prime suspects. Not changed, because TRACTOR is
single-cell and handover was never going to be a transferable feature. It does
mean `[Config HighMobility]` is not doing what its description claims.

**Empty exports.** Two separate runs produced vector declarations with no data
at all. Run the named config explicitly (`-c UrbanCongestion`) rather than the
bare `General` section, and check the `.vec` file size: a few kilobytes means
declarations only.

---

## 10. What to check on the next run

1. No column prints `<-- EMPTY` except `dl_buffer_bytes` and `requested_prbs`,
   which are expected to be empty permanently.
2. The UE stack report shows one stack clearly dominant.
3. `granted_prbs` max lands near 12,500, matching TRACTOR's ceiling.
4. PRB utilisation reaches saturation somewhere in the run. If the warning
   about saturation prints, the scenario has no congestion regardless of how
   well the recording works.
5. `corr(latency, rlc_delay)` is printed. If it is still above 0.99, the two
   remain collinear and only one may be used.

---

# Stage 1b — corrections after the first real runs

Checked against the Simu5G 1.4.3 / OMNeT++ 6.4.0 sources and confirmed by
completed runs (`Tiny`, and seed 0 of all six 120 s scenarios). Several Stage 1 conclusions came from a run whose
`app[0]` was an INET app, or from settings that silently matched nothing.

## 11. Stage 1 statements that were wrong

| Stage 1 said | Actually | Evidence |
|---|---|---|
| §1: `cbrFrameDelay` does not exist; use `endToEndDelay` | `app[0]` is Simu5G's `CbrReceiver`, which records `cbrFrameDelay` and `cbrReceivedBytes` and has no `endToEndDelay`. The statistic list came from an older run whose `app[0]` was `TcpSessionApp`/`UdpBasicApp` | `CbrReceiver.ned`; `cbrFrameDelay` has data on all 50 UEs |
| §2, §6: uplink SINR is measured at the gNB `channelModel` | Computed at the gNB but emitted on the **UE's** channel model (`ueChannelModel->emit(measuredSinrUl…)`), so it is per-UE | `LteRealisticChannelModel.cc`; gNB-side vectors empty, UE `nrChannelModel` vectors full |
| §3: which stack is live is unknown | NR. LTE `phy`/`mac`/`rlc` are idle in standalone mode, except that the LTE `phy` emits `servingCell = 0` every 100 ms | parser: 5.7 M NR events vs 60 k LTE events, all LTE ones `servingCell = 0` |
| §9: zero handovers | Handovers happen: 57 at 250 ms resolution across 50 UEs, all four cells used. The zero came from merging the LTE `servingCell = 0` stream with the NR one under the same name | `UrbanCongestion-0`, nrPhy `servingCell` |
| `macDelayDl` recorded per gNB | Declared but never emitted in Simu5G 1.4.3 | empty on every gNB; removed from the ini |

## 12. Settings that silently did nothing

OMNeT++ ignores ini keys that match no parameter, and the first matching
line wins.

- `**.ueTxPower` / `**.eNodeBTxPower` came before the per-node lines, so every
  gNB ran at 40 dBm (macro meant 46, micros 30). UE power lines targeted the
  idle LTE `phy`. Reordered; UE power now on `nrPhy`.
- Background cells: `BackgroundCell` has no `cellularNic` and defaults to
  `numBgUes = 0`, so the four cells generated no interference. Now configured
  through `bgScheduler` and `bgTrafficGenerator`. CQI went from a near-constant
  14 to p5 4 / median 12 / p95 15.
- `mac.queueSize` never drops anything; the backlog lives in RLC UM. The
  128 KiB limit moved to `rlc.um` (DL) and `nrRlc.um` (UL).
- `mac.harqProcesses` has no effect: HARQ buffers use compile-time constants.
- `**.vector-recording = true` recorded every default vector; the
  `result-recording-modes` lines only narrowed the chosen ones. Now selective
  enable plus a final catch-all `false`.
- `HeavyLoad` lowered the video UEs' rate and was lighter than the baseline.

## 13. Parser changes

- Idle LTE stack events dropped; `servingCell = 0` means "not attached" and
  is forward-filled over.
- `granted_prbs` stays empty for the simulation; the cell total goes only to
  `cell_granted_prbs` (+ `cell_granted_prbs_ul`). TRACTOR must be summed over
  UEs per bin to compare.
- `ul_mbps` is served uplink (RLC PDUs into grants); offered uplink is kept as
  `ul_offered_mbps_simonly`.
- HARQ columns are percentages; missing UL allocation in a bin is 0.

## 14. Does the scenario congest now?

First analysis pass: served/offered per UE and bin, offered DL taken from the
CBR configuration plus 28 B of IP/UDP header; starved = below 0.415 with
2-bin hysteresis; onsets at a 1 s horizon. Seed 0, first second excluded.
The final pipeline (§17–18) measures offered load from each run's own app
parameters and uses threshold 0.5, so its numbers differ slightly.

| Config | UEs | Cell DL util. p50 / p95 | CQI p5 / p50 | DL starved | DL onsets @1 s | UL starved | Handovers | Latency p50 / p95 |
|---|---|---|---|---|---|---|---|---|
| LightLoad | 20 | 0.00 / 0.03 | 10.5 / 14.0 | 1.0 % | 7 | 0.0 % | 35 | 5 ms / 11 ms |
| UrbanCongestion | 50 | 0.25 / 0.91 | 4.1 / 12.0 | 25.3 % | 1,490 | 0.1 % | 57 | 53 ms / 2.8 s |
| GradualCongestion | 50 | 0.24 / 0.91 | 4.0 / 12.1 | 25.0 % | 1,504 | 0.0 % | 53 | 54 ms / 2.6 s |
| HighMobility | 50 | 0.27 / 0.91 | 4.0 / 11.5 | 26.0 % | 1,578 | 0.1 % | 127 | 52 ms / 2.4 s |
| LowMobility | 50 | 0.32 / 0.90 | 2.0 / 11.0 | 25.8 % | 1,146 | 0.9 % | 59 | 40 ms / 3.9 s |
| HeavyLoad | 50 | 0.29 / 0.91 | 4.0 / 11.8 | 50.9 % | 3,010 | 0.1 % | 44 | 287 ms / 8.8 s |

Findings:

- Load now produces a clean gradient (1 % → 25 % → 51 % starved), the same
  kind of response TRACTOR shows across its slicing-off load sweep.
- Starvation is downlink-only and concentrated on the video UEs (0–19,
  median served/offered ≈ 0.5, 0.1 in `HeavyLoad`) on the saturated macro
  cell; web and IoT UEs are served in full.
- The uplink burst is real but never starves anyone: during 50–65 s UEs 0–29
  offer 0.90 Mbit/s each and are served 0.89, and uplink cell utilisation
  rises from 0.10 to 0.78. `GradualCongestion` only reshapes that burst, so
  it is indistinguishable from `UrbanCongestion`. Uplink congestion needs a
  heavier burst (larger `messageLength` or shorter `sendInterval`).
- `HighMobility` roughly doubles handovers (127 vs ~55), so it does what its
  description says; the §9 conclusion came from the parser artifact above.
- Served/offered is a direct per-UE analogue of granted/requested: the
  server traffic is CBR, so offered DL is known exactly. This is a candidate
  for the open label decision that needs neither a Simu5G patch nor the
  cell-utilisation proxy. (PDCP counts IP packets: add 28 B of IP/UDP header
  to each payload when computing offered.)
- `corr(latency, rlc_delay)` is still 1.0000.

## 15. Windows notes

- INET 4.6.0 does not export `SharedDataManager` / `CodeFragment` from its
  DLL, so Simu5G fails to link on Windows. Backported fix in README.
- `**.vector-record-empty = false`: by default OMNeT++ reserves a 1 MiB buffer
  for each of the ~12,000 declared vectors, about 12 GB of committed memory per
  run. Linux overcommits; Windows fails the third parallel run with
  `std::bad_alloc`. With the option each run commits about 1 GB.

---

# Stage 2 — TRACTOR side, labels and experiment datasets

Source: `logs/Multi-UE` of https://github.com/genesys-neu/TRACTOR (commit
9509fbf), 189 `<IMSI>_metrics.csv` files in 33 conditions (Trial0: 12
per-class conditions; Trials 1–3: `multi4`…`multi10`), one row per UE every
250 ms. Code: `analysis/harmonise_tractor.py`, `labels.py`,
`build_datasets.py`, checked by `tests.py`.

## 16. What the real data does and does not support

Reproduced exactly: 739,297 rows; 189 files in 33 conditions; one 19-column
file (`Trial0/embb1/1010123456002`, no slicing columns) and 188 with 36
columns; 250 ms reporting; slicing on = `slice_prb` 14 with median
granted/requested 0.294, slicing off = median 1.010.

**Not reproducible** from the public data under any definition tried
(threshold 0.415 or 0.5, all rows or rows with demand, concurrency from
`num_ues` or from UEs present):

| Earlier figure | What the data gives |
|---|---|
| 114,057 starved records (15.4 %) | 77,813 (10.5 %) below 0.415; 81,028 below 0.5; 125,291 with any shortfall |
| Starved, slicing off, by UEs: 1 → 0.006 %, 2 → 35.6 %, 3 → 44.7 %, 8 → 70.7 %, 9 → 82.0 % | Of rows with demand: 0 %, 0.1 %, 5 %, 48 %, 46 % |
| P(starved in 1 s ∣ starved now) 0.86–0.97, base rate ≈ 0.25 | 0.72, base rate 0.02 (§17 label, all rows); 0.78 / 0.10 on rows with demand at 0.415 without hysteresis |
| 8,670 onsets at 1 s | 3,606 (threshold 0.5) or 2,322 (0.415), slicing off |

Do not cite the left-hand column.

Further findings:

- **The bimodality is a slicing artefact.** With slicing off,
  granted/requested has one mode at 1.0 (58 % of demand rows in 0.9–1.5)
  and a smooth left tail; no second mode near 0.29. With slicing on, 78 %
  sit at 0.2–0.4 (the 14-PRB cap) and 22 % at 1.0. The fitted 0.415 valley
  separated capped from uncapped users, not congested from uncongested.
- **The mMTC "permanent cap" is also slicing.** Slicing off, mMTC UEs have
  median ratio 1.29 and 1.3 % starved rows; eMBB 9.1 %, URLLC 11.8 %
  (threshold 0.415). The reason for per-UE baseline thresholds disappears,
  so labels use one absolute threshold.
- **The PRB columns are downlink.** Rank correlations on rows with demand:
  granted vs DL throughput 0.94 (UL 0.33); requested vs DL buffer 0.62
  (UL buffer 0.11).
- **The three trials are not repetitions.** Trial2 and Trial3 assign the
  same trace to the same UE in only 2 of 49 cases; Trial1 has no trace list
  (its UEs are `traffic_class = unknown`). They cannot serve as independent
  seeds for confidence intervals.
- **Real UEs are idle most of the time**: only 22 % of slicing-off rows have
  any PRB demand, against 100 % in the simulator (constant-rate traffic).

## 17. Labels

`labels.py`, identical for both sources: ratio = supply ÷ demand per UE and
bin; starved = ratio < **0.5** with **2-bin** hysteresis on entry and exit;
onset = not starved now and starved **4 bins (1 s)** later. Bins without
demand are not starved; a gap in a UE's bins resets the state.

- TRACTOR (true label): `granted_prbs ÷ requested_prbs`.
- Simulator: `dl_served_bytes ÷ dl_offered_bytes`. Simu5G emits no
  requested-PRB statistic. Offered is the payload each server `CbrSender`
  sends to the UE, computed from that app's parameters in the run's `.sca`;
  served is the UE `CbrReceiver`'s `cbrReceivedBytes`. For one UE in one bin,
  needed and granted PRBs are both bytes ÷ bits-per-PRB at the UE's MCS, so
  the ratio equals granted ÷ requested up to MCS changes within the bin.

TRACTOR with these settings: 2.2 % of rows starved (11.2 % of rows with
demand), 3,606 onsets, episodes median 3 bins / p90 10 / max 725,
P(still starved in 1 s) 0.72 vs base rate 0.02. Starved share of rows with
demand by UEs present: 1 → 0, 2 → 0.1 %, 3 → 5.2 %, 4 → 22.6 %,
8 → 48.3 %, 9 → 46.3 %, 10 → 21.0 %. Threshold sensitivity (state / onsets):
0.3 → 1.2 % / 1,662; 0.415 → 1.7 % / 2,322; 0.5 → 2.2 % / 3,606;
0.7 → 4.1 % / 6,107.

Checking the simulator-side label on real data:

| Candidate computable on both sides | vs true label on TRACTOR | vs served/offered in the simulator |
|---|---|---|
| served ÷ (served + DL buffer growth) | precision 0.95, recall 0.01, kappa 0.01 | (is the simulator label) |
| cell_util ≥ 0.9 while the UE is active | precision 0.59, recall 0.65, kappa 0.61 | kappa −0.12 |

- Buffer growth cannot stand in for offered load on real traffic: when a
  TRACTOR UE is starved its DL buffer is already standing (median 10 kB vs
  0) and arrivals adapt or are dropped, so served ≈ arrivals. The check
  says nothing against the simulator label, whose offered load is exact.
- The cell-saturation proxy works in TRACTOR's single cell but not in the
  multi-cell simulator, where the macro cell is near-saturated much of the
  time and who starves depends on each UE's channel. It is not used.
- Remaining limitation: the two labels are measured differently (PRBs vs
  bytes). Making them identical needs Simu5G patched to emit per-UE
  requested PRBs.

## 18. Experiment datasets

`build_datasets.py`, seed 7. Shared TRACTOR test set: 30 % of each trial's
conditions — `Trial0/mmtc3`, `Trial0/mmtc4`, `Trial0/urllc1`,
`Trial0/urllc3`, `Trial1/multi10`, `Trial1/multi4`, `Trial2/multi4`,
`Trial2/multi9`, `Trial3/multi10`, `Trial3/multi6` (242,347 rows; these
conditions are larger than average, so 40 % of rows). E1 holds out the
highest seed of every config with at least two seeds. Features are
forward-filled within each UE series, then standardised with the train
set's mean and std (`scaler.json`).

Facts the modelling step must account for:

- Label prevalence differs by an order of magnitude: simulator 35 % of rows
  starved, TRACTOR 2.2 % (onsets 7 % vs 0.6 %). Use PR-AUC and calibrate.
- E5's train side (4–6 UEs) has few onsets (≈ 240) against ≈ 1,350 in its
  test side (9–10 UEs).
- `n_ue_active` is 20 or 50 in the simulator but 1–10 in TRACTOR; it is
  identity, not a feature.
- Simulator seeds come from a queue that was still running when the
  datasets were first built; re-run `export_all.sh` and `build_datasets.py`
  after more seeds finish. Counts are in each `manifest.json`.

## 19. Simulator seeds

`UrbanCongestion` and `HeavyLoad` have seeds 0–4 queued, the other four
scenarios seeds 0–2. Each 120 s run takes ~45 min on one core.
