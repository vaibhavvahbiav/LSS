# Linux Panic Button

A local system health dashboard and read-only diagnostics command for Linux.
The dashboard shows CPU, memory, root disk, uptime, network throughput, and
failed systemd units. It listens on `127.0.0.1:5000` only.

## Debian package

Build the package from the repository root:

```sh
./build-deb.sh
```

This creates `dist/linux-panic-button_1.1.0_all.deb`. Install it with:

```sh
sudo apt install ./dist/linux-panic-button_1.1.0_all.deb
```

Start the dashboard at boot and open it locally at <http://127.0.0.1:5000>:

```sh
sudo systemctl enable --now linux-panic-button
```

The package also installs the `linux-panic-button` CLI. It prints a diagnostic
summary and writes JSON to `reports/latest.json` by default; choose another
destination with `--json /path/to/report.json`.

## Run from source

Install Python dependencies from `requirements.txt`, then run:

```sh
python3 app.py
python3 panic.py --json /tmp/linux-panic-button-report.json
```

The CLI is read-only. Its DNS and external TCP checks require network access;
service health information is available on systemd based systems.
