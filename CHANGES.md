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
completed runs (`Tiny`, and 120 s / 50 UE runs of `UrbanCongestion` and
`HeavyLoad`, seed 0). Several Stage 1 conclusions came from a run whose
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

Served/offered per UE and bin, with offered DL known exactly from the CBR
configuration; starved = below 0.415 (TRACTOR's mixture valley) with 2-bin
hysteresis; onsets at a 1 s horizon. Seed 0, first second excluded.

| Config | Cell DL util. p95 | DL starved | DL onsets @1 s | UL starved | Latency p50 / p95 |
|---|---|---|---|---|---|
| UrbanCongestion | 0.91 | 25.3 % | 1,490 | 0.1 % | 53 ms / 2.8 s |
| HeavyLoad | 0.91 | 50.9 % | 3,010 | 0.1 % | 287 ms / 8.8 s |

Starvation is downlink-only and concentrated on the video UEs (0–19) served
by the saturated macro cell. The uplink burst never starves anyone, so it is
not a congestion source. `corr(latency, rlc_delay)` is still 1.0000.

## 15. Windows notes

- INET 4.6.0 does not export `SharedDataManager` / `CodeFragment` from its
  DLL, so Simu5G fails to link on Windows. Backported fix in README.
- `**.vector-record-empty = false`: by default OMNeT++ reserves a 1 MiB buffer
  for each of the ~12,000 declared vectors, about 12 GB of committed memory per
  run. Linux overcommits; Windows fails the third parallel run with
  `std::bad_alloc`. With the option each run commits about 1 GB.
