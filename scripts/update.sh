#!/usr/bin/env bash
set -euo pipefail
[[ ${EUID} -eq 0 ]] || { echo 'Run with sudo.' >&2; exit 1; }
APP=/opt/meshcore-weather-dispatch; ETC=/etc/meshcore-weather-dispatch; STATE=/var/lib/meshcore-weather-dispatch; DB=$STATE/dispatch.sqlite3; BACKUP=/var/backups/meshcore-weather-dispatch/$(date -u +%Y%m%dT%H%M%SZ)
SELF=$(readlink -f "$0")
UPDATE_BRANCH=${UPDATE_BRANCH:-main}
[[ -d "$APP/.git" ]] || { echo 'Not an updateable Git checkout; refusing update.' >&2; exit 1; }
runuser -u weatherdispatch -- git -C "$APP" remote get-url origin >/dev/null; runuser -u weatherdispatch -- git -C "$APP" diff --quiet || { echo 'Local application edits detected; refusing update.' >&2; exit 1; }
UNIT=/etc/systemd/system/meshcore-weather-dispatch.service
if grep -q 'Managed by MeshCore Weather Dispatch' "$UNIT"; then :
elif grep -q '^Description=MeshCore Weather Dispatch (manual NWS alert console)$' "$UNIT" && grep -q "gunicorn --workers 1 --bind 127.0.0.1:8080 'dispatch:create_app()'" "$UNIT" && grep -q '^User=weatherdispatch$' "$UNIT" && grep -q '^Environment=WEATHER_DISPATCH_CONFIG=/etc/meshcore-weather-dispatch/config.yml$' "$UNIT"; then echo 'Recognized legacy Weather Dispatch unit; migrating it to the managed unit.'
else echo 'Service unit is unknown or customized; refusing overwrite. Preserve custom changes in a .d override.' >&2; exit 1; fi
if command -v nginx >/dev/null; then
 [[ ! -f /etc/nginx/sites-available/meshcore-weather-dispatch ]] || grep -q 'Managed by MeshCore Weather Dispatch' /etc/nginx/sites-available/meshcore-weather-dispatch || { echo 'Nginx site is customized; refusing overwrite.' >&2; exit 1; }
else
 ss -ltnH '( sport = :80 )' | grep -q . && { echo 'Port 80 is occupied; cannot safely add Nginx. Existing service was left running.' >&2; exit 1; }
fi
runuser -u weatherdispatch -- git -C "$APP" fetch --prune origin; OLD=$(runuser -u weatherdispatch -- git -C "$APP" rev-parse HEAD)
install -d -m 700 "$BACKUP" "$BACKUP/etc" "$BACKUP/systemd" "$BACKUP/nginx" "$BACKUP/libexec" "$BACKUP/sudoers"
[[ -f "$DB" ]] && python3 - "$DB" "$BACKUP/dispatch.sqlite3" <<'PY'
import sqlite3,sys
s,t=sys.argv[1:]; a=sqlite3.connect(s); b=sqlite3.connect(t)
try: a.backup(b)
finally: b.close(); a.close()
PY
for f in "$ETC/config.yml" "$ETC/environment"; do [[ -f "$f" ]] && cp -a "$f" "$BACKUP/etc/$(basename "$f")"; done
cp -a /etc/systemd/system/meshcore-weather-dispatch.service "$BACKUP/systemd/service"; [[ -f /etc/nginx/sites-available/meshcore-weather-dispatch ]] && cp -a /etc/nginx/sites-available/meshcore-weather-dispatch "$BACKUP/nginx/site"
for f in /usr/local/libexec/meshcore-weather-dispatch-update /usr/local/libexec/meshcore-weather-dispatch-update-worker; do [[ -f "$f" ]] && cp -a "$f" "$BACKUP/libexec/$(basename "$f")"; done
for f in /etc/systemd/system/meshcore-weather-dispatch-update.socket /etc/systemd/system/meshcore-weather-dispatch-update@.service; do [[ -f "$f" ]] && cp -a "$f" "$BACKUP/systemd/$(basename "$f")"; done
runuser -u weatherdispatch -- sh -c 'cd "$1" && .venv/bin/pip freeze' sh "$APP" > "$BACKUP/pip-freeze.txt"
rollback(){ echo "Update failed; restoring $OLD. Backups: $BACKUP" >&2; runuser -u weatherdispatch -- git -C "$APP" checkout -q "$OLD" || true; cp -a "$BACKUP/systemd/service" /etc/systemd/system/meshcore-weather-dispatch.service; [[ -f "$BACKUP/nginx/site" ]] && cp -a "$BACKUP/nginx/site" /etc/nginx/sites-available/meshcore-weather-dispatch; for f in meshcore-weather-dispatch-update meshcore-weather-dispatch-update-worker; do [[ -f "$BACKUP/libexec/$f" ]] && cp -a "$BACKUP/libexec/$f" "/usr/local/libexec/$f"; done; for f in meshcore-weather-dispatch-update.socket meshcore-weather-dispatch-update@.service; do [[ -f "$BACKUP/systemd/$f" ]] && cp -a "$BACKUP/systemd/$f" "/etc/systemd/system/$f"; done; runuser -u weatherdispatch -- sh -c 'cd "$1" && .venv/bin/pip install -r "$2" && .venv/bin/pip install -e .' sh "$APP" "$BACKUP/pip-freeze.txt" || echo 'Python dependency rollback needs manual recovery; see pip-freeze.txt.' >&2; systemctl daemon-reload; nginx -t && systemctl reload nginx || true; systemctl start meshcore-weather-dispatch meshcore-weather-dispatch-update.socket || true; }
trap rollback ERR
if ! command -v nginx >/dev/null; then apt-get update; apt-get install -y nginx; if [[ -e /etc/nginx/sites-enabled/default ]]; then target=$(readlink -f /etc/nginx/sites-enabled/default || true); [[ "$target" == /etc/nginx/sites-available/default ]] && grep -q 'Default server configuration' "$target" || { echo 'Existing Nginx site found; migration refused.' >&2; exit 1; }; rm /etc/nginx/sites-enabled/default; fi; fi
systemctl stop meshcore-weather-dispatch
runuser -u weatherdispatch -- git -C "$APP" checkout "$UPDATE_BRANCH"; runuser -u weatherdispatch -- git -C "$APP" pull --ff-only origin "$UPDATE_BRANCH"
runuser -u weatherdispatch -- sh -c 'cd "$1" && .venv/bin/pip install --upgrade -e .' sh "$APP"
if [[ ${WEB_UPDATE:-0} != 1 ]]; then
 install -m 644 "$APP/systemd/meshcore-weather-dispatch.service" /etc/systemd/system/meshcore-weather-dispatch.service
 install -m 644 "$APP/systemd/meshcore-weather-dispatch-update.socket" /etc/systemd/system/meshcore-weather-dispatch-update.socket
 install -m 644 "$APP/systemd/meshcore-weather-dispatch-update@.service" /etc/systemd/system/meshcore-weather-dispatch-update@.service
 install -d -m 755 /usr/local/libexec
 install -o root -g root -m 755 "$SELF" /usr/local/libexec/meshcore-weather-dispatch-update
 install -m 755 "$APP/scripts/systemd-update-worker.sh" /usr/local/libexec/meshcore-weather-dispatch-update-worker
 rm -f /etc/sudoers.d/meshcore-weather-dispatch-web-updates
fi
[[ -e /etc/nginx/sites-enabled/meshcore-weather-dispatch && ! -L /etc/nginx/sites-enabled/meshcore-weather-dispatch ]] && { echo 'Managed Nginx link replaced; refusing overwrite.' >&2; exit 1; }
install -m 644 "$APP/nginx/meshcore-weather-dispatch.conf" /etc/nginx/sites-available/meshcore-weather-dispatch; [[ -e /etc/nginx/sites-enabled/meshcore-weather-dispatch ]] || ln -s /etc/nginx/sites-available/meshcore-weather-dispatch /etc/nginx/sites-enabled/meshcore-weather-dispatch
nginx -t; systemctl daemon-reload; systemctl reload nginx
if [[ ${WEB_UPDATE:-0} != 1 ]]; then systemctl enable --now meshcore-weather-dispatch-update.socket; fi
systemctl start meshcore-weather-dispatch
for attempt in $(seq 1 15); do curl --fail --max-time 2 -I http://127.0.0.1/ >/dev/null 2>&1 && break; sleep 1; done
curl --fail --max-time 5 -I http://127.0.0.1/ >/dev/null
trap - ERR; echo "Updated successfully. Backup: $BACKUP"
