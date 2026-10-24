#!/usr/bin/env python3
"""Linux Panic Button — local real-time monitoring dashboard."""

import platform
import ipaddress
import shutil
import socket
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import psutil
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

STARTED = time.monotonic()
PREVIOUS_NET = psutil.net_io_counters()
PREVIOUS_TIME = time.monotonic()
NETWORK_LOCK = threading.Lock()
SERVICE_CACHE_TTL = 15
SERVICE_CACHE_LOCK = threading.Lock()
SERVICE_CACHE = {"checked_at": 0.0, "value": None}
MAX_SCAN_HOSTS = 1024


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


def get_local_networks():
    """Return private IPv4 networks directly attached to active interfaces."""
    networks = []
    interface_stats = psutil.net_if_stats()
    for interface, addresses in psutil.net_if_addrs().items():
        stats = interface_stats.get(interface)
        if not stats or not stats.isup:
            continue
        for address in addresses:
            if address.family != socket.AF_INET or not address.netmask:
                continue
            try:
                network = ipaddress.ip_interface(
                    f"{address.address}/{address.netmask}"
                ).network
            except ValueError:
                continue
            if (not ipaddress.ip_address(address.address).is_private
                    or network.num_addresses < 2):
                continue
            networks.append({
                "interface": interface,
                "address": address.address,
                "cidr": str(network),
                "hosts": network.num_addresses - (2 if network.num_addresses > 2 else 0),
                "scannable": network.num_addresses <= MAX_SCAN_HOSTS,
            })
    return networks


def scan_network(network_info):
    """Ping usable addresses on an attached local subnet, with a strict cap."""
    network = ipaddress.ip_network(network_info["cidr"], strict=True)
    own_address = network_info["address"]
    targets = [str(address) for address in network.hosts()
               if str(address) != own_address]
    if len(targets) > MAX_SCAN_HOSTS:
        raise ValueError(f"Subnet is too large; scans are limited to {MAX_SCAN_HOSTS} hosts")
    if not shutil.which("ping"):
        raise RuntimeError("The ping command is unavailable; install iputils-ping")

    def probe(address):
        try:
            result = subprocess.run(
                ["ping", "-n", "-c", "1", "-W", "1", "-I",
                 network_info["interface"], address],
                capture_output=True, text=True, timeout=2, check=False,
            )
            return address if result.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired):
            return None

    with ThreadPoolExecutor(max_workers=48) as pool:
        responding = [address for address in pool.map(probe, targets) if address]

    neighbors = {}
    if responding and shutil.which("ip"):
        try:
            result = subprocess.run(
                ["ip", "neigh", "show", "dev", network_info["interface"]],
                capture_output=True, text=True, timeout=2, check=False,
            )
            for line in result.stdout.splitlines():
                fields = line.split()
                if "lladdr" in fields and fields:
                    neighbors[fields[0]] = fields[fields.index("lladdr") + 1]
        except (OSError, subprocess.TimeoutExpired):
            pass

    return [{"ip": address, "mac": neighbors.get(address, "Unknown")}
            for address in responding], len(targets)


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


@app.get("/api/networks")
def networks():
    return jsonify(get_local_networks())


@app.post("/api/scan")
def scan():
    payload = request.get_json(silent=True) or {}
    cidr = payload.get("cidr")
    interface = payload.get("interface")
    network_info = next((item for item in get_local_networks()
                         if item["cidr"] == cidr
                         and item["interface"] == interface), None)
    if not network_info:
        return jsonify({"error": "Choose a directly connected local network"}), 400
    if not network_info["scannable"]:
        return jsonify({"error": f"Subnet exceeds the {MAX_SCAN_HOSTS}-host scan limit"}), 400
    try:
        devices, scanned = scan_network(network_info)
    except (RuntimeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"network": network_info, "devices": devices,
                    "scanned": scanned})


if __name__ == "__main__":
    # Local machine only: do not expose this development server to a network.
    psutil.cpu_percent(interval=None)
    app.run(host="127.0.0.1", port=5000, debug=False)
