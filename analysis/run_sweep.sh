#!/bin/sh
# Run every run of one or more configs, J at a time, logging each run
# separately. Configs run in the order given, each last index first: in
# the TractorMatched* configs the highest indices are the 10-UE runs, which
# take longest, so starting them first keeps all J slots busy. A run whose
# log already ends with OMNeT++'s "End." is skipped, so an interrupted sweep
# resumes where it stopped (interrupted runs start again from t = 0).
#
#   sh analysis/run_sweep.sh "<Config> [<Config> ...]" [result-dir] [J]
#
# Uses run_opp (Windows, Smart App Control) unless RUNNER=run is set
# (Linux, the built executable). Logs: <result-dir>/logs/<Config>-r<N>.log.
set -e
CFGS=$1
RES=${2:-results}
J=${3:-3}
DIR=$(cd "$(dirname "$0")/.." && pwd)
RUNNER=${RUNNER:-run_opp}
cd "$DIR"
mkdir -p "$RES/logs"
for CFG in $CFGS; do
    N=$(sh "$RUNNER" -s -c "$CFG" -q numruns 2>/dev/null | tail -1 | tr -d '\r')
    echo "$CFG: $N runs" >&2
    for r in $(seq $((N - 1)) -1 0); do
        if grep -q '^End\.' "$RES/logs/$CFG-r$r.log" 2>/dev/null; then
            echo "skip  $CFG -r $r (finished)" >&2
        else
            echo "$CFG $r"
        fi
    done
done | xargs -P "$J" -L 1 sh -c \
    "sh '$RUNNER' -c \"\$0\" -r \"\$1\" --result-dir='$RES' > '$RES/logs/'\"\$0\"'-r'\"\$1\"'.log' 2>&1 && echo \"done  \$0 -r \$1\" || echo \"FAIL  \$0 -r \$1\""
