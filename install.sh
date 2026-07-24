#!/usr/bin/env bash
# Install CachyMonitor as a per-user systemd service.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"

[ -f "$DIR/config.json" ] || cp "$DIR/config.example.json" "$DIR/config.json"

command -v python3 >/dev/null || { echo "python3 missing"; exit 1; }
python3 -c "import psutil" 2>/dev/null || {
  echo ">> installing psutil"; sudo pacman -S --noconfirm python-psutil; }

mkdir -p "$HOME/.config/systemd/user"
ln -sf "$DIR/cachymonitor.service" "$HOME/.config/systemd/user/cachymonitor.service"
systemctl --user daemon-reload
systemctl --user enable --now cachymonitor.service
sleep 1
systemctl --user --no-pager status cachymonitor.service | head -6
echo
echo ">> health:"; curl -s "http://127.0.0.1:$(python3 -c "import json;print(json.load(open('$DIR/config.json'))['port'])")/api/health"; echo
echo ">> Edit token/port in: $DIR/config.json  then: systemctl --user restart cachymonitor"

chmod +x "$DIR/cachymonitor-manager.sh"

mkdir -p "$HOME/.local/bin"
ln -sf "$DIR/cachymonitor-manager.sh" "$HOME/.local/bin/cachymonitor-manager"
case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *) echo ">> note: $HOME/.local/bin isn't on your PATH, add it to run 'cachymonitor-manager' directly" ;;
esac
echo ">> 'cachymonitor-manager' command installed (push app to phone / generate QR token, headless-friendly)"

mkdir -p "$HOME/.local/share/applications"
rm -f "$HOME/.local/share/applications/CachyMonitor-App-Installer.desktop"
cat > "$HOME/.local/share/applications/CachyMonitorManager.desktop" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=CachyMonitorManager
Comment=Push the CachyMonitor Android app to a USB phone, or generate a QR pairing token
Exec=$DIR/cachymonitor-manager.sh
Icon=phone
Terminal=true
Categories=System;
EOF
update-desktop-database "$HOME/.local/share/applications" >/dev/null 2>&1 || true
echo ">> 'CachyMonitorManager' added to your app menu (System category)"
