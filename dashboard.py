#!/usr/bin/env python3
"""
dashboard.py

Aggregates /metrics from each of your Mac minis (running agent.py) and
serves one mobile-friendly, auto-refreshing HTML page plus a combined
JSON endpoint.

Run this on ONE always-on machine reachable over Tailscale (e.g. one of
the Mac minis, or a Raspberry Pi / always-on Linux box). It fetches from
the other minis using their Tailscale IPs or MagicDNS names.

Requires: nothing outside the standard library.

Create a config file from config.example.json and list your Mac minis'
Tailscale hostnames or IPs there. By default this script looks for
./config.json next to dashboard.py, or you can set MACMINI_DASHBOARD_CONFIG.
"""

import json
import os
import tempfile
import threading
import time
from pathlib import Path
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8080
REFRESH_SECONDS = 30
DEFAULT_CONFIG_PATH = Path(__file__).with_name("config.json")
HISTORY_PATH = Path(os.environ.get("MACMINI_DASHBOARD_HISTORY", Path(__file__).with_name("history.json")))

# How many samples a metric needs before we bother drawing a sparkline.
MIN_SPARKLINE_SAMPLES = 2

# History retention is expressed in days but clamped to a sane range so the
# history.json file can't be configured into growing unbounded.
MIN_RETENTION_DAYS = 0.5
MAX_RETENTION_DAYS = 7
MIN_POLL_SECONDS = 30


def load_config():
    config_path = Path(os.environ.get("MACMINI_DASHBOARD_CONFIG", DEFAULT_CONFIG_PATH))
    if not config_path.exists():
        raise SystemExit(
            f"Missing config file: {config_path}\n"
            "Copy config.example.json to config.json and add your Mac mini hosts."
        )

    with config_path.open("r", encoding="utf-8") as f:
        config = json.load(f)

    minis = config.get("minis", [])
    if not minis:
        raise SystemExit("Config must include at least one entry in the 'minis' list.")

    for mini in minis:
        if not mini.get("name") or not mini.get("host"):
            raise SystemExit("Each mini entry must include 'name' and 'host'.")
        mini.setdefault("port", 8787)

    history_cfg = config.get("history", {})
    retention_days = float(history_cfg.get("retention_days", 1))
    retention_days = max(MIN_RETENTION_DAYS, min(MAX_RETENTION_DAYS, retention_days))
    poll_seconds = int(history_cfg.get("poll_seconds", 300))
    poll_seconds = max(MIN_POLL_SECONDS, poll_seconds)

    return {
        "port": int(config.get("dashboard_port", PORT)),
        "refresh_seconds": int(config.get("refresh_seconds", REFRESH_SECONDS)),
        "minis": minis,
        "history_enabled": bool(history_cfg.get("enabled", True)),
        "history_poll_seconds": poll_seconds,
        "history_retention_days": retention_days,
    }


CONFIG = load_config()
PORT = CONFIG["port"]
REFRESH_SECONDS = CONFIG["refresh_seconds"]
MINIS = CONFIG["minis"]
HISTORY_ENABLED = CONFIG["history_enabled"]
HISTORY_POLL_SECONDS = CONFIG["history_poll_seconds"]
HISTORY_RETENTION_SECONDS = CONFIG["history_retention_days"] * 86400


# --- History storage -------------------------------------------------------
#
# Samples are stored per-host as compact [timestamp, cpu, mem, disk, temp]
# arrays (rather than one dict per sample) to keep history.json small. The
# file is trimmed to HISTORY_RETENTION_SECONDS on every write, so its size
# is bounded by (minis * retention_window / poll_interval), not by uptime.
_history_lock = threading.Lock()


def _load_history():
    if not HISTORY_PATH.exists():
        return {}
    try:
        with HISTORY_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


_HISTORY = _load_history()


def _save_history_atomic(data):
    fd, tmp_path = tempfile.mkstemp(dir=str(HISTORY_PATH.parent), prefix=".history-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, separators=(",", ":"))
        os.replace(tmp_path, HISTORY_PATH)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def record_history_sample(results):
    now = time.time()
    cutoff = now - HISTORY_RETENTION_SECONDS
    with _history_lock:
        for m in results:
            if m["_status"] != "online":
                continue
            host_key = m["_host_key"]
            entry = _HISTORY.setdefault(host_key, {"name": m["_label"], "samples": []})
            entry["name"] = m["_label"]
            entry["samples"].append([
                int(now),
                m["cpu"]["percent"],
                m["memory"]["percent"],
                m["disk"]["percent"],
                m.get("temperature_c"),
            ])
            entry["samples"] = [s for s in entry["samples"] if s[0] >= cutoff]

        # Drop hosts no longer present in config.
        known_keys = {mini["host"] for mini in MINIS}
        for stale_key in list(_HISTORY.keys()):
            if stale_key not in known_keys:
                del _HISTORY[stale_key]

        snapshot = json.loads(json.dumps(_HISTORY))

    _save_history_atomic(snapshot)


def get_history_snapshot():
    with _history_lock:
        return json.loads(json.dumps(_HISTORY))


def get_metric_series(host_key, metric_index):
    """metric_index: 1=cpu, 2=mem, 3=disk, 4=temp (index into the sample array)."""
    with _history_lock:
        samples = _HISTORY.get(host_key, {}).get("samples", [])
        return [s[metric_index] for s in samples if s[metric_index] is not None]


def history_poll_loop():
    while True:
        try:
            results = [fetch_metrics(m) for m in MINIS]
            record_history_sample(results)
        except Exception:
            pass
        time.sleep(HISTORY_POLL_SECONDS)


def fetch_metrics(mini):
    url = f"http://{mini['host']}:{mini['port']}/metrics"
    try:
        with urllib.request.urlopen(url, timeout=4) as resp:
            data = json.loads(resp.read())
            data["_status"] = "online"
            data["_label"] = mini["name"]
            data["_host_key"] = mini["host"]
            return data
    except Exception as e:
        return {"_status": "offline", "_label": mini["name"], "_host_key": mini["host"], "_error": str(e)}


COLOR_OK = "#4caf50"
COLOR_WARN = "#fb8c00"
COLOR_DANGER = "#e53935"

# Apple Silicon Mac minis idle in the 35-50C range; sustained heavy load can
# reach the 70s-80s before thermal throttling kicks in.
TEMP_WARN_C = 65
TEMP_DANGER_C = 80


def threshold_color(value, warn, danger):
    if value is None:
        return "#888"
    if value >= danger:
        return COLOR_DANGER
    if value >= warn:
        return COLOR_WARN
    return COLOR_OK


def temp_color(temp_c):
    return threshold_color(temp_c, TEMP_WARN_C, TEMP_DANGER_C)


def bar(percent, warn=70, danger=90):
    percent = percent or 0
    color = threshold_color(percent, warn, danger)
    return f'''<div class="bar-track">
        <div class="bar-fill" style="width:{min(percent,100)}%;background:{color}"></div>
    </div>'''


def sparkline(values, vmin, vmax, color, width=100, height=20):
    """Inline SVG trend line for a series of floats. Returns '' if not enough data."""
    if len(values) < MIN_SPARKLINE_SAMPLES:
        return ""
    span = max(vmax - vmin, 0.001)
    step = width / (len(values) - 1)
    points = []
    for i, v in enumerate(values):
        clamped = max(vmin, min(vmax, v))
        x = i * step
        y = height - ((clamped - vmin) / span) * height
        points.append(f"{x:.1f},{y:.1f}")
    path = " ".join(points)
    return (
        f'<svg class="spark" viewBox="0 0 {width} {height}" preserveAspectRatio="none">'
        f'<polyline points="{path}" fill="none" stroke="{color}" stroke-width="1.5" '
        f'stroke-linejoin="round" stroke-linecap="round"/></svg>'
    )


def render_card(m):
    if m["_status"] == "offline":
        return f'''
        <div class="card offline">
            <div class="card-header">
                <span class="dot dot-off"></span>
                <span class="name">{m["_label"]}</span>
            </div>
            <div class="offline-text">Offline / unreachable</div>
        </div>'''

    cpu = m["cpu"]["percent"]
    mem = m["memory"]["percent"]
    disk = m["disk"]["percent"]
    temp = m.get("temperature_c")
    temp_str = f'{temp}°C' if temp is not None else "N/A"

    host_key = m["_host_key"]
    cpu_series = get_metric_series(host_key, 1) if HISTORY_ENABLED else []
    mem_series = get_metric_series(host_key, 2) if HISTORY_ENABLED else []
    disk_series = get_metric_series(host_key, 3) if HISTORY_ENABLED else []
    temp_series = get_metric_series(host_key, 4) if HISTORY_ENABLED else []

    cpu_spark = sparkline(cpu_series, 0, 100, threshold_color(cpu, 70, 90))
    mem_spark = sparkline(mem_series, 0, 100, threshold_color(mem, 70, 90))
    disk_spark = sparkline(disk_series, 0, 100, threshold_color(disk, 70, 90))
    temp_spark = sparkline(temp_series, 20, 90, temp_color(temp), width=60, height=16)

    cpu_icon_color = threshold_color(cpu, 70, 90)
    mem_icon_color = threshold_color(mem, 70, 90)
    disk_icon_color = threshold_color(disk, 70, 90)
    t_color = temp_color(temp)

    return f'''
    <div class="card">
        <div class="card-header">
            <span class="dot dot-on"></span>
            <span class="name">{m["_label"]}</span>
            <span class="arch">{m.get("arch","")}</span>
        </div>

        <div class="row">
            <span class="label"><span style="color:{cpu_icon_color}">&#9881;&#65039;</span> CPU {cpu:.0f}%</span>
            {bar(cpu)}
            {cpu_spark}
        </div>
        <div class="row">
            <span class="label"><span style="color:{mem_icon_color}">&#128190;</span> Mem {mem:.0f}% ({m["memory"]["used_gb"]}/{m["memory"]["total_gb"]} GB)</span>
            {bar(mem)}
            {mem_spark}
        </div>
        <div class="row">
            <span class="label"><span style="color:{disk_icon_color}">&#128451;&#65039;</span> Disk {disk:.0f}% ({m["disk"]["used_gb"]}/{m["disk"]["total_gb"]} GB)</span>
            {bar(disk)}
            {disk_spark}
        </div>
        <div class="stats-line">
            <span><span style="color:{t_color}">&#127777;&#65039;</span> {temp_str} {temp_spark}</span>
            <span>&#9201;&#65039; up {m["uptime_hours"]}h</span>
            <span>{m.get("tailscale_ip") or ""}</span>
        </div>
    </div>'''


PAGE_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="{refresh}">
<title>Mac Minis</title>
<style>
  body {{
    margin:0; padding:16px;
    background:#0f1115; color:#eaeaea;
    font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
  }}
  h1 {{ font-size:18px; margin:0 0 12px; color:#fff; }}
  .updated {{ font-size:12px; color:#888; margin-bottom:16px; }}
  .card {{
    background:#1b1e26; border-radius:12px; padding:14px 16px;
    margin-bottom:12px; box-shadow:0 1px 3px rgba(0,0,0,.4);
  }}
  .card.offline {{ opacity:.55; }}
  .card-header {{ display:flex; align-items:center; gap:8px; margin-bottom:10px; }}
  .name {{ font-weight:600; font-size:15px; }}
  .arch {{ font-size:11px; color:#888; margin-left:auto; }}
  .dot {{ width:9px; height:9px; border-radius:50%; }}
  .dot-on {{ background:#4caf50; }}
  .dot-off {{ background:#e53935; }}
  .offline-text {{ font-size:13px; color:#e57373; }}
  .row {{ margin-bottom:8px; }}
  .label {{ font-size:12px; color:#c8c8c8; display:block; margin-bottom:3px; }}
  .bar-track {{ background:#2c2f38; border-radius:6px; height:7px; overflow:hidden; }}
  .bar-fill {{ height:100%; border-radius:6px; }}
  .spark {{ display:block; width:100%; height:16px; margin-top:4px; }}
  .stats-line {{
    display:flex; justify-content:space-between; align-items:center; font-size:11px;
    color:#999; margin-top:10px;
  }}
  .stats-line span {{ display:inline-flex; align-items:center; gap:4px; }}
  .stats-line .spark {{ width:60px; height:14px; margin-top:0; }}
</style>
</head>
<body>
  <h1>Mac Minis — Tailscale Dashboard</h1>
  <div class="updated">Auto-refreshes every {refresh}s</div>
  {cards}
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.rstrip("/") == "/history.json":
            body = json.dumps(get_history_snapshot()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path.rstrip("/") == "/metrics.json":
            results = [fetch_metrics(m) for m in MINIS]
            body = json.dumps(results, indent=2).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # default: HTML dashboard
        results = [fetch_metrics(m) for m in MINIS]
        cards_html = "\n".join(render_card(m) for m in results)
        page = PAGE_TEMPLATE.format(refresh=REFRESH_SECONDS, cards=cards_html)
        body = page.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


def main():
    if HISTORY_ENABLED:
        threading.Thread(target=history_poll_loop, daemon=True).start()
        print(
            f"history polling every {HISTORY_POLL_SECONDS}s, "
            f"retaining {CONFIG['history_retention_days']}d -> {HISTORY_PATH}"
        )

    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"dashboard listening on :{PORT}  (open http://<this-machine-tailscale-ip>:{PORT})")
    server.serve_forever()


if __name__ == "__main__":
    main()
