# CP_5G_Congestion — simulation side

OMNeT++/Simu5G scenario (`UrbanCongestionCluster`) and the parser that turns
its output into the canonical schema shared with the TRACTOR dataset.

| File | What it is |
|---|---|
| `omnetpp.ini` | Scenario, traffic and result-recording configuration |
| `UrbanCongestionCluster.ned` | Network: 1 macro + 3 micro gNBs, 4 interference-only background cells, 50 UEs |
| `demo.xml` | IPv4 address plan for the configurator |
| `run` | Launcher that sets the NED path (fixes `Cannot resolve module type 'LteChannelControl'`) |
| `analysis/parse_vectors2.py` | `.vec` export → canonical per-UE, 250 ms CSV |
| `CHANGES.md` | What was changed and why, with evidence, including first-run results |

Requires OMNeT++ 6.4.0, INET 4.6.0 and Simu5G 1.4.3.

## Build and run (Linux / WSL)

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
with `-r N`. Results go to `results/<Config>-<rep>.vec/.sca`.

## From results to the canonical dataset

```bash
opp_scavetool export -F CSV-R -o raw.csv results/UrbanCongestion-0.vec
python analysis/parse_vectors2.py raw.csv sim_canon.csv --run UrbanCongestion-0
```

Needs `pandas` and `numpy`. The parser prints a fill-rate report, handover
counts and a PRB-utilisation check; read it before using the output.

Things the output columns do **not** mean:

- `granted_prbs` is empty for the simulation. Simu5G has no per-UE grant;
  `cell_granted_prbs` is the whole cell's RBs per 250 ms bin (ceiling 12,500).
  TRACTOR is comparable only after summing its per-UE grants per bin.
- `ul_mbps` is served uplink; `ul_offered_mbps_simonly` is what the UE tried
  to send. Their ratio is a per-UE starvation signal.
- `requested_prbs` and the buffer columns are empty: Simu5G does not emit them.

## Building natively on Windows

Works with the official `omnetpp-6.4.0-windows-x86_64.7z` (unpack, run
`mingwenv.cmd` once to unpack the clang64 toolchain), then in that shell:
`./configure && make` for OMNeT++, `. setenv && make makefiles && make` for
INET, `. setenv -f && make makefiles && make` for Simu5G, and `make` here.

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
