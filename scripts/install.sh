#!/usr/bin/env bash
set -euo pipefail
[[ ${EUID} -eq 0 ]] || { echo 'Run with sudo.' >&2; exit 1; }
ROOT=$(cd "$(dirname "$0")/.." && pwd); APP=/opt/meshcore-weather-dispatch; ETC=/etc/meshcore-weather-dispatch; STATE=/var/lib/meshcore-weather-dispatch
ss -ltnH '( sport = :80 )' | grep -q . && { echo 'Port 80 is already in use; nothing was changed.' >&2; exit 1; }
apt-get update; apt-get install -y nginx python3-venv python3-pip curl
if [[ -e /etc/nginx/sites-enabled/default ]]; then
  target=$(readlink -f /etc/nginx/sites-enabled/default || true)
  [[ "$target" == /etc/nginx/sites-available/default ]] && grep -q 'Default server configuration' "$target" || { echo 'Existing Nginx site found; refusing to replace it.' >&2; exit 1; }
  rm /etc/nginx/sites-enabled/default
fi
id weatherdispatch >/dev/null 2>&1 || adduser --system --group --home "$APP" weatherdispatch
install -d -o weatherdispatch -g weatherdispatch -m 750 "$ETC" "$STATE"; install -d -o weatherdispatch -g weatherdispatch -m 755 "$APP"
cp -a "$ROOT"/. "$APP"/; chown -R weatherdispatch:weatherdispatch "$APP"
sudo -u weatherdispatch -H python3 -m venv "$APP/.venv"; sudo -u weatherdispatch -H "$APP/.venv/bin/pip" install --upgrade -e "$APP"
read -rp 'Administrator username [weatheradmin]: ' USER; USER=${USER:-weatheradmin}; read -rp 'NWS contact email: ' EMAIL; read -rp 'NWS states (e.g. AL,TN): ' STATES; read -rsp 'Initial administrator password: ' PASS; echo
printf '%s\n' "$PASS" | "$APP/.venv/bin/python" -m dispatch.bootstrap --config "$ETC/config.yml" --environment "$ETC/environment" --database "$STATE/dispatch.sqlite3" --username "$USER" --email "$EMAIL" --states "$STATES" --password-stdin; unset PASS
chown weatherdispatch:weatherdispatch "$ETC/config.yml" "$ETC/environment" "$STATE/dispatch.sqlite3"
install -m 644 "$APP/systemd/meshcore-weather-dispatch.service" /etc/systemd/system/meshcore-weather-dispatch.service
install -m 644 "$APP/nginx/meshcore-weather-dispatch.conf" /etc/nginx/sites-available/meshcore-weather-dispatch
ln -s /etc/nginx/sites-available/meshcore-weather-dispatch /etc/nginx/sites-enabled/meshcore-weather-dispatch
nginx -t; systemctl daemon-reload; systemctl enable --now nginx meshcore-weather-dispatch
curl --fail --max-time 10 -I http://127.0.0.1/ >/dev/null; echo "Installed: http://$(hostname -I | awk '{print $1}')/"
