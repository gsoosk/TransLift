#!/usr/bin/env bash
# Run txscan on every app in corpus/repos.tsv (or the slugs given), several at a time.
# Usage: tools/run_all.sh [-j N] [--save-prev] [slug ...]
#   --save-prev  copy the current output/out/<app>.json files to output/out_prev/ first, for tools/regress.py
# Exits non-zero if txscan failed for any app (their old output is kept and listed).
set -u
K="$(cd "$(dirname "$0")/.." && pwd)"
J=2; SAVE=0; APPS=()
while [ $# -gt 0 ]; do
  case "$1" in
    -j) J="$2"; shift 2;;
    --save-prev) SAVE=1; shift;;
    -h|--help) sed -n 2,5p "$0"; exit 0;;
    -*) echo "unknown option $1" >&2; exit 2;;
    *) APPS+=("$1"); shift;;
  esac
done
if [ ${#APPS[@]} -eq 0 ]; then
  mapfile -t APPS < <(python3 "$K/tools/corpus.py" apps)
fi
if [ $SAVE -eq 1 ]; then
  mkdir -p "$K/output/out_prev"
  n=0
  for a in "${APPS[@]}"; do [ -f "$K/output/out/$a.json" ] && cp "$K/output/out/$a.json" "$K/output/out_prev/$a.json" && n=$((n+1)); done
  echo "saved $n outputs to output/out_prev/"
fi
log=$(mktemp)
printf '%s\n' "${APPS[@]}" | xargs -P "$J" -I{} "$K/tools/run_one.sh" {} | tee "$log"
failed=$(grep -v ' rc=0 ' "$log" | awk '{print $1}' | tr '\n' ' ')
rm -f "$log"
if [ -n "$failed" ]; then
  echo "txscan failed for: $failed" >&2
  exit 1
fi
