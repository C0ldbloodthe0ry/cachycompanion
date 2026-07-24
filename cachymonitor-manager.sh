#!/usr/bin/env bash
# CachyMonitorManager — push the Android app to a USB-connected phone
# and/or generate a fresh QR pairing token for the daemon. Works over
# SSH on a headless box too: the QR renders as plain black/white
# terminal characters, no GUI or image viewer needed.
#
# Usage:
#   cachymonitor-manager           interactive menu
#   cachymonitor-manager push      install/update the app on a USB phone, then pair it
#   cachymonitor-manager token     generate + show a pairing token (no phone/adb needed)
set -e
# Resolve through the ~/.local/bin symlink installed by install.sh so this
# still finds its APK/config next to itself when invoked as a bare command.
DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
APK="$DIR/cachymonitor.apk"
CONFIG="$DIR/config.json"

[ -f "$CONFIG" ] || cp "$DIR/config.example.json" "$CONFIG"

usage() {
  echo "Usage: cachymonitor-manager [push|token]"
  echo "  push   install/update the app on a USB-connected phone, then pair it"
  echo "  token  generate a fresh pairing token and show its QR (no phone/adb needed)"
  echo "  (no argument) interactive menu"
}

ensure_qrencode() {
  command -v qrencode >/dev/null 2>&1 || {
    echo ">> first-time setup: installing qrencode (needs your sudo password)"
    sudo pacman -S --needed --noconfirm qrencode
  }
}

gen_token_and_show_qr() {
  ensure_qrencode
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
  echo
  qrencode -t ANSIUTF8 -m 2 "$TOKEN"
  echo
  echo ">> In the app: tap the camera button next to the token field and scan the code above."
  echo ">> token (fallback for manual entry): $TOKEN"
}

push_to_phone() {
  # Fresh CachyOS installs don't ship adb or the udev rules that let a
  # non-root user touch the phone over USB. Bootstrap both on first run.
  if ! command -v adb >/dev/null 2>&1 || ! pacman -Qq android-udev >/dev/null 2>&1; then
    echo ">> first-time setup: installing adb + USB device permissions (needs your sudo password)"
    sudo pacman -S --needed --noconfirm android-tools android-udev
    sudo udevadm control --reload-rules
    sudo udevadm trigger
    hash -r
  fi

  adb start-server >/dev/null 2>&1 || true
  # pick the first real device in 'device' state that isn't an emulator
  TARGET=$(adb devices | awk '$2=="device" && $1 !~ /^emulator-/ {print $1; exit}')
  if [ -z "$TARGET" ]; then
    echo ">> waiting for phone (plug in with USB debugging on, accept the debug prompt)..."
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

  gen_token_and_show_qr

  echo ">> launching"; adb shell monkey -p net.wokeovis.cachymonitor -c android.intent.category.LAUNCHER 1 >/dev/null 2>&1 || true
  echo ">> done. In the app, tap USB (127.0.0.1:5565) if host/port didn't autofill."
}

case "${1:-}" in
  push)
    push_to_phone
    ;;
  token)
    gen_token_and_show_qr
    ;;
  -h|--help)
    usage
    ;;
  "")
    echo "=== CachyMonitorManager ==="
    echo "1) Push app to phone (enable USB debugging first, then plug it in)"
    echo "2) Generate QR token only (app already installed, pairing over LAN or reusing USB)"
    echo "3) Quit"
    read -r -p "> " choice
    case "$choice" in
      1) push_to_phone ;;
      2) gen_token_and_show_qr ;;
      *) exit 0 ;;
    esac
    read -n 1 -s -r -p "Press any key to close..." || true
    echo
    ;;
  *)
    usage
    exit 1
    ;;
esac
