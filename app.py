#!/usr/bin/env python3
"""Linux Panic Button — local real-time monitoring dashboard."""

import platform
import shutil
import socket
import subprocess
import threading
import time
from datetime import datetime, timezone

import psutil
from flask import Flask, jsonify, render_template

app = Flask(__name__)

STARTED = time.monotonic()
PREVIOUS_NET = psutil.net_io_counters()
PREVIOUS_TIME = time.monotonic()
NETWORK_LOCK = threading.Lock()
SERVICE_CACHE_TTL = 15
SERVICE_CACHE_LOCK = threading.Lock()
SERVICE_CACHE = {"checked_at": 0.0, "value": None}


def get_services():
    now = time.monotonic()
    with SERVICE_CACHE_LOCK:
        if SERVICE_CACHE["value"] is not None and now - SERVICE_CACHE["checked_at"] < SERVICE_CACHE_TTL:
            return SERVICE_CACHE["value"]

        if not shutil.which("systemctl"):
            value = {"status": "UNKNOWN", "failed": [], "message": "systemctl unavailable"}
        else:
            try:
                result = subprocess.run(
                    ["systemctl", "--failed", "--no-legend", "--plain"],
                    capture_output=True,
                    text=True,
                    timeout=3,
                    check=False,
                )

                if result.returncode not in (0, 1):
                    value = {"status": "UNKNOWN", "failed": [], "message": "Could not inspect services"}
                else:
                    failed = [line.split()[0] for line in result.stdout.splitlines() if line.split()]
                    value = {
                        "status": "WARN" if failed else "OK",
                        "failed": failed,
                        "message": f"{len(failed)} failed unit(s)" if failed else "No failed units",
                    }
            except (OSError, subprocess.TimeoutExpired):
                value = {"status": "UNKNOWN", "failed": [], "message": "Service check timed out"}

        SERVICE_CACHE["checked_at"] = time.monotonic()
        SERVICE_CACHE["value"] = value
        return value


def get_network():
    global PREVIOUS_NET, PREVIOUS_TIME

    with NETWORK_LOCK:
        current = psutil.net_io_counters()
        now = time.monotonic()
        elapsed = max(now - PREVIOUS_TIME, 0.001)

        download = max(0, current.bytes_recv - PREVIOUS_NET.bytes_recv) / elapsed
        upload = max(0, current.bytes_sent - PREVIOUS_NET.bytes_sent) / elapsed

        PREVIOUS_NET = current
        PREVIOUS_TIME = now

    return {
        "download_bps": round(download),
        "upload_bps": round(upload),
        "total_received": current.bytes_recv,
        "total_sent": current.bytes_sent,
    }


def get_metrics():
    cpu = psutil.cpu_percent(interval=None)
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    uptime = int(time.time() - psutil.boot_time())

    net = get_network()
    services = get_services()

    checks = [services["status"]]
    if cpu >= 90 or memory.percent >= 90 or disk.percent >= 95:
        checks.append("WARN")

    overall = (
        "WARN" if "WARN" in checks
        else "UNKNOWN" if "UNKNOWN" in checks
        else "OK"
    )

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "hostname": socket.gethostname(),
        "platform": platform.platform(),
        "uptime_seconds": uptime,
        "app_uptime_seconds": round(time.monotonic() - STARTED),
        "cpu": {
            "percent": cpu,
            "logical_cores": psutil.cpu_count(),
        },
        "memory": {
            "percent": memory.percent,
            "used": memory.used,
            "total": memory.total,
            "available": memory.available,
        },
        "disk": {
            "percent": disk.percent,
            "used": disk.used,
            "total": disk.total,
            "free": disk.free,
        },
        "network": net,
        "services": services,
        "status": overall,
    }


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/metrics")
def metrics():
    response = jsonify(get_metrics())
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/api/health")
def health():
    return jsonify({"status": "online"})


if __name__ == "__main__":
    # Local machine only: do not expose this development server to a network.
    psutil.cpu_percent(interval=None)
    app.run(host="127.0.0.1", port=5000, debug=False)
