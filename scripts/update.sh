#!/usr/bin/env bash
set -euo pipefail
[[ ${EUID} -eq 0 ]] || { echo 'Run with sudo.' >&2; exit 1; }
APP=/opt/meshcore-weather-dispatch
systemctl stop meshcore-weather-dispatch
sudo -u weatherdispatch -H git -C "$APP" fetch --prune origin; sudo -u weatherdispatch -H git -C "$APP" checkout main; sudo -u weatherdispatch -H git -C "$APP" pull --ff-only origin main
sudo -u weatherdispatch -H "$APP/.venv/bin/pip" install --upgrade -e "$APP"
install -m 644 "$APP/systemd/meshcore-weather-dispatch.service" /etc/systemd/system/meshcore-weather-dispatch.service
[[ -e /etc/nginx/sites-enabled/meshcore-weather-dispatch ]] || { install -m 644 "$APP/nginx/meshcore-weather-dispatch.conf" /etc/nginx/sites-available/meshcore-weather-dispatch; ln -s /etc/nginx/sites-available/meshcore-weather-dispatch /etc/nginx/sites-enabled/meshcore-weather-dispatch; }
nginx -t; systemctl daemon-reload; systemctl reload nginx; systemctl start meshcore-weather-dispatch; curl --fail --max-time 10 -I http://127.0.0.1/ >/dev/null
