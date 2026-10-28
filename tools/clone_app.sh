#!/usr/bin/env bash
# Check out one app into clones/<slug>.
#   With corpus/pins/<slug>.txt: fetch exactly that commit.
#   Without a pin: shallow-clone the default branch and write the pin (sha and commit date).
# Usage: tools/clone_app.sh <slug>   (the slug must be in corpus/repos.tsv)
set -euo pipefail
K="$(cd "$(dirname "$0")/.." && pwd)"
a="${1:-}"
case "$a" in ""|-*|*[!A-Za-z0-9._-]*) echo "usage: tools/clone_app.sh <slug>   (a slug from corpus/repos.tsv)" >&2; exit 2;; esac
repo=$(python3 "$K/tools/corpus.py" repo "$a")
url="https://github.com/$repo.git"
dir="$K/clones/$a"
pinf="$K/corpus/pins/$a.txt"
mkdir -p "$K/clones" "$K/corpus/pins"
export GIT_TERMINAL_PROMPT=0
if [ -f "$pinf" ]; then
  pin=$(awk '{print $1; exit}' "$pinf")
  if [ -d "$dir/.git" ] && [ "$(git -C "$dir" rev-parse HEAD 2>/dev/null || true)" = "$pin" ]; then
    echo "$a already at ${pin:0:12}"; exit 0
  fi
  tmp="$dir.partial"; rm -rf "$tmp"; mkdir -p "$tmp"
  git -C "$tmp" init -q
  git -C "$tmp" remote add origin "$url"
  if git -C "$tmp" fetch -q --depth 1 origin "$pin" 2>/dev/null; then
    git -C "$tmp" -c advice.detachedHead=false checkout -q FETCH_HEAD
  else
    # some servers refuse to fetch a commit by id: fall back to a partial clone of the history
    rm -rf "$tmp"
    git clone -q --filter=blob:none --no-checkout "$url" "$tmp"
    git -C "$tmp" -c advice.detachedHead=false checkout -q "$pin"
  fi
  rm -rf "$dir"; mv "$tmp" "$dir"
  echo "$a @ ${pin:0:12} (pinned)"
else
  tmp="$dir.partial"; rm -rf "$tmp"
  git clone -q --depth 1 "$url" "$tmp"
  sha=$(git -C "$tmp" rev-parse HEAD)
  date=$(git -C "$tmp" log -1 --format=%cs)
  rm -rf "$dir"; mv "$tmp" "$dir"
  echo "$sha $date" > "$pinf"
  echo "$a @ ${sha:0:12} ($date), pin written to corpus/pins/$a.txt"
fi
