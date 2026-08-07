# CachyCompanion

Lightweight system monitor + remote task-manager for **CachyOS** (and most AMD Linux boxes).
A tiny Python daemon serves live CPU/GPU/RAM stats and a token-gated process-kill endpoint;
the Android app (`net.wokeovis.cachycompanion`) shows graphs and lets you kill PC processes from your phone.

## Daemon

```bash
cd ~/cachycompanion
./install.sh          # copies config.example.json -> config.json if missing,
                       # installs psutil if needed, enables the user service,
                       # installs the `cachycompanion-manager` command + app menu entry
```

The `token` in `config.json` starts as the placeholder `CHANGE-ME-cachymon`. You normally don't need to
edit it by hand — `cachycompanion-manager` (below) generates a fresh one and pushes it to both the daemon and
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

`install.sh` puts a `cachycompanion-manager` command on your `PATH` (`~/.local/bin`) and a matching
**"CachyCompanionManager"** entry under System in your app menu — same tool, either launch path. No
argument opens an interactive menu; it also takes a subcommand directly, which is the friendlier form
over SSH on a headless box:

```bash
cachycompanion-manager push       # install/update the app on a USB-connected phone, then pair it
cachycompanion-manager token      # just generate a fresh pairing token (no phone/adb needed)
cachycompanion-manager port <N>   # change the daemon's listening port (default 5565)
```

Either `push` or `token` writes a fresh token into `config.json`, restarts the daemon, and prints a
QR code made of plain black/white terminal characters (`qrencode -t ANSIUTF8`, bootstrapped on first
run) — no image viewer or GUI required, so it renders the same over SSH as it does at the desktop. The
QR payload is `token@port`, so scanning it fills in both fields at once. In the app, tap the **QR
button** next to the token field and scan it; the raw token and port are also printed as a manual-entry
fallback.

- **`push`:** installs `adb`/USB udev rules on first run if missing (asks for `sudo` once), installs/updates
  `cachycompanion.apk` on the first physical device adb sees, opens an `adb reverse` tunnel on the
  configured port, then runs the token/QR step above and launches the app.
- **`token`:** use this when the app is already installed and you just need a new pairing — reconnecting
  over Wi-Fi, or after the token rotated. Point the app at `<pc-ip>:<port>` for LAN, or `127.0.0.1:<port>`
  if reusing an existing USB tunnel (the **USB** button in the app fills in the loopback host without
  touching whatever port you've already paired with).
- **`port <N>`:** rewrites `config.json`'s `port` and restarts the daemon. Run `token` (or `push`) right
  after so the phone re-pairs against the new port — old QR codes/tokens carry the previous port.
- **Manual USB (no script):** `adb install -r cachycompanion.apk && adb reverse tcp:<port> tcp:<port>`,
  then enter the token and port from `config.json` by hand.

Security: the daemon binds LAN-only by default and every control call needs the token. Keep it off the WAN.
The token rotates every time `cachycompanion-manager` generates one, so any other client using the old one
(a second phone, a saved bookmark) will need to be re-paired.
