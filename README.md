# CachyMonitor

Lightweight system monitor + remote task-manager for **CachyOS** (and most AMD Linux boxes).
A tiny Python daemon serves live CPU/GPU/RAM stats and a token-gated process-kill endpoint;
the Android app (`net.wokeovis.cachymonitor`) shows graphs and lets you kill PC processes from your phone.

## Daemon

```bash
cd ~/cachymonitor
./install.sh          # copies config.example.json -> config.json if missing,
                       # installs psutil if needed, enables the user service,
                       # adds "CachyMonitor App Installer" to your app menu
```

Edit `config.json` (**set a real `token`**, it ships as `CHANGE-ME-cachymon`) then `systemctl --user restart cachymonitor`:

| key | meaning |
|-----|---------|
| `port` | TCP port (default 5565) |
| `token` | secret required for `/api/stats` and `/api/kill` — **change this** |
| `lan_only` | reject any client that isn't on a private/LAN/USB address |
| `gpu_card` | `auto` or a specific `cardN` |
| `process_count` | how many top processes to return |
| `allow_kill` | set false to make it read-only |

### Endpoints
- `GET /api/health` — no token
- `GET /api/stats` — token (`X-CachyMon-Token` header)
- `POST /api/kill` `{"pid":N,"hard":false}` — token; SIGTERM (or SIGKILL if `hard`)

GPU stats come from amdgpu sysfs (auto-detected); CPU temp from k10temp/zenpower.

## Phone connection
- **App menu (recommended):** `install.sh` adds a **"CachyMonitor App Installer"** entry under System.
  Plug the phone in with USB debugging on and run it — it installs `adb`/USB permissions on first
  run if they're missing (asks for `sudo` once), then installs `cachymonitor.apk`, opens the USB
  tunnel, and launches the app.
- **Manual USB:** `adb install -r cachymonitor.apk && adb reverse tcp:5565 tcp:5565`, then point the
  app at `127.0.0.1:5565`.
- **Wi-Fi (same LAN):** point the app at `<pc-ip>:5565`. Do **not** port-forward it.

Security: the daemon binds LAN-only by default and every control call needs the token. Keep it off the WAN.
