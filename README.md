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

The `token` in `config.json` starts as the placeholder `CHANGE-ME-cachymon`. You normally don't need to
edit it by hand — `phone-connect.sh` (below) generates a fresh one and pushes it to both the daemon and
the phone automatically. Other keys:

| key | meaning |
|-----|---------|
| `port` | TCP port (default 5565) |
| `token` | secret required for `/api/stats`, `/api/procs`, and `/api/kill` |
| `lan_only` | reject any client that isn't on a private/LAN/USB address |
| `gpu_card` | `auto` or a specific `cardN` |
| `process_count` | how many top processes `/api/stats` returns |
| `allow_kill` | set false to make it read-only |

### Endpoints
- `GET /api/health` — no token
- `GET /api/stats` — token; cpu/gpu/mem + top `process_count` processes by cpu/mem
- `GET /api/procs?q=<substring>` — token; every process whose name or pid matches (all of them if `q` is empty), for finding a small process that doesn't make the top list
- `POST /api/kill` `{"pid":N,"hard":false}` — token; SIGTERM (or SIGKILL if `hard`)

GPU stats come from amdgpu sysfs (auto-detected); CPU temp from k10temp/zenpower.

## Phone connection
- **App menu (recommended):** `install.sh` adds a **"CachyMonitor App Installer"** entry under System.
  Plug the phone in with USB debugging on and run it — it installs `adb`/USB permissions and `qrencode`
  on first run if they're missing (asks for `sudo` once), then installs `cachymonitor.apk`, opens the USB
  tunnel, generates a fresh token and writes it into `config.json`, restarts the daemon, and pops up a QR
  code of the token. In the app, tap the **📷 camera button** next to the token field and scan it — no
  typing required. The raw token is also printed in the terminal as a manual-entry fallback.
- **Manual USB:** `adb install -r cachymonitor.apk && adb reverse tcp:5565 tcp:5565`, then point the
  app at `127.0.0.1:5565` and enter the token from `config.json`.
- **Wi-Fi (same LAN):** point the app at `<pc-ip>:5565`. Do **not** port-forward it.

Security: the daemon binds LAN-only by default and every control call needs the token. Keep it off the WAN.
Note the token rotates every time `phone-connect.sh` runs, so any other client using the old one (a second
phone, a saved bookmark) will need to be re-paired.
