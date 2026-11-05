# MeshCore Weather Dispatch

MeshCore Weather Dispatch is a small web console for a human operator to review National Weather Service (NWS) alerts and manually send an approved, compact message through a MeshCore Companion.

It monitors NWS alerts, but it **never transmits automatically**. Every weather or test message needs an authenticated administrator, an explicit action, and a final confirmation. A Companion accepting a command is recorded as radio acceptance; it is not proof of over-the-air delivery.

Current release candidate: [`v1.0.0-rc.2`](docs/releases/v1.0.0-rc.2.md).

## Supported systems and hardware

The supported deployment target is a current Raspberry Pi OS Lite, Debian, or Ubuntu system with `systemd`, `apt`, Python 3.11 or later, a trusted LAN, and an otherwise unused HTTP port 80. A MeshCore Companion supported by the installed official `meshcore` Python library is optional until manual transmission is configured.

The deployment uses Nginx on port 80 and Gunicorn only on `127.0.0.1:8080`. The installer refuses port-80 conflicts and unrelated/customized Nginx sites instead of replacing them.

## Safety model

- Manual dispatch only; polling, startup, reconnects, Settings saves, and Test Alert creation never send a message.
- Send requires login, CSRF validation, a server-issued signed preview, explicit confirmation, a current eligible alert, selected Companion/channel validation, and an atomic database claim.
- Expired, canceled, superseded, already handled, or duplicate alerts cannot be dispatched.
- The complete outgoing message, including an optional operator comment, is checked against the configured UTF-8 byte limit.
- Test Alerts are visibly `TEST ONLY`, remain separate from real NWS alert history, and never contact the radio when created.
- Settings changes and alert actions are audited. Normal manual-transmission permission is stored in SQLite and can be disabled at any time.

## Fresh installation

Do not create or edit YAML or environment files for a new installation. The installer creates the service account, a clean Git checkout, virtual environment, Nginx configuration, systemd service, protected state directories, a random session secret, and the initial administrator account.

```bash
sudo apt update
sudo apt install -y git
git clone https://github.com/buildsbyrodney/meshcore-weather-dispatch.git
cd meshcore-weather-dispatch
sudo ./scripts/install.sh
```

The installer prompts for an administrator username, NWS contact email, polling state codes such as `AL,TN`, and an initial password twice. Administrator passwords require at least six characters; no character-type or periodic-change policy applies. It prints the trusted-LAN URL when the service responds, normally `http://<Pi-IP>/`.

Before opening the web interface, grant the service account permission to communicate with USB serial devices and reboot the Pi:

```bash
sudo usermod -aG dialout weatherdispatch
sudo reboot
```

Membership in `dialout` lets MeshCore Weather Dispatch access a USB serial Companion. The reboot activates that membership and also ensures Nginx loads the Weather Dispatch site instead of its default welcome page; no separate service restart is needed when these steps are followed.

If power, networking, or a package operation interrupts the first install after the application checkout is created, rerunning the installer stops safely rather than reusing it. First confirm that no initial state was created, then remove only the incomplete application checkout and rerun from the source checkout:

```bash
sudo test ! -e /etc/meshcore-weather-dispatch/environment && sudo test ! -e /var/lib/meshcore-weather-dispatch/dispatch.sqlite3 && sudo rm -rf /opt/meshcore-weather-dispatch
sudo ./scripts/install.sh
```

Do not use those removal commands for a completed installation; use `sudo /opt/meshcore-weather-dispatch/scripts/update.sh` instead.

The secret is generated once in `/etc/meshcore-weather-dispatch/environment` with restrictive permissions. It is never displayed by the application. The initial administrator password is stored only as a secure hash in SQLite. New installations allow manual transmissions in Settings, but no Companion or transmit channel is selected and installation cannot open or send through a radio.

Verify the local service after installation:

```bash
sudo systemctl status meshcore-weather-dispatch --no-pager
sudo systemctl status nginx --no-pager
sudo ss -ltnp | grep ':8080'
```

The Gunicorn listener must be `127.0.0.1:8080`, not a LAN-facing address.

## Initial web administration

1. Visit the installer’s LAN URL and sign in.
2. In **Settings → NWS Monitoring**, confirm the contact email, polling interval (at least 60 seconds), and state codes. The contact is included in the NWS User-Agent.
3. In **Settings → Alert Types**, import the [official NWS alert event-type catalog](https://api.weather.gov/alerts/types), then explicitly select the warnings, watches, advisories, and other alerts to monitor. The catalog is cached locally; a failed refresh leaves the existing cached catalog and selection intact. If NWS later removes a previously selected event from its catalog, the selection remains visible until an operator explicitly removes it. Disabling a type stops new alerts of that type; it does not modify recorded alerts or send a message.
4. In **Settings → Coverage Counties**, import the official NWS county catalog once, then select and explicitly save the counties to monitor. County selection is stored in SQLite; an empty selection is fail-closed and does not mean “all counties.”
5. Set the application time zone. Timestamps stay in UTC internally but display in the chosen IANA time zone with daylight-saving transitions.
6. Use **Settings → Manage Users → My Account** to change your username or password when needed. Changing credentials requires the current password.
7. Use **Settings → Manage Users** to add, edit, disable, or remove local operator accounts. Every active account has the same permissions; usernames are retained in audit history so manual actions remain attributable. The console will not let an operator remove or disable the last active account.

Coverage changes take effect on the next NWS poll and in Test Alerts without a service restart. Counties outside the selected polling states are retained but not monitored until their state is enabled again.

## Companion and channel setup

Connect the Companion over USB, then open **Settings → Companion device**.

1. Rescan USB devices and select the intended device. The scan itself does not open, alter, or select a radio automatically.
2. Use **Test selected connection** for the read-only Companion handshake. If Linux denies access, add the service account to `dialout` and reboot:

   ```bash
   sudo usermod -aG dialout weatherdispatch
   sudo reboot
   ```

3. Discover the configured channels and explicitly select the desired transmit channel by name. The app records its slot internally and rechecks the name and slot immediately before every send.

**Manage Channels** and **Device Name** are separate, explicitly confirmed radio-configuration actions. They never require weather transmission permission and never occur in the background. Adding or deleting a channel revalidates the live radio state before and after the write; the Public channel and current TX channel are protected from deletion.

If the radio is unplugged, ambiguous, lacks permission, or times out, the console reports an error and fails safely. It never chooses another USB radio automatically.

## Controlled test messages and Test Alerts

Use a private bench channel for first hardware tests.

- **Test Messaging** sends an arbitrary short message only after the administrator enters text, checks the confirmation box, and accepts the browser confirmation. It always uses the saved TX channel.
- **Test Alerts** creates one local synthetic CAP warning for selected coverage counties. It is marked `TEST ONLY` in the console, message, and audit trail. Creating it does not poll NWS, open the Companion, or transmit.
- A Test Alert follows the same manual preview, UTF-8 cap, channel validation, and Send/Ignore workflow as an NWS warning.

For a real NWS warning, review the compact row and Details, select **Send**, inspect the exact server-authoritative outgoing message, channel, byte count, and comment in the confirmation dialog, then choose **Confirm Send**. Radio acceptance does not prove delivery through the mesh.

## Updates, backups, and recovery

Administrators can also open **Settings → Software Updates**. It shows the installed version and Git commit, checks the configured Git repository for `main`, and retains the guarded updater's output while the application restarts. The page requires a separate explicit confirmation before an update or a Raspberry Pi reboot. It cannot accept a command, branch, URL, or package name from the browser.

The Flask service remains unprivileged with `NoNewPrivileges=true`. It requests only the fixed `update` or `reboot` operation over a mode-0660, socket-activated systemd endpoint; a root-owned worker validates that fixed request, serializes updates, and writes browser-readable status and logs. Flask has no sudoers entry and never runs a root command. The worker survives a Gunicorn restart because systemd owns it. Web-triggered updates do not replace root worker scripts or systemd units; apply future changes to those privileged assets through the same trusted-terminal procedure below.

### One-time bootstrap for existing v1.0.0-rc.2 installations

The older web updater cannot install this correction itself. On WXALERT, run these commands once from a trusted terminal (not from the web console):

```sh
cd /opt/meshcore-weather-dispatch
sudo -u weatherdispatch git fetch origin main
sudo -u weatherdispatch git checkout --detach origin/main
sudo -u weatherdispatch /opt/meshcore-weather-dispatch/.venv/bin/pip install --upgrade -e /opt/meshcore-weather-dispatch
sudo /opt/meshcore-weather-dispatch/scripts/update.sh
```

The update script restarts the application and enables the new request socket; no additional restart is required, and it does not reboot the Pi. Verify with `sudo systemctl status meshcore-weather-dispatch-update.socket --no-pager`. The update page can then perform future confirmed updates.

The update worker uses the same backup and rollback procedure as the command below. It restarts the application after a completed update but never reboots automatically. A reboot is sometimes necessary after an update - follow the instructions in the console.

For a managed installation, run:

```bash
sudo /opt/meshcore-weather-dispatch/scripts/update.sh
```

The updater refuses unknown/customized main systemd or Nginx files, preserves systemd drop-ins, creates a timestamped mode-700 backup under `/var/backups/meshcore-weather-dispatch/`, and uses SQLite’s online backup API before changing the checkout. It preserves the environment secret, administrator credentials, SQLite alert/audit history, county catalog and coverage, NWS settings, Companion identity, TX channel, and existing manual-transmission permission.

If an update fails, it restores the previous Git revision and managed service/proxy files, then attempts to restart the prior service. Python dependency rollback may require manual recovery; its saved `pip-freeze.txt` is in the backup directory. Database schema changes are not universally reversible.

To restore the saved SQLite file, first stop the service, preserve the current database separately, copy the chosen backup into `/var/lib/meshcore-weather-dispatch/dispatch.sqlite3`, restore ownership, then start the service:

```bash
sudo systemctl stop meshcore-weather-dispatch
sudo cp -a /var/lib/meshcore-weather-dispatch/dispatch.sqlite3 /var/lib/meshcore-weather-dispatch/dispatch.sqlite3.before-restore
sudo cp -a /var/backups/meshcore-weather-dispatch/REPLACE-WITH-TIMESTAMP/dispatch.sqlite3 /var/lib/meshcore-weather-dispatch/dispatch.sqlite3
sudo chown weatherdispatch:weatherdispatch /var/lib/meshcore-weather-dispatch/dispatch.sqlite3
sudo systemctl start meshcore-weather-dispatch
```

Do not replace the environment file or rerun the initializer during an ordinary upgrade.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Page is unavailable | `sudo systemctl status nginx meshcore-weather-dispatch --no-pager`, then `sudo journalctl -u meshcore-weather-dispatch -n 100 --no-pager`. |
| Port 80 installation refusal | Do not remove another application blindly. Resolve the existing web-server/site ownership or use a dedicated host. |
| Companion permission error | Run `sudo usermod -aG dialout weatherdispatch`, then `sudo reboot`; this grants the service account access to USB serial devices. |
| Nginx welcome page | Run `sudo reboot`. If it remains, run `sudo nginx -t` and `sudo systemctl reload nginx`, then check `sudo systemctl status nginx --no-pager`. |
| No transmit channel | Select a verified saved Companion, discover channels, and explicitly select one. |
| Send is blocked | Check manual transmission permission, alert eligibility, message byte count, device/channel validation, and the exact preview confirmation. |
| NWS feed stale | Verify LAN/DNS access, contact email, selected states/counties, and the NWS service; the dashboard retains the error. |
| County does not match | Check that it is selected in Coverage Counties and that its state is enabled in NWS Monitoring. |

## HTTP and HTTPS

The default Nginx setup serves HTTP on a trusted LAN to make initial setup possible. HTTP does not protect administrator credentials or session cookies on an untrusted network.

Before remote, Wi-Fi guest, Internet, or otherwise untrusted access, put a maintained TLS certificate/reverse proxy in front of Nginx, set `session_cookie_secure: true` in the installer-managed `/etc/meshcore-weather-dispatch/config.yml`, run `sudo nginx -t`, and restart the service. Restrict network access with your firewall/VPN. Do not expose Gunicorn directly; it must remain loopback-only.

## Configuration ownership

The installer-managed YAML file contains service settings only: database path, loopback host/port, cookie-security mode, and message cap. The protected environment file contains the session secret only. SQLite is authoritative for administrator credentials, NWS contact/states/polling interval, alert-type catalog/selection, time zone, county catalog/selection, Companion/channel selections, and normal manual-transmission permission.

Legacy YAML counties, alert types, credentials, and operational settings are migrated once into SQLite when missing; existing SQLite choices are not overwritten.

## Limits

This project intentionally does not implement autonomous sending, polygon-aware receiver targeting, region-scoped flooding, delivery confirmation, or automatic cancellation broadcasts. Validate your exact Companion firmware and the installed `meshcore` library on private bench hardware before operational use.

See [CHANGELOG.md](CHANGELOG.md) for release history.
