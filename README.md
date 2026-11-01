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

`dispatch/meshcore_adapter.py` uses the official [`meshcore`](https://github.com/meshcore-dev/meshcore_py) Python library’s serial transport and `send_chan_msg(channel_index, message)` command. It requires both `MESHCORE_SEND_ENABLED=1` and `transmit_enabled: true`; dry-run prevents all message sends but still permits read-only device/channel queries. The application never creates or changes channels except through the separately confirmed Manage Channels action. Verify the exact library/firmware pairing on a bench radio before enabling production transmissions.

### Channel and time-zone settings

Settings can query a selected-but-unsaved detected Companion using a read-only connection. Saving the device still requires an explicit action and preserves the stable USB identity check. The **Manage Channels** area reads the device-reported capacity and every slot. It reports three distinct states: **occupied**, **confirmed available** (an empty name with a returned all-zero 16-byte secret), and **unknown**. It only offers confirmed-empty slots; an error or incomplete payload is unknown and is never a candidate for writing or deletion. The official Python library calls the channel fields `channel_name` and `channel_secret`; this application reads those actual fields (while retaining compatibility with the older short spellings). If a firmware/library pair instead returns an error or incomplete payload for an unused slot, it remains unknown rather than being counted as occupied or available.

Adding a hashtag channel is an explicit administrative configuration action—not a weather dispatch. Enter a public hashtag, choose a confirmed empty slot, tick the confirmation box, and submit. The app derives the documented 16-byte hashtag key from the first 16 bytes of `SHA-256(channel name)`, writes exactly that slot, then reads it back. It never deletes, replaces, renames, or creates channels in the background. Any channel configuration write clears the saved transmit choice, requiring an administrator to select and revalidate it again.

Deleting a channel is similarly explicit: a Delete button appears only for a confirmed occupied, non-Public channel. The administrator must confirm the exact name and slot in both the page and browser confirmation. The Companion is re-read immediately before the documented clear operation (empty name plus zero 16-byte secret) and the slot table is re-read afterward. The Public channel has no Delete control, and the selected transmit channel must be deselected before deletion. Deletion removes that channel configuration from the Companion; it cannot be undone without recreating it and does not affect other slots.

The **Device Name** field is populated from the Companion's read-only `SELF_INFO` response whenever it is available. Saving is an explicit configuration action: the library command must succeed and the exact name must be returned by a new read-only `SELF_INFO` query. Current documented `DEVICE_INFO` payloads do not advertise a universal name-length limit, so the application never truncates a submitted name; it honours an optional library-reported limit when available, otherwise reports the Companion/library rejection or a read-back mismatch.

### Safe Companion diagnostics for an unknown slot state

If a tested Companion reports unknown slots, collect a read-only, redacted diagnostic before changing configuration. Replace the device path with the selected stable `/dev/serial/by-id/...` path. This sends only `APP_START`, `DEVICE_QUERY`, and `GET_CHANNEL`; it does not print channel secrets, transmit, write, delete, or change any radio setting.

```bash
sudo -u weatherdispatch -H /opt/meshcore-weather-dispatch/.venv/bin/python - /dev/serial/by-id/REPLACE-WITH-SELECTED-DEVICE <<'PY'
import asyncio, sys
from meshcore import MeshCore, EventType

async def main(path):
    radio = await MeshCore.create_serial(path)
    try:
        self_info = await radio.commands.send_appstart()
        info = await radio.commands.send_device_query()
        payload = info.payload if isinstance(info.payload, dict) else {}
        self_payload = self_info.payload if isinstance(self_info.payload, dict) else {}
        print({"self_info_event": str(self_info.type), "name": self_payload.get("name", "")})
        print({"device_info_event": str(info.type), "max_channels": payload.get("max_channels"), "model": payload.get("model"), "version": payload.get("ver") or payload.get("version")})
        count = payload.get("max_channels")
        if not isinstance(count, int):
            return
        for slot in range(count):
            result = await radio.commands.get_channel(slot)
            channel = result.payload if isinstance(result.payload, dict) else {}
            secret = channel.get("channel_secret", channel.get("secret"))
            print({"slot": slot, "event": str(result.type), "name": channel.get("name") or channel.get("channel_name", ""), "has_16_byte_secret": isinstance(secret, (bytes, bytearray)) and len(secret) == 16})
    finally:
        await radio.disconnect()

asyncio.run(main(sys.argv[1]))
PY
```

Select any valid IANA time zone in Settings. The application saves this independently of the operating system and renders NWS times, sync time, and activity history in that zone with daylight-saving transitions applied. Timestamps remain stored in UTC.

## Limits

This project does not implement polygon receivers, region-scoped flooding, delivery confirmation, autonomous sending, or an automatic cancellation broadcast. A radio ACK means the Companion accepted the command, not that the mesh delivered it.
