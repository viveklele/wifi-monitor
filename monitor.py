#!/usr/bin/env python3
"""Local Wi-Fi and internet monitor with a small browser dashboard."""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import threading
import time
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
LOG_DIR = Path(os.environ.get("WIFI_MONITOR_LOG_DIR", ROOT / "logs"))
PAGE = ROOT / "dashboard.html"
# India has no daylight-saving changes, so use the fixed IST offset. This also
# avoids requiring an OS timezone database on minimal environments like Termux.
IST = timezone(timedelta(hours=5, minutes=30), name="IST")
LAST_PRUNE = 0.0
IS_TERMUX = "TERMUX_VERSION" in os.environ or os.environ.get("PREFIX", "").endswith("/com.termux/files/usr")


def now() -> float:
    return time.time()


def stamp(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, IST).isoformat(timespec="seconds") if ts else None


def log_path(day: date) -> Path:
    return LOG_DIR / f"wifi-monitor-{day.isoformat()}.log"


def parse_log_timestamp(value: str) -> float:
    parsed = None
    for fmt in ("%Y-%m-%d %H:%M:%S IST", "%Y-%m-%d %I:%M:%S %p IST"):
        try:
            parsed = datetime.strptime(value, fmt).replace(tzinfo=IST)
            break
        except ValueError:
            continue
    if parsed is None:
        parsed = datetime.fromisoformat(value)
    return parsed.timestamp()


def prune_logs(current: float) -> None:
    """Keep only entries from the most recent 24 hours in the daily text logs."""
    cutoff = current - 24 * 3600
    cutoff_day = datetime.fromtimestamp(cutoff, IST).date()
    if not LOG_DIR.exists():
        return
    for path in LOG_DIR.glob("wifi-monitor-*.log"):
        try:
            file_day = date.fromisoformat(path.stem.removeprefix("wifi-monitor-"))
        except ValueError:
            continue
        if file_day < cutoff_day:
            path.unlink(missing_ok=True)
        elif file_day == cutoff_day:
            try:
                with path.open(encoding="utf-8") as log:
                    lines = log.readlines()
                kept = []
                for line in lines:
                    try:
                        ts = parse_log_timestamp(line.split(" | ", 1)[0])
                        if ts >= cutoff:
                            kept.append(line)
                    except ValueError:
                        kept.append(line)
                with path.open("w", encoding="utf-8") as log:
                    log.writelines(kept)
            except OSError as exc:
                print(f"Could not prune log {path}: {exc}", flush=True)


def default_gateway() -> str | None:
    try:
        result = subprocess.run(["ip", "-4", "route", "show", "default"], capture_output=True, text=True, timeout=2, check=False)
        words = result.stdout.split()
        if "via" in words:
            return words[words.index("via") + 1]
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def ping(host: str) -> bool:
    try:
        return subprocess.run(["ping", "-n", "-c", "1", "-W", "1", host], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2, check=False).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def termux_wifi_connected() -> bool | None:
    """Return Wi-Fi association state via optional Termux:API, or None if unavailable."""
    if not IS_TERMUX:
        return None
    try:
        result = subprocess.run(
            ["termux-wifi-connectioninfo"],
            capture_output=True,
            text=True,
            timeout=1.5,
            check=False,
        )
        if result.returncode != 0:
            return None
        info = json.loads(result.stdout)
        state = info.get("supplicant_state")
        if isinstance(state, str):
            return state.upper() == "COMPLETED"
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        pass
    return None


def check_dns() -> bool:
    """Check whether the system DNS resolver can resolve a public hostname."""
    try:
        socket.getaddrinfo("example.com", 443, type=socket.SOCK_STREAM)
        return True
    except OSError:
        return False


def check_connectivity() -> tuple[bool | None, bool, bool, float | None, str | None]:
    gateway = default_gateway()
    wifi_state = termux_wifi_connected()
    # Termux:API reports actual Wi-Fi association and also works on Android
    # versions where Android restricts access to the route table or ICMP.
    if wifi_state is not None:
        wifi = wifi_state
    elif IS_TERMUX and not gateway:
        # Some Android builds restrict route-table access, and the Play Store
        # Termux build does not provide the Wi-Fi API add-on.
        wifi = None
    else:
        wifi = ping(gateway) if gateway else False
    dns = check_dns()
    start = time.monotonic()
    internet = False
    try:
        with socket.create_connection(("1.1.1.1", 443), timeout=2):
            internet = True
    except OSError:
        pass
    latency = round((time.monotonic() - start) * 1000, 1) if internet else None
    return wifi, internet, dns, latency, gateway


def save_check(wifi: bool | None, internet: bool, dns: bool, latency: float | None, gateway: str | None) -> None:
    global LAST_PRUNE
    checked = now()
    if checked - LAST_PRUNE >= 3600:
        prune_logs(checked)
        LAST_PRUNE = checked
    timestamp = datetime.fromtimestamp(checked, IST).strftime("%Y-%m-%d %H:%M:%S IST")
    latency_text = f"{latency:.1f} ms" if latency is not None else "none"
    gateway_text = gateway or "none"
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    wifi_text = "UNKNOWN" if wifi is None else ("UP" if wifi else "DOWN")
    with log_path(datetime.fromtimestamp(checked, IST).date()).open("a", encoding="utf-8") as log:
        log.write(f"{timestamp} | wifi={wifi_text} | dns={'UP' if dns else 'DOWN'} | internet={'UP' if internet else 'DOWN'} | latency={latency_text} | gateway={gateway_text}\n")


def monitor_loop(interval: int) -> None:
    while True:
        try:
            save_check(*check_connectivity())
        except Exception as exc:
            print(f"Monitor check failed: {exc}", flush=True)
        time.sleep(interval)


def dashboard_data() -> dict:
    current = now()
    since = current - 24 * 3600
    today = datetime.fromtimestamp(current, IST).date()
    samples = []
    for day in (today - timedelta(days=1), today):
        path = log_path(day)
        if not path.exists():
            continue
        try:
            with path.open(encoding="utf-8") as log:
                for line in log:
                    try:
                        timestamp_text, values_text = line.rstrip("\n").split(" | ", 1)
                        checked = parse_log_timestamp(timestamp_text)
                        if checked < since:
                            break
                        fields = values_text.split(" | ") if " | " in values_text else values_text.split()
                        values = dict(item.strip().split("=", 1) for item in fields if "=" in item)
                        latency = values.get("latency", values.get("latency_ms", "none"))
                        latency = latency.removesuffix("ms").removesuffix(" ms")
                        dns_value = values.get("dns")
                        samples.append({
                            "checked_at": checked,
                            "wifi": True if values.get("wifi") == "UP" else (False if values.get("wifi") == "DOWN" else None),
                            "dns": True if dns_value == "UP" else (False if dns_value == "DOWN" else None),
                            "internet": values.get("internet") == "UP",
                            "latency_ms": float(latency) if latency not in ("none", "") else None,
                            "gateway": values.get("gateway") or None,
                        })
                    except (ValueError, TypeError):
                        continue
        except OSError:
            continue
    samples.sort(key=lambda sample: sample["checked_at"])
    latest = samples[-1] if samples else None
    total = len(samples)
    up_count = sum(sample["internet"] for sample in samples)
    uptime = round(100 * up_count / total, 2) if total else None
    outages = []
    active = None
    for sample in samples:
        kind = "wifi" if sample["wifi"] is False else ("internet" if not sample["internet"] else None)
        if kind and active is None:
            active = {"id": len(outages) + 1, "kind": kind, "started_at": sample["checked_at"], "ended_at": None}
        elif kind and active and active["kind"] != kind:
            active["ended_at"] = sample["checked_at"]
            outages.append(active)
            active = {"id": len(outages) + 1, "kind": kind, "started_at": sample["checked_at"], "ended_at": None}
        elif not kind and active:
            active["ended_at"] = sample["checked_at"]
            outages.append(active)
            active = None
    if active:
        outages.append(active)
    return {
        "current": {"wifi": latest["wifi"], "dns": latest["dns"], "internet": latest["internet"], "latency_ms": latest["latency_ms"], "gateway": latest["gateway"], "checked_at": stamp(latest["checked_at"])} if latest else None,
        "uptime_24h": uptime,
        "checks_24h": total,
        "outages": [{"id": o["id"], "kind": o["kind"], "started_at": stamp(o["started_at"]), "ended_at": stamp(o["ended_at"]), "duration_seconds": max(0, int((o["ended_at"] or current) - o["started_at"]))} for o in outages[-100:][::-1]],
        "samples": [{"time": stamp(s["checked_at"]), "wifi": s["wifi"], "dns": s["dns"], "internet": s["internet"]} for s in samples],
        "interval_seconds": INTERVAL,
    }


class Handler(BaseHTTPRequestHandler):
    def log_date_time_string(self) -> str:
        return datetime.now(IST).strftime("%d/%b/%Y:%H:%M:%S +0530")

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/status":
            body = json.dumps(dashboard_data()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
        elif path == "/":
            body = PAGE.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
        else:
            body = b"Not found"
            self.send_response(404)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        message = fmt % args
        # Keep successful HTTP 200 requests quiet; surface every other status.
        status = re.search(r'" (\d{3}) ', message)
        if not status or status.group(1) != "200":
            print(f"{self.log_date_time_string()} {message}", flush=True)


def main() -> None:
    global INTERVAL
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interval", type=int, default=2, help="seconds between checks (default: 2)")
    parser.add_argument("--host", default="127.0.0.1", help="dashboard bind address; keep localhost for private use")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if args.interval < 2:
        parser.error("--interval must be at least 2 seconds")
    INTERVAL = args.interval
    threading.Thread(target=monitor_loop, args=(args.interval,), daemon=True).start()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Monitoring every {args.interval}s. Open http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping monitor.")
    finally:
        server.server_close()


INTERVAL = 2
if __name__ == "__main__":
    main()
