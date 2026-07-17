# Mac Mini Health Dashboard

A lightweight local dashboard for monitoring one or more Mac minis over a private
network such as Tailscale. Each Mac runs a small agent that reports CPU, memory,
disk, temperature, uptime, and basic platform details. One dashboard process
polls the agents and serves a mobile-friendly web page plus a combined JSON API.

No hosted service is required. Do not expose the agent or dashboard ports to the
public internet.

## What You Get

- `agent.py`: runs on each Mac mini and serves `GET /metrics` on port `8787`.
- `dashboard.py`: runs on one always-on machine and serves the dashboard on port
  `8080`.
- `config.example.json`: copy this to `config.json` and add your own machines.
- LaunchAgent plist examples for running both scripts in the background on macOS.

## Requirements

- macOS on each monitored Mac.
- Python 3.
- Tailscale or another private network path between the dashboard and agents.
- `psutil` on each agent machine.
- Optional temperature tool:
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

To install the dashboard as a background LaunchAgent:

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

Unload the agent:

```bash
launchctl unload ~/Library/LaunchAgents/com.example.macmini-agent.plist
```

Unload the dashboard:

```bash
launchctl unload ~/Library/LaunchAgents/com.example.macmini-dashboard.plist
```

View logs:

```bash
tail -f /tmp/macmini-agent.log /tmp/macmini-agent.err
tail -f /tmp/macmini-dashboard.log /tmp/macmini-dashboard.err
```

## Security Notes

- Keep the agent and dashboard on a private network.
- Do not port-forward ports `8787` or `8080` from your router.
- Do not commit `config.json`; it may contain private hostnames or IP addresses.
- The example config uses placeholder values only.
