#!/usr/bin/env bash
set -euo pipefail
[[ ${EUID} -eq 0 ]] || { echo 'Run with sudo.' >&2; exit 1; }
APP=/opt/meshcore-weather-dispatch; ETC=/etc/meshcore-weather-dispatch; STATE=/var/lib/meshcore-weather-dispatch; DB=$STATE/dispatch.sqlite3; BACKUP=/var/backups/meshcore-weather-dispatch/$(date -u +%Y%m%dT%H%M%SZ)
[[ -d "$APP/.git" ]] || { echo 'Not an updateable Git checkout; refusing update.' >&2; exit 1; }
runuser -u weatherdispatch -- git -C "$APP" remote get-url origin >/dev/null; runuser -u weatherdispatch -- git -C "$APP" diff --quiet || { echo 'Local application edits detected; refusing update.' >&2; exit 1; }
runuser -u weatherdispatch -- git -C "$APP" fetch --prune origin; OLD=$(runuser -u weatherdispatch -- git -C "$APP" rev-parse HEAD)
install -d -m 700 "$BACKUP"
[[ -f "$DB" ]] && python3 - "$DB" "$BACKUP/dispatch.sqlite3" <<'PY'
import sqlite3,sys
s,t=sys.argv[1:]; a=sqlite3.connect(s); b=sqlite3.connect(t)
try: a.backup(b)
finally: b.close(); a.close()
PY
for f in "$ETC/config.yml" "$ETC/environment" /etc/systemd/system/meshcore-weather-dispatch.service /etc/nginx/sites-available/meshcore-weather-dispatch; do [[ -f "$f" ]] && cp -a "$f" "$BACKUP/$(basename "$f")"; done
rollback(){ echo "Update failed; restoring $OLD. Backups: $BACKUP" >&2; runuser -u weatherdispatch -- git -C "$APP" checkout -q "$OLD" || true; systemctl daemon-reload; systemctl start meshcore-weather-dispatch || true; }
trap rollback ERR
systemctl stop meshcore-weather-dispatch
runuser -u weatherdispatch -- git -C "$APP" checkout main; runuser -u weatherdispatch -- git -C "$APP" pull --ff-only origin main
runuser -u weatherdispatch -- sh -c 'cd "$1" && .venv/bin/pip install --upgrade -e .' sh "$APP"
install -m 644 "$APP/systemd/meshcore-weather-dispatch.service" /etc/systemd/system/meshcore-weather-dispatch.service
[[ -e /etc/nginx/sites-enabled/meshcore-weather-dispatch && ! -L /etc/nginx/sites-enabled/meshcore-weather-dispatch ]] && { echo 'Managed Nginx link replaced; refusing overwrite.' >&2; exit 1; }
install -m 644 "$APP/nginx/meshcore-weather-dispatch.conf" /etc/nginx/sites-available/meshcore-weather-dispatch; [[ -e /etc/nginx/sites-enabled/meshcore-weather-dispatch ]] || ln -s /etc/nginx/sites-available/meshcore-weather-dispatch /etc/nginx/sites-enabled/meshcore-weather-dispatch
nginx -t; systemctl daemon-reload; systemctl reload nginx; systemctl start meshcore-weather-dispatch; curl --fail --max-time 15 -I http://127.0.0.1/ >/dev/null
trap - ERR; echo "Updated successfully. Backup: $BACKUP"
