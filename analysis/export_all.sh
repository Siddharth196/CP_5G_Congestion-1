#!/bin/sh
# Export every results/*.vec to CSV-R and convert it to the canonical schema.
#
#   sh analysis/export_all.sh [results-dir]
#
# Writes <results>/canonical/<Config>-<rep>.csv plus the parser's report as
# <Config>-<rep>.report.txt. Runs already converted (CSV newer than the .vec)
# are skipped. Needs opp_scavetool on PATH and a Python with pandas/numpy;
# set PYTHON to choose the interpreter.
set -e

DIR=$(cd "$(dirname "$0")/.." && pwd)
RES=${1:-$DIR/results}
OUT=$RES/canonical
PY=${PYTHON:-python3}
mkdir -p "$OUT"

for vec in "$RES"/*.vec; do
    [ -e "$vec" ] || continue
    run=$(basename "$vec" .vec)
    if [ -f "$OUT/$run.csv" ] && [ "$OUT/$run.csv" -nt "$vec" ]; then
        echo "skip  $run (up to date)"
        continue
    fi
    echo "parse $run"
    opp_scavetool export -F CSV-R -o "$OUT/$run.raw.csv" "$vec" >/dev/null
    "$PY" "$DIR/analysis/parse_vectors2.py" "$OUT/$run.raw.csv" "$OUT/$run.csv" \
        --run "$run" > "$OUT/$run.report.txt"
    rm -f "$OUT/$run.raw.csv"
done
