#!/usr/bin/env python3
"""
CachyMonitor daemon  -  lightweight system-monitor + remote task-manager for CachyOS.
Serves a JSON stats snapshot and a token-gated process-kill endpoint over LAN / USB(adb reverse).

Endpoints:
  GET  /api/health                 -> {"ok": true, "host": "..."}              (no token)
  GET  /api/stats                  -> full snapshot (cpu/gpu/mem/top procs)    (token)
  GET  /api/procs?q=<substring>    -> every process matching name/pid, capped  (token)
  POST /api/kill   {"pid":N,"hard":false}                                      (token)

Auth: header  X-CachyMon-Token: <token from config.json>
Only one dependency: psutil.  GPU stats come from amdgpu sysfs (auto-detected).
"""
import json
import os
import signal
import socket
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs

try:
    import psutil
except ImportError:
    raise SystemExit("CachyMonitor needs psutil:  sudo pacman -S python-psutil")

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")


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


# ---------- GPU (amdgpu sysfs) ----------

def find_gpu_card(pref="auto"):
    """Return the sysfs device path of an amdgpu render card, or None."""
    base = "/sys/class/drm"
    cands = []
    try:
        for name in sorted(os.listdir(base)):
            if not name.startswith("card") or "-" in name:
                continue
            dev = os.path.join(base, name, "device")
            if os.path.exists(os.path.join(dev, "gpu_busy_percent")):
                cands.append((name, dev))
    except FileNotFoundError:
        return None
    if pref not in ("auto", "", None):
        for name, dev in cands:
            if name == pref:
                return dev
    return cands[0][1] if cands else None


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


def read_gpu(dev):
    if not dev:
        return None
    g = {"name": "AMD GPU"}
    # name from the marketing label if present
    g["name"] = _read(os.path.join(dev, "product_name")) or g["name"]
    g["busy_pct"] = _read_int(os.path.join(dev, "gpu_busy_percent"))
    vt = _read_int(os.path.join(dev, "mem_info_vram_total"), div=1024 * 1024)
    vu = _read_int(os.path.join(dev, "mem_info_vram_used"), div=1024 * 1024)
    g["vram_total_mb"], g["vram_used_mb"] = vt, vu
    g["sclk_mhz"] = _current_clock(os.path.join(dev, "pp_dpm_sclk"))
    g["mclk_mhz"] = _current_clock(os.path.join(dev, "pp_dpm_mclk"))
    # hwmon: temps / power / fan
    hw = None
    hwdir = os.path.join(dev, "hwmon")
    if os.path.isdir(hwdir):
        subs = os.listdir(hwdir)
        if subs:
            hw = os.path.join(hwdir, sorted(subs)[0])
    if hw:
        for i in range(1, 6):
            lbl = (_read(os.path.join(hw, f"temp{i}_label")) or "").lower()
            val = _read_int(os.path.join(hw, f"temp{i}_input"), div=1000)
            if val is None:
                continue
            if "junction" in lbl or "hotspot" in lbl:
                g["temp_junction_c"] = val
            elif "mem" in lbl:
                g["temp_mem_c"] = val
            elif "edge" in lbl or i == 1:
                g["temp_edge_c"] = val
        g["power_w"] = _read_int(os.path.join(hw, "power1_average"), div=1_000_000)
        g["fan_rpm"] = _read_int(os.path.join(hw, "fan1_input"))
    return g


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
    # AMD k10temp Tctl, else first available
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
                "gpu": read_gpu(self.gpu_dev),
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
    Handler.gpu_dev = find_gpu_card(cfg.get("gpu_card", "auto"))
    # prime cpu_percent so first sample isn't 0/garbage
    psutil.cpu_percent(interval=None)
    psutil.cpu_percent(interval=None, percpu=True)
    srv = ThreadingHTTPServer((cfg["bind"], cfg["port"]), Handler)
    print(f"CachyMonitor on {cfg['bind']}:{cfg['port']}  host={cfg['hostname']}  "
          f"gpu={'yes' if Handler.gpu_dev else 'none'}  lan_only={cfg['lan_only']}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == "__main__":
    main()
