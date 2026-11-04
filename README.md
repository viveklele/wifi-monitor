# Local Wi-Fi and internet monitor

A small Python 3 program for Ubuntu. It checks whether the computer can reach its default router and the public internet, records timestamped checks in plain text logs, and serves a local dashboard. It uses only Python's standard library plus Ubuntu's `ip` and `ping` commands.

## Run

```bash
cd ~/projects/wifi-monitor
python3 monitor.py
```

Open <http://127.0.0.1:8765>. Stop it with `Ctrl+C`. The monitor checks every 2 seconds by default. You can change the interval or port:

```bash
python3 monitor.py --interval 10 --port 8765
```

The monitor writes readable daily log files under `logs/`, such as `logs/wifi-monitor-2026-11-04.log`. Each line records its timestamp in 24-hour India Standard Time (IST), Wi-Fi/router status, internet status, response time, and gateway. It keeps a rolling 24 hours of entries, removing older log lines about once an hour. The dashboard derives recent outages and sample-based internet uptime from those logs. A brief failure can be missed between checks; lower intervals catch shorter drops but create more log entries.

## Start automatically after login (optional)

Create a user systemd service:

```bash
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/wifi-monitor.service <<'EOF'
[Unit]
Description=Local Wi-Fi uptime monitor
After=network-online.target

[Service]
Type=simple
WorkingDirectory=%h/projects/wifi-monitor
ExecStart=/usr/bin/python3 %h/projects/wifi-monitor/monitor.py
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user enable --now wifi-monitor.service
```

Check the service with `systemctl --user status wifi-monitor.service`; stop it with `systemctl --user disable --now wifi-monitor.service`.

The server binds to `127.0.0.1` by default, so the dashboard is only available on this computer. Keep that default unless you intentionally want other devices on your network to access it.
