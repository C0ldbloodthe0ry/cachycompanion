#!/usr/bin/env bash
# CachyCompanionManager — push the Android app to a USB-connected phone
# and/or generate a fresh QR pairing token for the daemon. Works over
# SSH on a headless box too: the QR renders as plain black/white
# terminal characters, no GUI or image viewer needed.
#
# Usage:
#   cachycompanion-manager           interactive menu
#   cachycompanion-manager push      install/update the app on a USB phone, then pair it
#   cachycompanion-manager token     generate + show a pairing token (no phone/adb needed)
#   cachycompanion-manager port <N>  change the daemon's listening port
set -e
# Resolve through the ~/.local/bin symlink installed by install.sh so this
# still finds its APK/config next to itself when invoked as a bare command.
DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"

# Self-contained installs (install.sh's ~/cachycompanion layout) keep the APK
# and config.example.json next to this script. A distro package installs this
# script to /usr/bin and the read-only data files under /usr/share instead.
SYSTEM_SHARE="/usr/share/cachycompanion"
if [ -f "$DIR/cachycompanion.apk" ]; then
  APK="$DIR/cachycompanion.apk"
  EXAMPLE="$DIR/config.example.json"
  CONFIG="$DIR/config.json"
else
  APK="$SYSTEM_SHARE/cachycompanion.apk"
  EXAMPLE="$SYSTEM_SHARE/config.example.json"
  CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/cachycompanion/config.json"
fi

[ -f "$CONFIG" ] || { mkdir -p "$(dirname "$CONFIG")"; cp "$EXAMPLE" "$CONFIG"; }

current_port() {
  python3 -c "import json;print(json.load(open('$CONFIG'))['port'])"
}

usage() {
  echo "Usage: cachycompanion-manager [push|token|port <N>]"
  echo "  push     install/update the app on a USB-connected phone, then pair it"
  echo "  token    generate a fresh pairing token and show its QR (no phone/adb needed)"
  echo "  port <N> change the daemon's listening port (default 5565)"
  echo "  (no argument) interactive menu"
}

set_port() {
  local new_port="$1"
  case "$new_port" in
    ''|*[!0-9]*) echo "!! port must be a number"; exit 1 ;;
  esac
  if [ "$new_port" -lt 1 ] || [ "$new_port" -gt 65535 ]; then
    echo "!! port must be between 1 and 65535"; exit 1
  fi
  python3 - "$CONFIG" "$new_port" <<'PYEOF'
import json, sys
path, port = sys.argv[1], int(sys.argv[2])
with open(path) as f:
    cfg = json.load(f)
cfg["port"] = port
with open(path, "w") as f:
    json.dump(cfg, f, indent=2)
    f.write("\n")
PYEOF
  systemctl --user restart cachycompanion
  sleep 1
  echo ">> daemon now listening on :$new_port"
  echo ">> run 'cachycompanion-manager token' (or 'push') next so the phone re-pairs on the new port"
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
  systemctl --user restart cachycompanion
  sleep 1
  PORT="$(current_port)"
  echo
  qrencode -t ANSIUTF8 -m 2 "${TOKEN}@${PORT}"
  echo
  echo ">> In the app: tap the QR button next to the token field and scan the code above."
  echo "   (the code carries both the token and port :$PORT — the app fills in both fields)"
  echo ">> manual-entry fallback — token: $TOKEN   port: $PORT"
}

push_to_phone() {
  # Fresh Arch-based installs don't ship adb or the udev rules that let a
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
    adb uninstall net.wokeovis.cachycompanion >/dev/null 2>&1 || true
    adb install "$APK" | tail -1
  fi
  PORT="$(current_port)"
  echo ">> USB tunnel: phone 127.0.0.1:$PORT -> PC $PORT"; adb reverse "tcp:$PORT" "tcp:$PORT"

  gen_token_and_show_qr

  echo ">> launching"; adb shell monkey -p net.wokeovis.cachycompanion -c android.intent.category.LAUNCHER 1 >/dev/null 2>&1 || true
  echo ">> done. In the app, tap USB (127.0.0.1:$PORT) if host/port didn't autofill."
}

case "${1:-}" in
  push)
    push_to_phone
    ;;
  token)
    gen_token_and_show_qr
    ;;
  port)
    [ -n "${2:-}" ] || { echo "!! usage: cachycompanion-manager port <N>"; exit 1; }
    set_port "$2"
    ;;
  -h|--help)
    usage
    ;;
  "")
    echo "=== CachyCompanionManager ==="
    echo "current port: $(current_port)"
    echo "1) Push app to phone (enable USB debugging first, then plug it in)"
    echo "2) Generate QR token only (app already installed, pairing over LAN or reusing USB)"
    echo "3) Set a custom port"
    echo "4) Quit"
    read -r -p "> " choice
    case "$choice" in
      1) push_to_phone ;;
      2) gen_token_and_show_qr ;;
      3) read -r -p "new port: " newport; set_port "$newport" ;;
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
