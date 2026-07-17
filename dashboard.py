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
from pathlib import Path
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8080
REFRESH_SECONDS = 30
DEFAULT_CONFIG_PATH = Path(__file__).with_name("config.json")


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

    return {
        "port": int(config.get("dashboard_port", PORT)),
        "refresh_seconds": int(config.get("refresh_seconds", REFRESH_SECONDS)),
        "minis": minis,
    }


CONFIG = load_config()
PORT = CONFIG["port"]
REFRESH_SECONDS = CONFIG["refresh_seconds"]
MINIS = CONFIG["minis"]


def fetch_metrics(mini):
    url = f"http://{mini['host']}:{mini['port']}/metrics"
    try:
        with urllib.request.urlopen(url, timeout=4) as resp:
            data = json.loads(resp.read())
            data["_status"] = "online"
            data["_label"] = mini["name"]
            return data
    except Exception as e:
        return {"_status": "offline", "_label": mini["name"], "_error": str(e)}


def bar(percent, warn=70, danger=90):
    percent = percent or 0
    color = "#4caf50"
    if percent >= danger:
        color = "#e53935"
    elif percent >= warn:
        color = "#fb8c00"
    return f'''<div class="bar-track">
        <div class="bar-fill" style="width:{min(percent,100)}%;background:{color}"></div>
    </div>'''


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

    return f'''
    <div class="card">
        <div class="card-header">
            <span class="dot dot-on"></span>
            <span class="name">{m["_label"]}</span>
            <span class="arch">{m.get("arch","")}</span>
        </div>

        <div class="row">
            <span class="label">CPU {cpu:.0f}%</span>
            {bar(cpu)}
        </div>
        <div class="row">
            <span class="label">Mem {mem:.0f}% ({m["memory"]["used_gb"]}/{m["memory"]["total_gb"]} GB)</span>
            {bar(mem)}
        </div>
        <div class="row">
            <span class="label">Disk {disk:.0f}% ({m["disk"]["used_gb"]}/{m["disk"]["total_gb"]} GB)</span>
            {bar(disk)}
        </div>
        <div class="stats-line">
            <span>🌡 {temp_str}</span>
            <span>⏱ up {m["uptime_hours"]}h</span>
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
  .stats-line {{
    display:flex; justify-content:space-between; font-size:11px;
    color:#999; margin-top:10px;
  }}
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
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"dashboard listening on :{PORT}  (open http://<this-machine-tailscale-ip>:{PORT})")
    server.serve_forever()


if __name__ == "__main__":
    main()
