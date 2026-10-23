#!/usr/bin/env python3
"""Linux Panic Button — read-only Linux diagnostics CLI."""

import argparse
import json
import platform
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import psutil
except ImportError:
    print("Missing dependency: pip install psutil", file=sys.stderr)
    raise SystemExit(2)

DEFAULT_REPORT = Path("reports/latest.json")


def run_command(command, timeout=4):
    """Execute a read-only system command safely."""
    if not shutil.which(command[0]):
        return {"available": False, "returncode": None,
                "stdout": "", "stderr": f"{command[0]} not found"}

    try:
        result = subprocess.run(
            command, capture_output=True, text=True,
            timeout=timeout, check=False
        )
        return {
            "available": True,
            "returncode": result.returncode,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "available": True, "returncode": None,
            "stdout": "", "stderr": str(exc)
        }


def cpu_check():
    usage = psutil.cpu_percent(interval=0.5)
    return {
        "usage_percent": usage,
        "logical_processors": psutil.cpu_count(),
        "status": "WARN" if usage >= 90 else "OK",
        "message": f"{usage:.1f}% utilization"
    }


def memory_check():
    memory = psutil.virtual_memory()
    return {
        "usage_percent": memory.percent,
        "total_bytes": memory.total,
        "used_bytes": memory.used,
        "available_bytes": memory.available,
        "status": "WARN" if memory.percent >= 85 else "OK",
        "message": f"{memory.percent:.1f}% utilized"
    }


def disk_check():
    disk = psutil.disk_usage("/")
    return {
        "mountpoint": "/",
        "usage_percent": disk.percent,
        "total_bytes": disk.total,
        "used_bytes": disk.used,
        "free_bytes": disk.free,
        "status": "WARN" if disk.percent >= 90 else "OK",
        "message": f"{disk.percent:.1f}% used"
    }


def uptime_check():
    seconds = max(0, int(time.time() - psutil.boot_time()))
    days, remaining = divmod(seconds, 86400)
    hours, remaining = divmod(remaining, 3600)
    minutes = remaining // 60

    return {
        "seconds": seconds,
        "status": "OK",
        "message": f"{days}d {hours}h {minutes}m"
    }


def service_check():
    result = run_command(
        ["systemctl", "--failed", "--no-legend", "--plain"]
    )

    if not result["available"] or result["returncode"] not in (0, 1):
        return {
            "status": "UNKNOWN",
            "failed_units": [],
            "message": result["stderr"] or "Could not inspect services"
        }

    failed = [
        line.split()[0]
        for line in result["stdout"].splitlines()
        if line.split()
    ]

    return {
        "status": "WARN" if failed else "OK",
        "failed_units": failed,
        "failed_count": len(failed),
        "message": (
            f"{len(failed)} failed unit(s)"
            if failed else "No failed systemd units reported"
        )
    }


def network_check():
    interfaces = psutil.net_if_stats()
    active = sorted(
        name for name, stats in interfaces.items()
        if stats.isup and name != "lo"
    )

    route = run_command(["ip", "route", "show", "default"])
    default_route = (
        bool(route["stdout"]) if route["available"] else None
    )

    dns_ok = False
    dns_error = None
    try:
        socket.getaddrinfo("example.com", 443,
                           type=socket.SOCK_STREAM)
        dns_ok = True
    except socket.gaierror as exc:
        dns_error = str(exc)

    tcp_ok = False
    tcp_error = None
    try:
        with socket.create_connection(("1.1.1.1", 443), timeout=2):
            tcp_ok = True
    except OSError as exc:
        tcp_error = str(exc)

    if not active:
        status = "WARN"
        message = "No active non-loopback interface"
    elif not dns_ok and not tcp_ok:
        status = "WARN"
        message = "DNS and external TCP checks failed"
    elif not dns_ok:
        status = "WARN"
        message = "DNS failed; external TCP works"
    elif not tcp_ok:
        status = "WARN"
        message = "DNS works; external TCP failed"
    else:
        status = "OK"
        message = "DNS and external TCP checks succeeded"

    return {
        "status": status,
        "active_interfaces": active,
        "default_route_detected": default_route,
        "dns_resolution_ok": dns_ok,
        "dns_error": dns_error,
        "external_tcp_ok": tcp_ok,
        "external_tcp_error": tcp_error,
        "message": message
    }


def build_report():
    checks = {}

    for name, function in [
        ("cpu", cpu_check),
        ("memory", memory_check),
        ("disk", disk_check),
        ("uptime", uptime_check),
        ("network", network_check),
        ("services", service_check),
    ]:
        try:
            checks[name] = function()
        except (OSError, RuntimeError, ValueError) as exc:
            checks[name] = {
                "status": "UNKNOWN",
                "message": str(exc)
            }

    statuses = [item["status"] for item in checks.values()]
    overall = (
        "WARN" if "WARN" in statuses
        else "UNKNOWN" if "UNKNOWN" in statuses
        else "OK"
    )

    return {
        "application": "Linux Panic Button",
        "version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "host": {
            "hostname": socket.gethostname(),
            "platform": platform.platform(),
            "kernel": platform.release(),
            "python": platform.python_version()
        },
        "overall_status": overall,
        "checks": checks
    }


def print_report(report, use_color=True):
    shades = {
        "OK": "\033[92m",
        "WARN": "\033[93m",
        "UNKNOWN": "\033[90m",
        "RESET": "\033[0m",
        "CYAN": "\033[96m"
    }

    def paint(text, shade):
        prefix = shades[shade] if use_color else ""
        suffix = shades["RESET"] if use_color else ""
        return f"{prefix}{text}{suffix}"

    print()
    print(paint("╔══════════════════════════════════════╗", "CYAN"))
    print(paint("║       LINUX PANIC BUTTON v1.0        ║", "CYAN"))
    print(paint("║       SYSTEM HEALTH AUTOPSY          ║", "CYAN"))
    print(paint("╚══════════════════════════════════════╝", "CYAN"))

    print(f" Host: {report['host']['hostname']}")
    print(f" OS:   {report['host']['platform']}")
    print()

    checks = report["checks"]
    for name in ("cpu", "memory", "disk", "uptime",
                 "network", "services"):
        item = checks[name]
        status = item["status"]
        print(
            f"[{paint(status, status)}] "
            f"{name.upper():<9} {item['message']}"
        )

    memory = checks["memory"]
    disk = checks["disk"]

    def size(number):
        value = float(number)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if value < 1024 or unit == "TB":
                return f"{value:.1f} {unit}"
            value /= 1024
        return f"{value:.1f} TB"

    print()
    print(f" RAM:  {size(memory['used_bytes'])} / "
          f"{size(memory['total_bytes'])}")
    print(f" Disk: {size(disk['used_bytes'])} / "
          f"{size(disk['total_bytes'])}")

    verdict = {
        "OK": "Your machine appears to be holding it together.",
        "WARN": "Something needs attention. Investigate the checks.",
        "UNKNOWN": "Some checks could not complete."
    }[report["overall_status"]]

    print()
    print(paint("VERDICT: " + verdict, report["overall_status"]))

    failed = checks["services"].get("failed_units", [])
    if failed:
        print(" Failed units: " + ", ".join(failed))
    print()


def save_report(report, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(
        json.dumps(report, indent=2) + "\n",
        encoding="utf-8"
    )
    temporary.replace(destination)


def main():
    parser = argparse.ArgumentParser(
        description="Read-only Linux system diagnostics"
    )
    parser.add_argument(
        "--json", type=Path, default=DEFAULT_REPORT,
        help="JSON report destination"
    )
    parser.add_argument(
        "--no-color", action="store_true",
        help="Disable terminal colors"
    )
    args = parser.parse_args()

    if platform.system() != "Linux":
        print("Warning: designed for Linux.", file=sys.stderr)

    report = build_report()
    print_report(
        report,
        use_color=sys.stdout.isatty() and not args.no_color
    )

    try:
        save_report(report, args.json)
        print(f"JSON report saved: {args.json}")
    except OSError as exc:
        print(f"Could not save report: {exc}", file=sys.stderr)
        return 2

    print("No services restarted. No system settings modified.")
    return 1 if report["overall_status"] == "WARN" else 0


if __name__ == "__main__":
    raise SystemExit(main())
