#!/bin/sh
# Export every results/*.vec to CSV-R and convert it to the canonical schema.
#
#   sh analysis/export_all.sh [results-dir [run ...]]
#
# Writes <results>/canonical/<Config>-<rep>.csv plus the parser's report as
# <Config>-<rep>.report.txt. With run names (e.g. UrbanCongestion-0) only
# those are converted, which avoids runs that are still being written.
# Runs already converted (CSV newer than the .vec) are skipped. Needs
# opp_scavetool on PATH and a Python with pandas/numpy; set PYTHON to choose
# the interpreter.
set -e

DIR=$(cd "$(dirname "$0")/.." && pwd)
RES=${1:-$DIR/results}
[ $# -gt 0 ] && shift
OUT=$RES/canonical
PY=${PYTHON:-python3}
mkdir -p "$OUT"

if [ $# -gt 0 ]; then
    vecs=$(for r in "$@"; do echo "$RES/$r.vec"; done)
else
    vecs=$(ls "$RES"/*.vec 2>/dev/null || true)
fi

for vec in $vecs; do
    [ -e "$vec" ] || continue
    run=$(basename "$vec" .vec)
    if [ -f "$OUT/$run.csv" ] && [ "$OUT/$run.csv" -nt "$vec" ]; then
        echo "skip  $run (up to date)"
        continue
    fi
    echo "parse $run"
    # the .sca carries the server apps' parameters, needed for dl_offered_bytes
    sca="$RES/$run.sca"
    [ -f "$sca" ] || sca=""
    opp_scavetool export -F CSV-R -o "$OUT/$run.raw.csv" "$vec" $sca >/dev/null
    "$PY" "$DIR/analysis/parse_vectors2.py" "$OUT/$run.raw.csv" "$OUT/$run.csv" \
        --run "$run" > "$OUT/$run.report.txt"
    rm -f "$OUT/$run.raw.csv"
done
