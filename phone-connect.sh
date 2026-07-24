#!/usr/bin/env bash
# Plug in the phone (USB debugging ON + authorized), then run this:
#   installs/updates the app, opens the USB tunnel, generates a fresh
#   daemon token, and shows it as a QR code for the app's camera button
#   to scan.
# Auto-selects the physical phone, ignoring any offline emulator.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
APK="$DIR/cachymonitor.apk"
CONFIG="$DIR/config.json"

[ -f "$CONFIG" ] || cp "$DIR/config.example.json" "$CONFIG"

# Fresh CachyOS installs don't ship adb or the udev rules that let a
# non-root user touch the phone over USB. Bootstrap both on first run.
if ! command -v adb >/dev/null 2>&1 || ! pacman -Qq android-udev >/dev/null 2>&1; then
  echo ">> first-time setup: installing adb + USB device permissions (needs your sudo password)"
  sudo pacman -S --needed --noconfirm android-tools android-udev
  sudo udevadm control --reload-rules
  sudo udevadm trigger
  hash -r
fi

# QR display for the fresh token needs qrencode.
command -v qrencode >/dev/null 2>&1 || {
  echo ">> first-time setup: installing qrencode (needs your sudo password)"
  sudo pacman -S --needed --noconfirm qrencode
}

adb start-server >/dev/null 2>&1 || true
# pick the first real device in 'device' state that isn't an emulator
TARGET=$(adb devices | awk '$2=="device" && $1 !~ /^emulator-/ {print $1; exit}')
if [ -z "$TARGET" ]; then
  echo ">> waiting for phone (plug in, accept the debug prompt)..."
  for i in $(seq 1 30); do
    TARGET=$(adb devices | awk '$2=="device" && $1 !~ /^emulator-/ {print $1; exit}')
    [ -n "$TARGET" ] && break; sleep 1
  done
fi
if [ -z "$TARGET" ]; then
  echo "!! no physical device in 'device' state. 'adb devices' shows:"; adb devices
  exit 1
fi
export ANDROID_SERIAL="$TARGET"
echo ">> target: $ANDROID_SERIAL"

echo ">> installing"
if ! adb install -r "$APK" 2>&1 | tail -1 | grep -q Success; then
  echo ">> signature mismatch or bad state, reinstalling clean"
  adb uninstall net.wokeovis.cachymonitor >/dev/null 2>&1 || true
  adb install "$APK" | tail -1
fi
echo ">> USB tunnel: phone 127.0.0.1:5565 -> PC 5565"; adb reverse tcp:5565 tcp:5565

echo ">> generating a fresh token"
TOKEN=$(python3 -c "import secrets; print(secrets.token_hex(10))")
python3 - "$CONFIG" "$TOKEN" <<'PYEOF'
import json, sys
path, token = sys.argv[1], sys.argv[2]
with open(path) as f:
    cfg = json.load(f)
cfg["token"] = token
with open(path, "w") as f:
    json.dump(cfg, f, indent=2)
    f.write("\n")
PYEOF
systemctl --user restart cachymonitor
sleep 1

echo ">> launching"; adb shell monkey -p net.wokeovis.cachymonitor -c android.intent.category.LAUNCHER 1 >/dev/null 2>&1 || true

QR_PNG="/tmp/cachymonitor-token-qr.png"
qrencode -o "$QR_PNG" -s 10 -m 2 "$TOKEN"
xdg-open "$QR_PNG" >/dev/null 2>&1 &

echo ">> done. In the app: tap USB (127.0.0.1:5565), then tap the camera button next to the"
echo "   token field and scan the QR code that just opened."
echo ">> token (fallback for manual entry): $TOKEN"
read -n 1 -s -r -p "Press any key to close..." || true
echo
