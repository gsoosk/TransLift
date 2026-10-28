#!/usr/bin/env bash
# Run txscan on one app: clones/<slug> -> output/out/<slug>.json, log in output/out/<slug>.err.
# Uses rules/<slug>.json (if present) and the commit in corpus/pins/<slug>.txt.
# The output replaces output/out/<slug>.json only when txscan succeeds.
# Usage: tools/run_one.sh <slug>
set -u
K="$(cd "$(dirname "$0")/.." && pwd)"
a="${1:-}"
case "$a" in ""|-*|*[!A-Za-z0-9._-]*) echo "usage: tools/run_one.sh <slug>" >&2; exit 2;; esac
dir="$K/clones/$a"
[ -d "$dir" ] || { echo "$a: no checkout in clones/$a (run tools/clone_app.sh $a)" >&2; exit 1; }
rules="{}"
if [ -f "$K/rules/$a.json" ]; then
  rules=$(python3 -I -c 'import json,sys; print(json.dumps(json.load(open(sys.argv[1]))))' "$K/rules/$a.json") \
    || { echo "$a: rules/$a.json is not valid JSON" >&2; exit 1; }
fi
commit=""
[ -f "$K/corpus/pins/$a.txt" ] && commit=$(awk '{print $1; exit}' "$K/corpus/pins/$a.txt")
if [ -n "$commit" ] && [ -d "$dir/.git" ]; then
  head=$(git -C "$dir" rev-parse HEAD 2>/dev/null || true)
  [ "$head" = "$commit" ] || echo "$a: warning: checkout is at ${head:0:12}, pin says ${commit:0:12} (run tools/clone_app.sh $a)" >&2
fi
mkdir -p "$K/output/out"
tmp="$K/output/out/.run-$a.$$"
rm -rf "$tmp"; mkdir -p "$tmp"
start=$(date +%s)
python3 -I "$K/tools/txscan.py" "$dir" "$tmp" --app "$a" --commit "$commit" --rules "$rules" > /dev/null 2> "$K/output/out/$a.err"
rc=$?
end=$(date +%s)
if [ $rc -eq 0 ] && [ -s "$tmp/$a.json" ]; then
  mv "$tmp/$a.json" "$K/output/out/$a.json"
else
  [ $rc -eq 0 ] && rc=1
fi
rm -rf "$tmp"
echo "$a rc=$rc $((end-start))s"
[ $rc -ne 0 ] && { echo "$a: txscan failed; output/out/$a.json was not changed. Last lines of output/out/$a.err:" >&2; tail -6 "$K/output/out/$a.err" >&2; }
exit $rc
