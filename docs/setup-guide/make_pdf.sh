#!/bin/sh
# Prints guide.html to docs/FinePoint-Perception-Setup-Guide.pdf with headless Chrome. On Windows, run Chrome (or Edge,
# msedge.exe) the same way from PowerShell in the app's folder:
#   & "$env:ProgramFiles\Google\Chrome\Application\chrome.exe" --headless=new --no-pdf-header-footer --disable-gpu `
#     "--user-data-dir=$env:TEMP\fpp-pdf" "--print-to-pdf=$PWD\docs\FinePoint-Perception-Setup-Guide.pdf" `
#     "file:///$($PWD -replace '\\','/')/docs/setup-guide/guide.html"
set -e
here="$(cd "$(dirname "$0")" && pwd)"
chrome="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
profile="$(mktemp -d)"
trap 'rm -rf "$profile"' EXIT
"$chrome" --headless=new --no-pdf-header-footer --disable-gpu --user-data-dir="$profile" \
  --print-to-pdf="$here/../FinePoint-Perception-Setup-Guide.pdf" "file://$here/guide.html"
echo "Wrote $(cd "$here/.." && pwd)/FinePoint-Perception-Setup-Guide.pdf"
