#!/usr/bin/env bash
# Plug in the Pixel (USB debugging ON + authorized), then run this:
#   installs/updates the app, opens the USB tunnel, launches it.
# Auto-selects the physical phone, ignoring any offline emulator.
set -e
APK=/home/zero/apkproject/releases/cachymonitor.apk

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

echo ">> installing"; adb install -r "$APK" | tail -1
echo ">> USB tunnel: phone 127.0.0.1:5565 -> PC 5565"; adb reverse tcp:5565 tcp:5565
echo ">> launching"; adb shell monkey -p net.wokeovis.cachymonitor -c android.intent.category.LAUNCHER 1 >/dev/null 2>&1 || true
echo ">> done. In the app, tap USB (127.0.0.1:5565). Default token: CHANGE-ME-cachymon"
