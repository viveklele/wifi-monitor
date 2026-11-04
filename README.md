# Local Wi-Fi and internet monitor

A small Python 3 program for Ubuntu or Android through Termux. It records timestamped checks in plain text logs and serves a local dashboard. On Ubuntu it checks the default router and public internet. On Termux it can use Termux:API to check whether the phone is associated with Wi-Fi, then checks public internet reachability.

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

## Android with Termux

1. Install **Termux** and **Termux:API** from the same source (for example, both from F-Droid). Termux and its add-ons need matching signing sources.
2. In Termux, install the required packages:

   ```sh
   pkg update
   pkg install python git termux-api iproute2
   ```

3. Copy this project folder into Termux, or clone the repository after it has been pushed to GitHub. Then run:

   ```sh
   cd ~/wifi-monitor
   termux-wifi-connectioninfo
   python monitor.py
   ```

   The Wi-Fi info command should print JSON including `supplicant_state`. If Android asks for permission, grant it. The monitor uses that state to identify Wi-Fi association; it does not need root. `iproute2` is optional for the gateway field.
4. Open <http://127.0.0.1:8765> in the phone's browser.

To keep monitoring with the screen off, run `termux-wake-lock` in another Termux session and set Termux's Android battery use to **Unrestricted**. This can increase battery use, and Android or the phone maker may still stop background work. Stop the monitor with `Ctrl+C`, then release the wake lock with `termux-wake-unlock`.

The logs stay in the project's `logs/` folder inside Termux's private storage. The same 2-second interval and rolling 24-hour retention apply. Use `--interval 5` or higher if you want to reduce battery usage.
