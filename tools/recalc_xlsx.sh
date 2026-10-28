#!/usr/bin/env bash
# Recalculate the workbook's formulas with LibreOffice (headless) and store the values, in place.
# Excel computes the formulas itself on opening; this step is for viewers that do not, and for verify_xlsx.py.
# Usage: tools/recalc_xlsx.sh [build/java_tx_corpus.xlsx]     (exit 3: LibreOffice not installed)
set -eu
case "${1:-}" in -h|--help) sed -n 2,4p "$0"; exit 0;; esac
K="$(cd "$(dirname "$0")/.." && pwd)"
f="${1:-$K/build/java_tx_corpus.xlsx}"
soffice=$(command -v soffice || command -v libreoffice || true)
if [ -z "$soffice" ] && [ -x "/Applications/LibreOffice.app/Contents/MacOS/soffice" ]; then
  soffice="/Applications/LibreOffice.app/Contents/MacOS/soffice"
fi
# exit 3 when LibreOffice is missing, so "recalc_xlsx.sh && verify_xlsx.py" skips the check
[ -n "$soffice" ] || { echo "LibreOffice not found: values not stored (Excel computes the formulas on opening); skip verify_xlsx.py"; exit 3; }
tmp=$(mktemp -d)
"$soffice" --headless --calc --convert-to xlsx --outdir "$tmp" "$f" > /dev/null 2>&1
out="$tmp/$(basename "$f")"
[ -s "$out" ] || { echo "LibreOffice did not write $out" >&2; rm -rf "$tmp"; exit 1; }
mv "$out" "$f"; rm -rf "$tmp"
echo "recalculated $f"
