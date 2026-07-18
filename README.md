# Mac Mini Health Dashboard

A lightweight local dashboard for monitoring one or more Mac minis over a private
network such as Tailscale. Each Mac runs a small agent that reports CPU, memory,
disk, temperature, uptime, and basic platform details. One dashboard process
polls the agents and serves a mobile-friendly web page plus a combined JSON API,
with color-coded stats and short trend sparklines built from locally stored
history.

No hosted service is required. Do not expose the agent or dashboard ports to the
public internet.

## What You Get

- `agent.py`: runs on each Mac mini and serves `GET /metrics` on port `8787`.
- `dashboard.py`: runs on one always-on machine, serves the dashboard on port
  `8080`, and polls agents in the background to build a short rolling history
  (12 hours to 7 days, configurable) used for the trend sparklines.
- `config.example.json`: copy this to `config.json` and add your own machines.
- `com.example.macmini-agent.plist`: LaunchAgent example for running the agent
  in the background on each Mac mini.
- `macmini-dashboard.service`: systemd unit example for running the dashboard
  in the background on a Linux host. `com.example.macmini-dashboard.plist` is
  also included if you'd rather run the dashboard on a Mac instead.

`dashboard.py` only uses the Python standard library, so the dashboard host
itself doesn't need to be a Mac — it can be a Linux box, a Raspberry Pi, or
one of the Mac minis. The agent (`agent.py`) does need macOS + `psutil`, since
it reads Mac-specific stats.

## Requirements

- macOS on each monitored Mac mini (for the agent).
- Any machine with Python 3 for the dashboard host (Linux, macOS, Raspberry Pi, etc.).
- Tailscale or another private network path between the dashboard and agents.
- `psutil` on each agent machine.
- Optional temperature tool on each agent machine:
  - Apple Silicon: `smctemp`
  - Intel Mac: `osx-cpu-temp`

## 1. Clone the Repository

```bash
git clone https://github.com/itsbkm/macmini-health-dashboard.git
cd macmini-health-dashboard
```

## 2. Install the Agent on Each Mac Mini

Install Python dependencies:

```bash
python3 -m pip install psutil
```

Optional CPU temperature support:

```bash
brew install narugit/tap/smctemp
```

For Intel Macs, use this instead:

```bash
brew install osx-cpu-temp
```

Run the agent manually for a quick test:

```bash
python3 agent.py
```

In another terminal, verify that it responds:

```bash
curl http://localhost:8787/metrics
```

To install it as a background LaunchAgent:

```bash
sudo cp agent.py /opt/homebrew/bin/macmini-agent.py
mkdir -p ~/Library/LaunchAgents
cp com.example.macmini-agent.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.example.macmini-agent.plist
```

If your `python3` path is not `/usr/bin/python3`, edit the plist before loading
it. Check your Python path with:

```bash
which python3
```

## 3. Configure the Dashboard

On the machine that will host the dashboard, create your local config:

```bash
cp config.example.json config.json
```

Edit `config.json` and replace the sample machines with your own Tailscale
MagicDNS names or private IPs:

```json
{
  "dashboard_port": 8080,
  "refresh_seconds": 30,
  "history": {
    "enabled": true,
    "poll_seconds": 300,
    "retention_days": 1
  },
  "minis": [
    {
      "name": "Office Mini",
      "host": "office-mini",
      "port": 8787
    },
    {
      "name": "Studio Mini",
      "host": "studio-mini",
      "port": 8787
    }
  ]
}
```

`config.json` is ignored by git so your machine names and private IP addresses
do not get committed.

### History settings

- `enabled`: turn trend history on/off. When off, cards show live stats only,
  no data is polled or stored, and `/history.json` returns an empty object.
- `poll_seconds`: how often the dashboard polls every mini in the background
  to record a history sample. This is independent of `refresh_seconds` (which
  only controls how often your browser reloads the page). Clamped to a
  30-second minimum.
- `retention_days`: how far back to keep samples, from `0.5` (12 hours) up to
  `7` (one week). Older samples are dropped on every write, so the file size
  is bounded by this window, not by how long the dashboard has been running.
  At the default 5-minute poll interval, even 7 days of history for a couple
  of minis stays well under 1 MB.

## 4. Run the Dashboard

Run it manually:

```bash
python3 dashboard.py
```

Open the dashboard from another device on the same private network:

```text
http://<dashboard-host>:8080
```

The combined JSON endpoint is available at:

```text
http://<dashboard-host>:8080/metrics.json
```

Historical samples (used to draw the trend sparklines on each card) are
available at:

```text
http://<dashboard-host>:8080/history.json
```

Samples are stored in `history.json` next to `dashboard.py` (override the
location with the `MACMINI_DASHBOARD_HISTORY` environment variable). This
file is ignored by git, same as `config.json` — it only ever contains data
polled from your own machines, but there's no reason for it to be public
either. It's a plain JSON file that a background thread in `dashboard.py`
rewrites on every poll; nothing needs to be created manually.

### Sparklines and color coding

Each card shows a small trend line under CPU, memory, and disk usage, plus a
short one next to the temperature reading, built from the samples in
`history.json`. The CPU/memory/disk icons, bars, and sparklines turn orange
at 70% and red at 90%. The temperature icon and its sparkline turn orange at
65°C and red at 80°C, tuned for typical Apple Silicon Mac mini idle/load
ranges — adjust `TEMP_WARN_C` / `TEMP_DANGER_C` near the top of
`dashboard.py` if your hardware runs hotter or cooler.

### Running the dashboard in the background

**On Linux (systemd)** — clone the repo to its final location first (e.g.
`/var/www/macmini-health-dashboard`, or wherever `config.json` already lives),
then:

```bash
sudo cp macmini-dashboard.service /etc/systemd/system/macmini-dashboard.service
sudo systemctl daemon-reload
sudo systemctl enable --now macmini-dashboard.service
```

The unit file assumes the repo lives at `/var/www/macmini-health-dashboard`
and `python3` is at `/usr/bin/python3` — edit `WorkingDirectory` and
`ExecStart` in `macmini-dashboard.service` first if either differs on your
system. Check it's up with:

```bash
systemctl status macmini-dashboard.service
```

**On macOS**, install it as a background LaunchAgent instead:

```bash
sudo cp dashboard.py /opt/homebrew/bin/macmini-dashboard.py
sudo cp config.json /opt/homebrew/bin/config.json
mkdir -p ~/Library/LaunchAgents
cp com.example.macmini-dashboard.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.example.macmini-dashboard.plist
```

## 5. Use It From a Phone

With Tailscale connected on your phone, open:

```text
http://<dashboard-host>:8080
```

On Android or iOS, you can add the page to your home screen from the browser
menu. For a real widget, point any JSON widget app at:

```text
http://<dashboard-host>:8080/metrics.json
```

## Managing Services

Unload the agent (on each Mac mini):

```bash
launchctl unload ~/Library/LaunchAgents/com.example.macmini-agent.plist
```

View agent logs:

```bash
tail -f /tmp/macmini-agent.log /tmp/macmini-agent.err
```

**Dashboard on Linux (systemd):**

```bash
sudo systemctl stop macmini-dashboard.service      # stop it
sudo systemctl disable macmini-dashboard.service   # stop it from starting on boot
sudo rm /etc/systemd/system/macmini-dashboard.service
sudo systemctl daemon-reload
journalctl -u macmini-dashboard.service -f         # view logs
```

**Dashboard on macOS (LaunchAgent):**

```bash
launchctl unload ~/Library/LaunchAgents/com.example.macmini-dashboard.plist
tail -f /tmp/macmini-dashboard.log /tmp/macmini-dashboard.err
```

## Security Notes

- Keep the agent and dashboard on a private network.
- Do not port-forward ports `8787` or `8080` from your router.
- Do not commit `config.json` or `history.json`; the former may contain
  private hostnames or IP addresses, the latter contains historical stats
  from your machines. Both are gitignored by default.
- The example config uses placeholder values only.
