#!/usr/bin/env bash
# Review pending batches unattended: one headless Claude Code session (the java-tx-reviewer agent) per batch.
# Usage: tools/run_reviews.sh [-j 3] [--max N] [--pilot] [--model sonnet] [--budget-usd 3]
#   -j N          sessions at a time (default 3)
#   --max N       stop after N batches in total (default: until none is pending)
#   --pilot       only the pilot batches (make_batches.py --pilot N)
#   --model M     override the reviewer's model (sonnet, opus, haiku or a full model id)
#   --budget-usd  stop any one session at this estimated spend
# Each worker claims the next free batch (reviews/claims/), so several drivers, or an interactive /tx-corpus
# review, can run at the same time without doing the same batch twice. Claims lapse when this driver exits,
# so running it again resumes, retrying the batches that were left incomplete.
# Logs: reviews/logs/<batch>.json (result and estimated cost), .err, .check.
# Open the kit once in interactive Claude Code first, so its project settings and agents are trusted.
set -u
K="$(cd "$(dirname "$0")/.." && pwd)"
cd "$K"
J=3; MAX=0; PILOT=""; MODEL=""; BUDGET=""
while [ $# -gt 0 ]; do
  case "$1" in
    -j) J="$2"; shift 2;;
    --max) MAX="$2"; shift 2;;
    --pilot) PILOT="--pilot"; shift;;
    --model) MODEL="$2"; shift 2;;
    --budget-usd) BUDGET="$2"; shift 2;;
    -h|--help) sed -n 2,13p "$0"; exit 0;;
    *) echo "unknown option $1" >&2; exit 2;;
  esac
done
command -v claude >/dev/null || { echo "claude (the Claude Code CLI) is not on PATH" >&2; exit 1; }
mkdir -p reviews/logs
COUNT=reviews/logs/.started.$$
: > "$COUNT"

review_one() {
  local b="$1"
  local args=(-p "Review batch $b." --agent java-tx-reviewer --permission-mode dontAsk --output-format json)
  [ -n "$MODEL" ] && args+=(--model "$MODEL")
  [ -n "$BUDGET" ] && args+=(--max-budget-usd "$BUDGET")
  claude "${args[@]}" > "reviews/logs/$b.json" 2> "reviews/logs/$b.err"
  if python3 tools/review_batch.py check "$b" > "reviews/logs/$b.check" 2>&1; then s=done; else s=incomplete; fi
  echo "$(date +%H:%M:%S) $b: $s · $(grep '^batch ' "reviews/logs/$b.check" | tail -1)"
}

worker() {
  while :; do
    if [ "$MAX" -gt 0 ] && [ "$(wc -l < "$COUNT")" -ge "$MAX" ]; then return; fi
    b=$(python3 tools/review_status.py --next 1 --claim --owner $$ $PILOT)
    [ -z "$b" ] && return
    case "$b" in *[!A-Za-z0-9._-]*) echo "bad batch id $b" >&2; return;; esac
    echo "$b" >> "$COUNT"
    review_one "$b"
    # an incomplete batch stays claimed while this driver runs (no retry loop); the claim lapses when it exits
  done
}

for _ in $(seq "$J"); do worker & sleep 2; done
wait
rm -f "$COUNT"
python3 tools/review_batch.py check-all | tail -3
python3 tools/review_status.py | tail -4
python3 tools/review_status.py --cost
