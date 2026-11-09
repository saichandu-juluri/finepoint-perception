#!/bin/zsh
# Installs FinePoint Perception on this Mac, makes "FinePoint Perception.app" and opens it once. It asks nothing (the
# server address, email and password go into the app's sign-in) and is safe to run again. The README's one line runs
# it right after git clone. FPP_APPS_DIR names another folder for the app (tests); the default is ~/Applications.
cd "${0:A:h}" || exit 1
fail() { print -r -- "$1"; exit 1; }
again="to try again, paste this line into Terminal: ${(q-)${0:A}}"

[[ $(sysctl -n hw.optional.arm64 2>/dev/null) == 1 ]] || fail "FinePoint Perception needs a Mac with an Apple chip (M1 or newer)."
(( ${$(sw_vers -productVersion)%%.*} >= 14 )) || fail "FinePoint Perception needs macOS 14 or newer."

PIXI=$HOME/.pixi/bin/pixi
if [[ ! -x $PIXI ]]; then   # the official installer, with the pixi version that made pixi.lock
  curl -fsSL https://pixi.sh/install.sh | PIXI_VERSION=0.83.0 sh && [[ -x $PIXI ]] || fail "Could not install pixi, the tool that installs the app (check the internet connection); $again"
fi

# A running copy of the app would go on serving the old build: stop it, but only the server started from this folder.
for pid in $(lsof -nP -tiTCP:3210 -sTCP:LISTEN 2>/dev/null); do
  lsof -a -p $pid -d cwd -Fn 2>/dev/null | grep -qxF "n$PWD/viewer" && kill $pid
done

"$PIXI" install -e core --locked || fail "Could not install what the app needs (check the internet connection, and that about 3 GB are free); $again"
"$PIXI" run --frozen -e core viewer-build || fail "Could not build the app; $again"

# The app runs start-finepoint-perception.command in the background and returns at once. do shell script starts with
# PATH /usr/bin:/bin:/usr/sbin:/sbin, so every path in it is absolute.
APPS=${FPP_APPS_DIR:-$HOME/Applications}
APP="$APPS/FinePoint Perception.app"
LOG="$HOME/Library/Logs/FinePoint Perception.log"
bin=$HOME/.pixi/bin start=$PWD/start-finepoint-perception.command
cmd="PATH=${(qq)bin}:\$PATH; export PATH; /bin/zsh ${(qq)start} >> ${(qq)LOG} 2>&1 < /dev/null &"
cmd=${cmd//\\/\\\\}; cmd=${cmd//\"/\\\"}
mkdir -p "$APPS" "${LOG:h}" && rm -rf "$APP" && osacompile -o "$APP" -e "do shell script \"$cmd\"" 2>/dev/null || fail "Could not make the app in $APPS; $again"

# Its icon is the web app's icon, drawn on Apple's icon grid. If a tool here cannot draw it, the app keeps the default icon.
ok=0
t=$(mktemp -d) && {
  sed 's/width="32" height="32" viewBox="0 0 28 28"/width="1024" height="1024" viewBox="-3.4 -3.4 34.8 34.8"/' viewer/src/app/icon.svg > $t/icon.svg &&
    sips -s format png $t/icon.svg --out $t/icon.png >/dev/null 2>&1 && mkdir $t/icon.iconset && ok=1
  for s in 16 32 128 256 512; do
    (( ok )) && sips -z $s $s $t/icon.png --out $t/icon.iconset/icon_${s}x$s.png >/dev/null 2>&1 &&
      sips -z $((2 * s)) $((2 * s)) $t/icon.png --out $t/icon.iconset/icon_${s}x$s@2x.png >/dev/null 2>&1 || ok=0
  done
  (( ok )) && iconutil -c icns -o $t/icon.icns $t/icon.iconset 2>/dev/null &&
    cp $t/icon.icns "$APP/Contents/Resources/applet.icns" && rm -f "$APP/Contents/Resources/Assets.car" &&
    plutil -remove CFBundleIconName "$APP/Contents/Info.plist"
} always { rm -rf $t }
codesign --force --deep --sign - "$APP" 2>/dev/null && touch "$APP" || fail "Could not make the app in $APPS; $again"

open "$APP" || fail "Could not open the app; to start it another way, double-click start-finepoint-perception.command in ${(q-)PWD}."
print "FinePoint Perception is installed and opens in your browser. Next time, click FinePoint Perception in Launchpad"
print "(Apps on newer Macs), or in the Applications folder of your home folder."
