#!/bin/zsh
# FinePoint Perception: starts the app and opens it in the browser. Double-click this file, or click the FinePoint
# Perception app that install.command makes: the app runs this file in the background, with its output in
# ~/Library/Logs/FinePoint Perception.log. Uploads that a restart of the computer cut off carry on as soon as it runs.
cd "${0:A:h}" || exit 1
export PATH="$HOME/.pixi/bin:$PATH"
URL=http://127.0.0.1:3210
if curl -fs -o /dev/null "$URL"; then open "$URL"; exit 0; fi
[[ -f viewer/.next/BUILD_ID ]] || pixi run --frozen viewer-build || {
  echo "FinePoint Perception could not be built. Run install.command in this folder again."; [[ -t 0 ]] && read -k1; exit 1; }
pixi run --frozen -e core viewer &
for i in {1..150}; do curl -fs -o /dev/null "$URL" && break; sleep 0.2; done
open "$URL"
wait
