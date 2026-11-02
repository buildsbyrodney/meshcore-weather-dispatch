#!/usr/bin/env bash
set -euo pipefail
[[ ${EUID} -eq 0 ]] || { echo 'Run with sudo.' >&2; exit 1; }
APP=/opt/meshcore-weather-dispatch; ETC=/etc/meshcore-weather-dispatch; STATE=/var/lib/meshcore-weather-dispatch; DB=$STATE/dispatch.sqlite3; BACKUP=/var/backups/meshcore-weather-dispatch/$(date -u +%Y%m%dT%H%M%SZ)
UPDATE_BRANCH=${UPDATE_BRANCH:-main}
[[ -d "$APP/.git" ]] || { echo 'Not an updateable Git checkout; refusing update.' >&2; exit 1; }
runuser -u weatherdispatch -- git -C "$APP" remote get-url origin >/dev/null; runuser -u weatherdispatch -- git -C "$APP" diff --quiet || { echo 'Local application edits detected; refusing update.' >&2; exit 1; }
grep -q 'Managed by MeshCore Weather Dispatch' /etc/systemd/system/meshcore-weather-dispatch.service || { echo 'Service file is customized; use a systemd .d override and update it manually.' >&2; exit 1; }
[[ ! -f /etc/nginx/sites-available/meshcore-weather-dispatch ]] || grep -q 'Managed by MeshCore Weather Dispatch' /etc/nginx/sites-available/meshcore-weather-dispatch || { echo 'Nginx site is customized; refusing overwrite.' >&2; exit 1; }
runuser -u weatherdispatch -- git -C "$APP" fetch --prune origin; OLD=$(runuser -u weatherdispatch -- git -C "$APP" rev-parse HEAD)
install -d -m 700 "$BACKUP" "$BACKUP/etc" "$BACKUP/systemd" "$BACKUP/nginx"
[[ -f "$DB" ]] && python3 - "$DB" "$BACKUP/dispatch.sqlite3" <<'PY'
import sqlite3,sys
s,t=sys.argv[1:]; a=sqlite3.connect(s); b=sqlite3.connect(t)
try: a.backup(b)
finally: b.close(); a.close()
PY
for f in "$ETC/config.yml" "$ETC/environment"; do [[ -f "$f" ]] && cp -a "$f" "$BACKUP/etc/$(basename "$f")"; done
cp -a /etc/systemd/system/meshcore-weather-dispatch.service "$BACKUP/systemd/service"; [[ -f /etc/nginx/sites-available/meshcore-weather-dispatch ]] && cp -a /etc/nginx/sites-available/meshcore-weather-dispatch "$BACKUP/nginx/site"
runuser -u weatherdispatch -- sh -c 'cd "$1" && .venv/bin/pip freeze' sh "$APP" > "$BACKUP/pip-freeze.txt"
rollback(){ echo "Update failed; restoring $OLD. Backups: $BACKUP" >&2; runuser -u weatherdispatch -- git -C "$APP" checkout -q "$OLD" || true; cp -a "$BACKUP/systemd/service" /etc/systemd/system/meshcore-weather-dispatch.service; [[ -f "$BACKUP/nginx/site" ]] && cp -a "$BACKUP/nginx/site" /etc/nginx/sites-available/meshcore-weather-dispatch; runuser -u weatherdispatch -- sh -c 'cd "$1" && .venv/bin/pip install -r "$2" && .venv/bin/pip install -e .' sh "$APP" "$BACKUP/pip-freeze.txt" || echo 'Python dependency rollback needs manual recovery; see pip-freeze.txt.' >&2; systemctl daemon-reload; nginx -t && systemctl reload nginx || true; systemctl start meshcore-weather-dispatch || true; }
trap rollback ERR
systemctl stop meshcore-weather-dispatch
runuser -u weatherdispatch -- git -C "$APP" checkout "$UPDATE_BRANCH"; runuser -u weatherdispatch -- git -C "$APP" pull --ff-only origin "$UPDATE_BRANCH"
runuser -u weatherdispatch -- sh -c 'cd "$1" && .venv/bin/pip install --upgrade -e .' sh "$APP"
install -m 644 "$APP/systemd/meshcore-weather-dispatch.service" /etc/systemd/system/meshcore-weather-dispatch.service
[[ -e /etc/nginx/sites-enabled/meshcore-weather-dispatch && ! -L /etc/nginx/sites-enabled/meshcore-weather-dispatch ]] && { echo 'Managed Nginx link replaced; refusing overwrite.' >&2; exit 1; }
install -m 644 "$APP/nginx/meshcore-weather-dispatch.conf" /etc/nginx/sites-available/meshcore-weather-dispatch; [[ -e /etc/nginx/sites-enabled/meshcore-weather-dispatch ]] || ln -s /etc/nginx/sites-available/meshcore-weather-dispatch /etc/nginx/sites-enabled/meshcore-weather-dispatch
nginx -t; systemctl daemon-reload; systemctl reload nginx; systemctl start meshcore-weather-dispatch
for attempt in $(seq 1 15); do curl --fail --max-time 2 -I http://127.0.0.1/ >/dev/null 2>&1 && break; sleep 1; done
curl --fail --max-time 5 -I http://127.0.0.1/ >/dev/null
trap - ERR; echo "Updated successfully. Backup: $BACKUP"
