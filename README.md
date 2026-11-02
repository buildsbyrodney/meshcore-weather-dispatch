# MeshCore Weather Dispatch

This is a Raspberry Pi OS Lite/Debian application for **human-approved** dispatch of National Weather Service (NWS) alerts to one or more MeshCore channels. It monitors NWS; it never sends an alert on its own.

## Safety model

- Every transmission requires a signed-in operator pressing **Send to Mesh**.
- The server atomically claims an alert before transmitting, preventing concurrent double sends.
- Sending is rejected if an alert is canceled, superseded, expired, already acted on, or transmission is disabled.
- Radio acceptance is recorded as `accepted`, not delivery: MeshCore does not prove over-the-air delivery to all recipients.
- A newly installed instance has no selected device; manual transmissions are allowed only after a device/channel is selected and an operator explicitly confirms each send.
- Device scans never select a device automatically. An administrator must select and test one.

## Quick start (development)

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
export WEATHER_DISPATCH_CONFIG=$PWD/config.yml
python -m dispatch
```

For development only, provide a password hash and states in local test configuration. Production initialization below creates these safely without placing credentials in YAML.

## Install on Raspberry Pi OS Lite / Debian

For a new production installation, use `sudo ./scripts/install.sh` from the release checkout. It installs Nginx, refuses an already-occupied port 80 or an unrelated Nginx site, keeps Gunicorn on `127.0.0.1:8080`, creates the protected files and administrator account, validates Nginx, and prints the trusted-LAN URL `http://<Pi-IP>/`. Test login, Settings, Test Alerts, and dispatch previews through that URL; CSRF and session cookies are proxied normally. No radio message is sent by installation.

Initial HTTP is appropriate only on a trusted LAN: the installer sets `session_cookie_secure: false` so browser sessions work over HTTP. For remote or untrusted access, place an HTTPS certificate/reverse proxy in front of Nginx, set `session_cookie_secure: true` in the installer-managed config, restrict firewall access, validate `nginx -t`, then restart the service. Do not expose port 8080; it is loopback-only. `sudo ./scripts/update.sh` creates a UTC-timestamped mode-700 backup under `/var/backups/meshcore-weather-dispatch/` using SQLite's online backup API before updating. It retains the environment secret, SQLite history, counties, USB selection, TX channel, and systemd drop-ins. If Git, pip, Nginx, or service health fails it returns the checkout to the old revision and restarts it; database schema migrations cannot always be reversed, so restore the SQLite backup only with the service stopped.

1. Install prerequisites: `sudo apt update && sudo apt install -y python3-venv python3-pip`.
2. Create a dedicated user: `sudo adduser --system --group --home /opt/meshcore-weather-dispatch weatherdispatch`.
3. Copy this project to `/opt/meshcore-weather-dispatch`, create a virtual environment as that user, then install `pip install -e .`.
4. Create durable state: `sudo install -d -o weatherdispatch -g weatherdispatch -m 750 /var/lib/meshcore-weather-dispatch`.
5. Initialize protected service files and the first administrator account. This generates `WEATHER_DISPATCH_SECRET_KEY` exactly once (without printing it), stores it in a mode-600 environment file, and seeds SQLite with the password hash, initial NWS settings, and enabled manual-transmission permission:

```bash
read -s -p 'Initial administrator password: ' WD_PASSWORD; echo
printf '%s\n' "$WD_PASSWORD" | sudo /opt/meshcore-weather-dispatch/.venv/bin/python -m dispatch.bootstrap \
  --config /etc/meshcore-weather-dispatch/config.yml \
  --environment /etc/meshcore-weather-dispatch/environment \
  --database /var/lib/meshcore-weather-dispatch/dispatch.sqlite3 \
  --username weatheradmin --email you@example.org --states AL --password-stdin
unset WD_PASSWORD
sudo chown weatherdispatch:weatherdispatch /etc/meshcore-weather-dispatch/config.yml /etc/meshcore-weather-dispatch/environment /var/lib/meshcore-weather-dispatch/dispatch.sqlite3
```

Do not put the password on a command line or commit either file. Re-running the initializer preserves the existing secret, configuration file, and SQLite data; it can deliberately reset the administrator password only when given a new password.
7. Install `systemd/meshcore-weather-dispatch.service`, adjust paths if needed, then `sudo systemctl daemon-reload && sudo systemctl enable --now meshcore-weather-dispatch`. The supplied unit intentionally uses one Gunicorn worker, which owns the NWS polling thread. Check `sudo systemctl status meshcore-weather-dispatch` and `sudo journalctl -u meshcore-weather-dispatch -f`.
8. Put the app behind an HTTPS reverse proxy and restrict it to your LAN/VPN. Keep `session_cookie_secure: true` for HTTPS; use `false` only during a short local HTTP setup session.
9. Allow the service account to open the Companion serial device, then restart the service: `sudo usermod -aG dialout weatherdispatch && sudo systemctl restart meshcore-weather-dispatch`.

### Bench-test a Companion without transmitting

Connect the Companion, open **Settings**, select it manually, and use **Test selected connection**. This performs only the library's read-only `send_appstart` handshake. Settings can then read the existing Companion channels and lets the administrator select one by name; it never creates or changes a channel. Neither action sends a channel message. Use an isolated/private bench channel before sending any manual test message.

## Configuration

### Configuration precedence and routine administration

The installer-managed YAML file is deliberately limited to service settings: database path, host/port, secret environment-variable name, TLS cookie behavior, system time defaults, alert types, and message cap. It contains no secret, administrator password, selected counties, NWS contact, states, or manual transmission switch.

SQLite is authoritative for the web-managed administrator account, NWS contact email (rendered into a fixed `MeshCoreWeatherDispatch/0.1 (email)` User-Agent), polling interval (never under 60 seconds), selected polling states, time zone, coverage catalog/counties, device/channel selections, and normal transmission enablement. Changes take effect on the next poll without a service restart. A legacy YAML installation seeds any missing SQLite values once and never overwrites later web changes.

The environment file is intentionally not a web setting. `WEATHER_DISPATCH_SECRET_KEY` signs sessions and confirmations; it is generated once, mode 600, never rendered or logged, and must be retained across upgrades. Manual-transmission permission is managed and audited in Settings; every send still requires an authenticated operator, a signed preview, an explicit confirmation, a verified Companion, and a verified selected channel.

Use **Settings** for daily administration: change the administrator username/password (with current-password verification), set NWS contact/states/interval, select time zone, manage Coverage Counties, and set normal manual transmission permission. Removing a state that contains selected counties requires explicit confirmation and preserves those county records; they simply are not monitored until that state is restored. Empty county selection remains fail-closed and never means nationwide monitoring.

### Coverage Counties

Use **Settings → Manage Coverage Counties** to import the official NWS county-zone catalog from `api.weather.gov/zones?type=county`. The importer accepts only county (`C`) UGC entries, derives SAME as `0` plus the official state FIPS and county FIPS suffix, and records the source URL, retrieval time, and ETag/Last-Modified value when NWS provides one. The catalog and selected counties are stored separately in SQLite for offline use. A failed or invalid import leaves both the prior catalog and coverage selection unchanged.

The durable SQLite selection takes precedence over `config.yml` after first start. Existing `config.yml` county entries are migrated once, de-duplicated, into that selection; `config.yml` remains only the migration seed thereafter. Back up `dispatch.sqlite3` before upgrades. An empty selection is fail-closed and matches no county alerts—it never enables nationwide monitoring. Saving more than 25 counties requires an explicit confirmation. Changes take effect on the next poll and immediately in Test Alerts; no service restart is required.

Selected states now live in Settings. Legacy `counties` accepts NWS SAME six-digit codes (for example `001089`; shorter numeric input is normalized), UGC county codes (for example `ALC089`), or an NWS URL ending in either code and is migrated once to durable coverage selection. The filter checks the NWS JSON `geocode.SAME` and `geocode.UGC` values; it intentionally does not inspect polygons, so missing geometry is safe. An alert affecting several counties matches when any configured county matches. `alert_types` is an installer-managed allowlist. Select exactly one existing regional transmit channel by name in Settings; the application records its radio slot but never changes the radio configuration.

### Test Alerts

**Test Alerts** creates one local, synthetic CAP warning for exercising the same geographic filtering, byte shortening, audit history, and manual dispatch controls used by the NWS feed. It never contacts NWS, opens the Companion, or transmits by itself. Every generated alert, dashboard card, shortened message, and audit entry—including later send, ignore, expiration, revision, or cancellation events—is marked **TEST ONLY**. Sending or ignoring it remains a separate operator decision requiring the normal authenticated preview and explicit confirmation. Synthetic and genuine CAP revision families are kept separate even if a submitted reference names a real alert.

The checkbox list is populated only from SQLite-selected Coverage Counties. Legacy YAML county entries are accepted once during an upgrade migration, then retained only in SQLite; new installations never need county YAML.

Select one or more entries. The application stores every selected SAME/UGC code in one synthetic alert's `geocode` fields and does not split the selection into multiple alerts or radio messages. An invalid, unconfigured, or empty selection is rejected.

Test Alerts has no history-clearing control. Its audit entries share the durable activity log by design; the application has no operation that deletes NWS history or radio transmission records.

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

Before starting, optionally back up all durable state: `sudo cp -a /etc/meshcore-weather-dispatch/config.yml /etc/meshcore-weather-dispatch/config.yml.backup`, `sudo cp -a /etc/meshcore-weather-dispatch/environment /etc/meshcore-weather-dispatch/environment.backup`, and `sudo cp -a /var/lib/meshcore-weather-dispatch/dispatch.sqlite3 /var/lib/meshcore-weather-dispatch/dispatch.sqlite3.backup`. Keep the environment file unchanged, especially `WEATHER_DISPATCH_SECRET_KEY`; do not rerun the initializer during an ordinary upgrade and do not replace the config with the example. After the upgrade, sign in and use Settings for time zone, NWS, account, manual transmission, and Coverage Counties changes.

### USB Companion setup

Open Settings as an administrator and choose **Detect devices**. The scan reads `/dev/serial/by-id` and serial descriptors; it does not open or change any device. Pick a result and test it. On success, the application stores both the selected stable `/dev/serial/by-id/...` path and USB identity (VID, PID, serial number). At reconnect it resolves the by-id path first, then only a unique matching USB identity. Zero matches means disconnected; multiple matches means ambiguous. It will never guess and select a different radio. Remove or change the device in Settings without stopping NWS polling.

`dispatch/meshcore_adapter.py` uses the official [`meshcore`](https://github.com/meshcore-dev/meshcore_py) Python library’s serial transport and `send_chan_msg(channel_index, message)` command. It requires the Settings-managed manual-transmission permission, a verified Companion, and a verified selected channel. The application never creates or changes channels except through the separately confirmed Manage Channels action. Verify the exact library/firmware pairing on a bench radio before using manual transmissions.

### Channel and time-zone settings

Settings can query a selected-but-unsaved detected Companion using a read-only connection. Saving the device still requires an explicit action and preserves the stable USB identity check. The **Manage Channels** area reads the device-reported capacity and every slot. It reports three distinct states: **occupied**, **confirmed available** (an empty name with a returned all-zero 16-byte secret), and **unknown**. It only offers confirmed-empty slots; an error or incomplete payload is unknown and is never a candidate for writing or deletion. The official Python library calls the channel fields `channel_name` and `channel_secret`; this application reads those actual fields (while retaining compatibility with the older short spellings). If a firmware/library pair instead returns an error or incomplete payload for an unused slot, it remains unknown rather than being counted as occupied or available.

The **Manage Channels** view shows only configured channels, with a compact total/configured/available summary. Empty and unknown slots are deliberately not listed. Adding a hashtag channel is an explicit administrative configuration action—not a weather dispatch. Enter a public hashtag and tick the confirmation box; the app suggests the first confirmed-empty slot internally, then re-reads that slot before writing. The app derives the documented 16-byte hashtag key from the first 16 bytes of `SHA-256(channel name)`, writes exactly that slot, then reads it back. It never deletes, replaces, renames, or creates channels in the background. Any channel configuration write clears the saved transmit choice, requiring an administrator to select and revalidate it again.

Deleting a channel is similarly explicit: a Delete button appears only for a confirmed occupied, non-Public channel that is not the selected transmit channel. The administrator must use the exact-name Remove dialog; the server independently requires its confirmation value and the Companion is re-read immediately before the documented clear operation (empty name plus zero 16-byte secret). The slot table is read again afterward. The Public channel has no Delete control, and the selected transmit channel must be deselected before deletion. Deletion removes that channel configuration from the Companion; it cannot be undone without recreating it and does not affect other slots.

The **Device Name** field is populated from the Companion's read-only `SELF_INFO` response whenever it is available; a saved application value is never presented as a live radio value when that read fails. Saving first performs `APP_START`, then uses the official library's `set_name()` command (`CMD_SET_ADVERT_NAME`, byte `0x08`). The Companion must return `OK`, and a new serial session must return the exact requested name in `SELF_INFO` before the console reports success. The firmware implementation persists this command with `savePrefs()`, so a verified name survives a Companion reboot; the app's separate-session read-back and tests cover the reconnect path. Current documented `DEVICE_INFO` payloads do not advertise a universal name-length limit. The firmware may truncate an oversized command while returning `OK`, so the application never silently accepts truncation: it reports both the requested and actual radio name as an unverified failure. It honours an optional library-reported limit when available.

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
