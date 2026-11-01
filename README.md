# MeshCore Weather Dispatch

This is a Raspberry Pi OS Lite/Debian application for **human-approved** dispatch of National Weather Service (NWS) alerts to one or more MeshCore channels. It monitors NWS; it never sends an alert on its own.

## Safety model

- Every transmission requires a signed-in operator pressing **Send to Mesh**.
- The server atomically claims an alert before transmitting, preventing concurrent double sends.
- Sending is rejected if an alert is canceled, superseded, expired, already acted on, or transmission is disabled.
- Radio acceptance is recorded as `accepted`, not delivery: MeshCore does not prove over-the-air delivery to all recipients.
- A newly installed instance has no selected device and `transmit_enabled: false`.
- Device scans never select a device automatically. An administrator must select and test one.

## Quick start (development)

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
export WEATHER_DISPATCH_CONFIG=$PWD/config.yml
python -m dispatch
```

Log in using the password hash in `config.yml` (generate it with `python -c "from werkzeug.security import generate_password_hash; print(generate_password_hash('choose-a-password'))"`). Use a long random `secret_key` and set `WEATHER_DISPATCH_SECRET_KEY` instead of storing it in the file.

## Install on Raspberry Pi OS Lite / Debian

1. Install prerequisites: `sudo apt update && sudo apt install -y python3-venv python3-pip`.
2. Create a dedicated user: `sudo adduser --system --group --home /opt/meshcore-weather-dispatch weatherdispatch`.
3. Copy this project to `/opt/meshcore-weather-dispatch`, create a virtual environment as that user, then install `pip install -e .`.
4. Create durable state: `sudo install -d -o weatherdispatch -g weatherdispatch -m 750 /var/lib/meshcore-weather-dispatch`.
5. Copy `config.example.yml` to `/etc/meshcore-weather-dispatch/config.yml`; make it readable only by the service account: `sudo install -o weatherdispatch -g weatherdispatch -m 600 config.example.yml /etc/meshcore-weather-dispatch/config.yml`. Replace the password-hash placeholder and use an NWS User-Agent with a monitored contact address.
6. Generate a session secret with `openssl rand -hex 32`; put `WEATHER_DISPATCH_SECRET_KEY=...` and `MESHCORE_SEND_ENABLED=0` in `/etc/meshcore-weather-dispatch/environment` (mode `600`). Do not commit that file.
7. Install `systemd/meshcore-weather-dispatch.service`, adjust paths if needed, then `sudo systemctl daemon-reload && sudo systemctl enable --now meshcore-weather-dispatch`. The supplied unit intentionally uses one Gunicorn worker, which owns the NWS polling thread. Check `sudo systemctl status meshcore-weather-dispatch` and `sudo journalctl -u meshcore-weather-dispatch -f`.
8. Put the app behind an HTTPS reverse proxy and restrict it to your LAN/VPN. Keep `session_cookie_secure: true` for HTTPS; use `false` only during a short local HTTP setup session.
9. Allow the service account to open the Companion serial device, then restart the service: `sudo usermod -aG dialout weatherdispatch && sudo systemctl restart meshcore-weather-dispatch`.

### Bench-test a Companion without transmitting

Leave `dry_run: true`, `transmit_enabled: false`, and `MESHCORE_SEND_ENABLED=0`. Connect the Companion, open **Settings**, select it manually, and use **Test selected connection**. In dry-run this verifies the chosen identity without opening the serial port; with `dry_run: false` it performs only the library's read-only `send_appstart` handshake. Settings can then read the existing Companion channels and lets the administrator select one by name; it never creates or changes a channel. Neither action sends a channel message. Do not change the two transmission interlocks until you have tested with an isolated/bench radio and confirmed the selected channel.

## Configuration

`states` accepts NWS two-letter state codes. `counties` accepts NWS SAME six-digit codes (for example `001089`; shorter numeric input is normalized), UGC county codes (for example `ALC089`), or an NWS URL ending in either code. The filter checks the NWS JSON `geocode.SAME` and `geocode.UGC` values; it intentionally does not inspect polygons, so missing geometry is safe. An alert affecting several counties matches when any configured county matches. Empty county list means all alerts returned by a state feed. `alert_types` is an allowlist. Select exactly one existing regional transmit channel by name in Settings; the application records its radio slot but never changes the radio configuration.

The NWS endpoint is polled roughly every 60 seconds. CAP `id`, `references`, `messageType`, `sent`, and `expires` are persisted. CAP-linked revisions form one related history. A normalized fingerprint prevents a new CAP ID with unchanged substantive content from becoming a duplicate actionable item. A material revision of a sent or ignored warning becomes a clearly labeled manual decision; a cancellation closes only pending revisions and never broadcasts automatically. The dashboard shows last successful synchronization, errors, and a stale-feed warning. The full NWS description is retained in SQLite while the derived radio text is capped by UTF-8 bytes.

## Upgrade an existing Raspberry Pi installation

These commands update only the checked-out application and its virtual-environment dependencies. They do **not** copy `config.example.yml`, touch `/etc/meshcore-weather-dispatch/environment`, alter the selected USB device/channel saved in SQLite, or replace the SQLite database.

```bash
sudo systemctl stop meshcore-weather-dispatch
sudo -u weatherdispatch -H git -C /opt/meshcore-weather-dispatch fetch --prune origin
sudo -u weatherdispatch -H git -C /opt/meshcore-weather-dispatch checkout main
sudo -u weatherdispatch -H git -C /opt/meshcore-weather-dispatch pull --ff-only origin main
sudo -u weatherdispatch -H /opt/meshcore-weather-dispatch/.venv/bin/pip install --upgrade -e /opt/meshcore-weather-dispatch
sudo usermod -aG dialout weatherdispatch
sudo systemctl daemon-reload
sudo systemctl start meshcore-weather-dispatch
sudo systemctl status meshcore-weather-dispatch --no-pager
```

Before starting, optionally make a separate backup of the only two local state files: `sudo cp -a /etc/meshcore-weather-dispatch/config.yml /etc/meshcore-weather-dispatch/config.yml.backup` and `sudo cp -a /var/lib/meshcore-weather-dispatch/dispatch.sqlite3 /var/lib/meshcore-weather-dispatch/dispatch.sqlite3.backup`. Keep the existing environment file unchanged, especially `MESHCORE_SEND_ENABLED=0`. For this release, add `timezone: America/Chicago` (or the local IANA timezone) with `sudoedit /etc/meshcore-weather-dispatch/config.yml`; do not replace the file with the example configuration.

### USB Companion setup

Open Settings as an administrator and choose **Detect devices**. The scan reads `/dev/serial/by-id` and serial descriptors; it does not open or change any device. Pick a result and test it. On success, the application stores both the selected stable `/dev/serial/by-id/...` path and USB identity (VID, PID, serial number). At reconnect it resolves the by-id path first, then only a unique matching USB identity. Zero matches means disconnected; multiple matches means ambiguous. It will never guess and select a different radio. Remove or change the device in Settings without stopping NWS polling.

`dispatch/meshcore_adapter.py` uses the official [`meshcore`](https://github.com/meshcore-dev/meshcore_py) Python library’s serial transport and `send_chan_msg(channel_index, message)` command. It requires both `MESHCORE_SEND_ENABLED=1` and `transmit_enabled: true`; dry-run mode never opens a serial connection. Set `channel_index` to an already configured Companion channel slot. The application never creates or changes channels. Verify the exact library/firmware pairing on a bench radio before enabling production transmissions.

## Limits

This project does not implement polygon receivers, region-scoped flooding, delivery confirmation, autonomous sending, or an automatic cancellation broadcast. A radio ACK means the Companion accepted the command, not that the mesh delivered it.
