#!/usr/bin/env python3
"""
macmini-agent.py

Runs on each Mac mini. Serves a small JSON endpoint with CPU, memory, disk,
temperature and uptime stats. Meant to be reached only over your Tailscale
network (don't expose this port to the public internet).

Endpoint: GET http://<tailscale-ip-or-name>:8787/metrics

Requires: psutil
    pip3 install psutil --break-system-packages

Optional (for CPU temperature):
    brew install narugit/tap/smctemp     # works on Apple Silicon
    or
    brew install osx-cpu-temp            # Intel Macs
"""

import json
import platform
import shutil
import socket
import subprocess
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    import psutil
except ImportError:
    raise SystemExit(
        "psutil is required. Install with: pip3 install psutil --break-system-packages"
    )

PORT = 8787
START_TIME = time.time()

# launchd services get a minimal PATH, so CLI tools installed via Homebrew
# aren't found by bare name. Use full paths (check with `which <tool>` on
# each machine and update here if yours differ).
SMCTEMP_BIN = "/opt/homebrew/bin/smctemp"
OSX_CPU_TEMP_BIN = "/usr/local/bin/osx-cpu-temp"
TAILSCALE_BIN = "/opt/homebrew/bin/tailscale"


def get_temperature_c():
    """Try a few CLI tools to read CPU temperature. Returns float or None."""
    # smctemp: works on Apple Silicon (M1/M2/M3/M4)
    try:
        out = subprocess.run(
            [SMCTEMP_BIN, "-c"], capture_output=True, text=True, timeout=3
        )
        if out.returncode == 0 and out.stdout.strip():
            return round(float(out.stdout.strip()), 1)
    except Exception:
        pass

    # osx-cpu-temp: works on Intel Macs
    try:
        out = subprocess.run(
            [OSX_CPU_TEMP_BIN], capture_output=True, text=True, timeout=3
        )
        if out.returncode == 0 and out.stdout.strip():
            # output like "55.2°C"
            val = out.stdout.strip().replace("°C", "").replace("C", "")
            return round(float(val), 1)
    except Exception:
        pass

    return None


def get_tailscale_ip():
    try:
        out = subprocess.run(
            [TAILSCALE_BIN, "ip", "-4"], capture_output=True, text=True, timeout=3
        )
        if out.returncode == 0:
            return out.stdout.strip().splitlines()[0]
    except Exception:
        pass
    return None


def collect_metrics():
    cpu_percent = psutil.cpu_percent(interval=0.5)
    cpu_per_core = psutil.cpu_percent(interval=0, percpu=True)
    mem = psutil.virtual_memory()
    disk = shutil.disk_usage("/")
    boot_time = psutil.boot_time()
    uptime_seconds = time.time() - boot_time

    load1, load5, load15 = (None, None, None)
    try:
        load1, load5, load15 = psutil.getloadavg()
    except Exception:
        pass

    return {
        "hostname": socket.gethostname(),
        "tailscale_ip": get_tailscale_ip(),
        "platform": platform.platform(),
        "arch": platform.machine(),
        "timestamp": int(time.time()),
        "cpu": {
            "percent": cpu_percent,
            "per_core_percent": cpu_per_core,
            "load_avg": {"1m": load1, "5m": load5, "15m": load15},
        },
        "memory": {
            "total_gb": round(mem.total / 1e9, 2),
            "used_gb": round(mem.used / 1e9, 2),
            "percent": mem.percent,
        },
        "disk": {
            "total_gb": round(disk.total / 1e9, 2),
            "used_gb": round(disk.used / 1e9, 2),
            "free_gb": round(disk.free / 1e9, 2),
            "percent": round(disk.used / disk.total * 100, 1),
        },
        "temperature_c": get_temperature_c(),
        "uptime_hours": round(uptime_seconds / 3600, 1),
        "agent_uptime_seconds": int(time.time() - START_TIME),
    }


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.rstrip("/") in ("", "/metrics"):
            try:
                data = collect_metrics()
                body = json.dumps(data, indent=2).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(str(e).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # keep it quiet


def main():
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"macmini-agent listening on :{PORT}/metrics")
    server.serve_forever()


if __name__ == "__main__":
    main()
