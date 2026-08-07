#!/usr/bin/env bash
# Install CachyCompanion as a per-user systemd service.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"

[ -f "$DIR/config.json" ] || cp "$DIR/config.example.json" "$DIR/config.json"

command -v python3 >/dev/null || { echo "python3 missing"; exit 1; }
python3 -c "import psutil" 2>/dev/null || {
  echo ">> installing psutil"; sudo pacman -S --noconfirm python-psutil; }

mkdir -p "$HOME/.config/systemd/user"
ln -sf "$DIR/cachycompanion.service" "$HOME/.config/systemd/user/cachycompanion.service"
systemctl --user daemon-reload
systemctl --user enable --now cachycompanion.service
sleep 1
systemctl --user --no-pager status cachycompanion.service | head -6
echo
echo ">> health:"; curl -s "http://127.0.0.1:$(python3 -c "import json;print(json.load(open('$DIR/config.json'))['port'])")/api/health"; echo
echo ">> Edit token/port in: $DIR/config.json  then: systemctl --user restart cachycompanion"

chmod +x "$DIR/cachycompanion-manager.sh"

mkdir -p "$HOME/.local/bin"
ln -sf "$DIR/cachycompanion-manager.sh" "$HOME/.local/bin/cachycompanion-manager"
case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *) echo ">> note: $HOME/.local/bin isn't on your PATH, add it to run 'cachycompanion-manager' directly" ;;
esac
echo ">> 'cachycompanion-manager' command installed (push app to phone / generate QR token, headless-friendly)"

mkdir -p "$HOME/.local/share/applications"
rm -f "$HOME/.local/share/applications/CachyMonitor-App-Installer.desktop"
rm -f "$HOME/.local/share/applications/CachyMonitorManager.desktop"
cat > "$HOME/.local/share/applications/CachyCompanionManager.desktop" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=CachyCompanionManager
Comment=Push the CachyCompanion Android app to a USB phone, or generate a QR pairing token
Exec=$DIR/cachycompanion-manager.sh
Icon=phone
Terminal=true
Categories=System;
EOF
update-desktop-database "$HOME/.local/share/applications" >/dev/null 2>&1 || true
echo ">> 'CachyCompanionManager' added to your app menu (System category)"
