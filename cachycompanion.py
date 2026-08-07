#!/usr/bin/env python3
"""
CachyCompanion daemon  -  lightweight system-monitor + remote task-manager for Arch Linux
(and derivatives — CachyOS, EndeavourOS, Manjaro, etc.) and most other systemd Linux boxes.
Serves a JSON stats snapshot and a token-gated process-kill endpoint over LAN / USB(adb reverse).

Endpoints:
  GET  /api/health                 -> {"ok": true, "host": "..."}              (no token)
  GET  /api/stats                  -> full snapshot (cpu/gpu/mem/top procs)    (token)
  GET  /api/procs?q=<substring>    -> every process matching name/pid, capped  (token)
  POST /api/kill   {"pid":N,"hard":false}                                      (token)

Auth: header  X-CachyMon-Token: <token from config.json>
Only one dependency: psutil.  GPU stats: AMD and Intel are auto-detected by PCI vendor ID
and read from sysfs (amdgpu / i915-xe); NVIDIA is detected via `nvidia-smi` on PATH, since
the proprietary driver doesn't expose amdgpu-style sysfs. AMD is developed and tested on
real hardware; the Intel and NVIDIA paths are implemented from public driver/tool docs only
and have not been run against real Intel or NVIDIA hardware — see read_gpu() for details.
"""
import json
import os
import shutil
import signal
import socket
import subprocess
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs

try:
    import psutil
except ImportError:
    raise SystemExit("CachyCompanion needs psutil:  sudo pacman -S python-psutil")

HERE = os.path.dirname(os.path.abspath(__file__))
# Self-contained installs (install.sh's ~/cachycompanion layout) keep config.json
# next to the script. A distro package installs the script read-only under /usr,
# so it falls back to the XDG config dir instead — same script, either layout.
_XDG_CONFIG = os.path.join(
    os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")),
    "cachycompanion", "config.json")
_COLOCATED_CONFIG = os.path.join(HERE, "config.json")
CONFIG_PATH = (os.environ.get("CACHYCOMPANION_CONFIG")
               or (_COLOCATED_CONFIG if os.path.exists(_COLOCATED_CONFIG) else _XDG_CONFIG))


def load_config():
    cfg = {
        "bind": "0.0.0.0", "port": 5565, "token": "CHANGE-ME-cachymon",
        "hostname": "", "lan_only": True, "gpu_card": "auto",
        "process_count": 40, "allow_kill": True,
    }
    try:
        with open(CONFIG_PATH) as f:
            cfg.update({k: v for k, v in json.load(f).items() if not k.startswith("_")})
    except FileNotFoundError:
        pass
    if not cfg.get("hostname"):
        cfg["hostname"] = socket.gethostname()
    return cfg


# ---------- GPU (AMD/Intel via sysfs, NVIDIA via nvidia-smi) ----------

_PCI_VENDOR = {"0x1002": "amd", "0x8086": "intel"}  # NVIDIA (0x10de) has no usable sysfs


def find_gpu(pref="auto"):
    """Return (vendor, dev_or_None) for the GPU to monitor.

    AMD/Intel are identified by PCI vendor ID under /sys/class/drm/cardN/device and read
    from sysfs. NVIDIA's proprietary driver doesn't expose amdgpu-style sysfs, so it's
    detected instead by the presence of `nvidia-smi` on PATH (dev is None in that case;
    read_gpu() shells out per-poll rather than reading a fixed sysfs path).
    """
    base = "/sys/class/drm"
    cands = []
    try:
        for name in sorted(os.listdir(base)):
            if not name.startswith("card") or "-" in name:
                continue
            dev = os.path.join(base, name, "device")
            vendor = _PCI_VENDOR.get(_read(os.path.join(dev, "vendor")))
            if vendor:
                cands.append((name, vendor, dev))
    except FileNotFoundError:
        pass
    if pref not in ("auto", "", None):
        for name, vendor, dev in cands:
            if name == pref:
                return vendor, dev
    if cands:
        return cands[0][1], cands[0][2]
    if shutil.which("nvidia-smi"):
        return "nvidia", None
    return None, None


def _read(path, default=None):
    try:
        with open(path) as f:
            return f.read().strip()
    except (OSError, ValueError):
        return default


def _read_int(path, div=1, default=None):
    v = _read(path)
    try:
        return int(int(v) / div)
    except (TypeError, ValueError):
        return default


def _current_clock(path):
    """Parse a pp_dpm_* table; return the active (starred) clock in MHz."""
    txt = _read(path)
    if not txt:
        return None
    for line in txt.splitlines():
        if "*" in line:
            for tok in line.replace(":", " ").split():
                t = tok.lower()
                if t.endswith("mhz"):
                    try:
                        return int(t[:-3])
                    except ValueError:
                        return None
    return None


def _read_hwmon_stats(dev):
    """temp/power/fan from the first hwmon child under a DRM device's sysfs dir, if any.
    Shared by the AMD and Intel readers — both amdgpu and i915/xe register a standard
    hwmon device when the card exposes power/thermal sensors (mainly discrete cards)."""
    stats = {}
    hwdir = os.path.join(dev, "hwmon")
    if not os.path.isdir(hwdir):
        return stats
    subs = os.listdir(hwdir)
    if not subs:
        return stats
    hw = os.path.join(hwdir, sorted(subs)[0])
    for i in range(1, 6):
        lbl = (_read(os.path.join(hw, f"temp{i}_label")) or "").lower()
        val = _read_int(os.path.join(hw, f"temp{i}_input"), div=1000)
        if val is None:
            continue
        if "junction" in lbl or "hotspot" in lbl:
            stats["temp_junction_c"] = val
        elif "mem" in lbl:
            stats["temp_mem_c"] = val
        elif "edge" in lbl or i == 1:
            stats["temp_edge_c"] = val
    stats["power_w"] = _read_int(os.path.join(hw, "power1_average"), div=1_000_000)
    stats["fan_rpm"] = _read_int(os.path.join(hw, "fan1_input"))
    return stats


def _read_gpu_amd(dev):
    g = {"name": _read(os.path.join(dev, "product_name")) or "AMD GPU"}
    g["busy_pct"] = _read_int(os.path.join(dev, "gpu_busy_percent"))
    g["vram_total_mb"] = _read_int(os.path.join(dev, "mem_info_vram_total"), div=1024 * 1024)
    g["vram_used_mb"] = _read_int(os.path.join(dev, "mem_info_vram_used"), div=1024 * 1024)
    g["sclk_mhz"] = _current_clock(os.path.join(dev, "pp_dpm_sclk"))
    g["mclk_mhz"] = _current_clock(os.path.join(dev, "pp_dpm_mclk"))
    g.update(_read_hwmon_stats(dev))
    return g


def _read_gpu_intel(dev):
    """UNTESTED: written from public i915/xe kernel sysfs docs, no Intel GPU on hand to
    verify against. gt_act_freq_mhz/gt_cur_freq_mhz are i915 attributes (older kernels /
    non-Arc parts); the newer `xe` driver (Arc, Meteor Lake+) uses a different tile/gt sysfs
    layout that isn't handled here and will just come back with no clock reading. There is
    no standard sysfs busy% (would need debugfs as root, or intel_gpu_top) and integrated
    Intel GPUs share system RAM, so busy_pct/vram are intentionally omitted rather than
    guessed. hwmon (temp/power) only exists on discrete Arc cards."""
    g = {"name": "Intel GPU"}
    g["sclk_mhz"] = (_read_int(os.path.join(dev, "gt_act_freq_mhz"))
                      or _read_int(os.path.join(dev, "gt_cur_freq_mhz")))
    g.update(_read_hwmon_stats(dev))
    return g


def _read_gpu_nvidia():
    """UNTESTED: written from the documented nvidia-smi CSV query interface, no NVIDIA
    hardware on hand to verify against. Requires the proprietary driver's nvidia-smi to be
    on PATH; that's a heavier dependency than sysfs reads but it's the only reliable source
    of these stats since NVIDIA doesn't expose an amdgpu-style sysfs."""
    try:
        out = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=name,utilization.gpu,memory.total,memory.used,temperature.gpu,power.draw,clocks.sm",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=2,
        ).stdout.strip().splitlines()
        if not out:
            return None
        name, busy, vt, vu, temp, power, sclk = (x.strip() for x in out[0].split(","))
        return {
            "name": name,
            "busy_pct": int(float(busy)),
            "vram_total_mb": int(float(vt)),
            "vram_used_mb": int(float(vu)),
            "temp_edge_c": int(float(temp)),
            "power_w": int(float(power)),
            "sclk_mhz": int(float(sclk)),
        }
    except Exception:
        return None


def read_gpu(vendor, dev):
    if vendor == "amd":
        return _read_gpu_amd(dev)
    if vendor == "intel":
        return _read_gpu_intel(dev)
    if vendor == "nvidia":
        return _read_gpu_nvidia()
    return None


# ---------- CPU / MEM / PROC ----------

def read_cpu():
    c = {}
    c["usage_pct"] = psutil.cpu_percent(interval=None)
    c["per_core"] = psutil.cpu_percent(interval=None, percpu=True)
    c["cores"] = psutil.cpu_count(logical=True)
    try:
        fr = psutil.cpu_freq()
        if fr:
            c["freq_mhz"] = round(fr.current)
            c["freq_max_mhz"] = round(fr.max) if fr.max else None
    except Exception:
        pass
    # AMD k10temp/zenpower (Tctl/Tccd1) or Intel coretemp (Package id 0), else first available
    try:
        temps = psutil.sensors_temperatures()
        pick = None
        for key in ("k10temp", "coretemp", "zenpower"):
            if key in temps and temps[key]:
                for e in temps[key]:
                    if e.label in ("Tctl", "Tccd1", "Package id 0", ""):
                        pick = e.current
                        break
                pick = pick or temps[key][0].current
                break
        c["temp_c"] = round(pick) if pick is not None else None
    except Exception:
        c["temp_c"] = None
    return c


def read_mem():
    m = psutil.virtual_memory()
    return {"used_mb": int(m.used / 1024 / 1024),
            "total_mb": int(m.total / 1024 / 1024),
            "pct": m.percent}


_proc_cache = {}  # pid -> (cpu_time_total, wall_ts), to compute %CPU across polls


def read_procs(n):
    now = time.time()
    ncpu = psutil.cpu_count() or 1
    rows, seen = [], set()
    for p in psutil.process_iter(["pid", "name", "memory_info", "cpu_times"]):
        try:
            info = p.info
            pid = info["pid"]
            seen.add(pid)
            ct = info["cpu_times"]
            tot = (ct.user + ct.system) if ct else 0.0
            cpu = 0.0
            prev = _proc_cache.get(pid)
            if prev:
                dt = now - prev[1]
                if dt > 0:
                    cpu = max(0.0, (tot - prev[0]) / dt / ncpu * 100.0)
            _proc_cache[pid] = (tot, now)
            mem = info["memory_info"]
            rows.append({
                "pid": pid,
                "name": info["name"] or "?",
                "cpu_pct": round(cpu, 1),
                "mem_mb": int(mem.rss / 1024 / 1024) if mem else 0,
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    for pid in [p for p in _proc_cache if p not in seen]:
        del _proc_cache[pid]
    rows.sort(key=lambda x: (x["cpu_pct"], x["mem_mb"]), reverse=True)
    return rows[:n]


def read_all_procs(query=None, cap=500):
    """Every running process, optionally filtered by name/pid substring. Sorted by name."""
    now = time.time()
    ncpu = psutil.cpu_count() or 1
    q = (query or "").strip().lower()
    rows, seen = [], set()
    for p in psutil.process_iter(["pid", "name", "memory_info", "cpu_times"]):
        try:
            info = p.info
            pid = info["pid"]
            seen.add(pid)
            name = info["name"] or "?"
            if q and q not in name.lower() and q != str(pid):
                continue
            ct = info["cpu_times"]
            tot = (ct.user + ct.system) if ct else 0.0
            cpu = 0.0
            prev = _proc_cache.get(pid)
            if prev:
                dt = now - prev[1]
                if dt > 0:
                    cpu = max(0.0, (tot - prev[0]) / dt / ncpu * 100.0)
            _proc_cache[pid] = (tot, now)
            mem = info["memory_info"]
            rows.append({
                "pid": pid,
                "name": name,
                "cpu_pct": round(cpu, 1),
                "mem_mb": int(mem.rss / 1024 / 1024) if mem else 0,
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    for pid in [p for p in _proc_cache if p not in seen]:
        del _proc_cache[pid]
    rows.sort(key=lambda x: x["name"].lower())
    return rows[:cap]


# ---------- HTTP ----------

class Handler(BaseHTTPRequestHandler):
    cfg = None
    gpu_vendor = None
    gpu_dev = None

    def log_message(self, *a):
        pass  # quiet

    def _client_is_lan(self):
        ip = self.client_address[0]
        return (ip.startswith("127.") or ip.startswith("10.") or
                ip.startswith("192.168.") or
                any(ip.startswith(f"172.{x}.") for x in range(16, 32)) or
                ip in ("::1", "localhost"))

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _authed(self):
        return self.headers.get("X-CachyMon-Token", "") == self.cfg["token"]

    def do_GET(self):
        if self.cfg["lan_only"] and not self._client_is_lan():
            return self._send(403, {"error": "lan_only"})
        if self.path == "/api/health":
            return self._send(200, {"ok": True, "host": self.cfg["hostname"]})
        if self.path == "/api/stats":
            if not self._authed():
                return self._send(401, {"error": "bad token"})
            return self._send(200, {
                "ts": int(time.time()),
                "host": self.cfg["hostname"],
                "cpu": read_cpu(),
                "mem": read_mem(),
                "gpu": read_gpu(self.gpu_vendor, self.gpu_dev),
                "procs": read_procs(self.cfg["process_count"]),
            })
        if self.path == "/api/procs" or self.path.startswith("/api/procs?"):
            if not self._authed():
                return self._send(401, {"error": "bad token"})
            q = parse_qs(urlsplit(self.path).query).get("q", [""])[0]
            rows = read_all_procs(q)
            return self._send(200, {"ts": int(time.time()), "count": len(rows), "procs": rows})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.cfg["lan_only"] and not self._client_is_lan():
            return self._send(403, {"error": "lan_only"})
        if not self._authed():
            return self._send(401, {"error": "bad token"})
        if self.path != "/api/kill":
            return self._send(404, {"error": "not found"})
        if not self.cfg.get("allow_kill", True):
            return self._send(403, {"error": "kill disabled in config"})
        try:
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n) or b"{}")
            pid = int(body["pid"])
            hard = bool(body.get("hard", False))
        except Exception as e:
            return self._send(400, {"error": f"bad body: {e}"})
        if pid in (0, 1) or pid == os.getpid():
            return self._send(400, {"error": "refused (protected pid)"})
        try:
            name = psutil.Process(pid).name()
            os.kill(pid, signal.SIGKILL if hard else signal.SIGTERM)
            return self._send(200, {"ok": True, "pid": pid, "name": name,
                                    "signal": "SIGKILL" if hard else "SIGTERM"})
        except psutil.NoSuchProcess:
            return self._send(404, {"error": "no such pid"})
        except PermissionError:
            return self._send(403, {"error": "permission denied (process not owned by daemon user)"})
        except Exception as e:
            return self._send(500, {"error": str(e)})


def main():
    cfg = load_config()
    Handler.cfg = cfg
    Handler.gpu_vendor, Handler.gpu_dev = find_gpu(cfg.get("gpu_card", "auto"))
    # prime cpu_percent so first sample isn't 0/garbage
    psutil.cpu_percent(interval=None)
    psutil.cpu_percent(interval=None, percpu=True)
    srv = ThreadingHTTPServer((cfg["bind"], cfg["port"]), Handler)
    print(f"CachyCompanion on {cfg['bind']}:{cfg['port']}  host={cfg['hostname']}  "
          f"gpu={Handler.gpu_vendor or 'none'}  lan_only={cfg['lan_only']}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == "__main__":
    main()
