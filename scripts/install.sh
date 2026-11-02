#!/usr/bin/env bash
set -euo pipefail
[[ ${EUID} -eq 0 ]] || { echo 'Run with sudo.' >&2; exit 1; }
APP=/opt/meshcore-weather-dispatch; ETC=/etc/meshcore-weather-dispatch; STATE=/var/lib/meshcore-weather-dispatch
REPO_URL=${REPO_URL:-https://github.com/buildsbyrodney/meshcore-weather-dispatch.git}
APP_BRANCH=${APP_BRANCH:-main}
[[ ! -e "$APP" ]] || { echo "$APP already exists; use scripts/update.sh instead." >&2; exit 1; }
ss -ltnH '( sport = :80 )' | grep -q . && { echo 'Port 80 is already in use; nothing was changed.' >&2; exit 1; }
apt-get update; apt-get install -y nginx git python3-venv python3-pip curl
# On Debian-family systems, do not turn this host into the default site while
# another enabled virtual host exists.  This remains safe even when Nginx was
# installed but stopped, so a port scan alone is not sufficient.
for site in /etc/nginx/sites-enabled/*; do
 [[ -e "$site" ]] || continue
 target=$(readlink -f "$site" || true)
 if [[ "$target" == /etc/nginx/sites-available/default ]] && grep -q 'Default server configuration' "$target"; then
  rm "$site"
 else
  echo "Existing Nginx site found at $site; refusing to replace or shadow it." >&2
  exit 1
 fi
done
id weatherdispatch >/dev/null 2>&1 || adduser --system --group --home "$APP" weatherdispatch
install -d -o weatherdispatch -g weatherdispatch -m 750 "$ETC" "$STATE"
git clone --branch "$APP_BRANCH" --single-branch "$REPO_URL" "$APP"; git -C "$APP" remote get-url origin >/dev/null; chown -R weatherdispatch:weatherdispatch "$APP"
runuser -u weatherdispatch -- sh -c 'cd "$1" && python3 -m venv .venv && .venv/bin/pip install --upgrade -e .' sh "$APP"
read -rp 'Administrator username [weatheradmin]: ' USER; USER=${USER:-weatheradmin}; read -rp 'NWS contact email: ' EMAIL; read -rp 'NWS states (e.g. AL,TN): ' STATES; read -rsp 'Initial administrator password: ' PASS; echo
printf '%s\n' "$PASS" | "$APP/.venv/bin/python" -m dispatch.bootstrap --config "$ETC/config.yml" --environment "$ETC/environment" --database "$STATE/dispatch.sqlite3" --username "$USER" --email "$EMAIL" --states "$STATES" --password-stdin; unset PASS
chown weatherdispatch:weatherdispatch "$ETC/config.yml" "$ETC/environment" "$STATE/dispatch.sqlite3"
install -m 644 "$APP/systemd/meshcore-weather-dispatch.service" /etc/systemd/system/meshcore-weather-dispatch.service; install -m 644 "$APP/nginx/meshcore-weather-dispatch.conf" /etc/nginx/sites-available/meshcore-weather-dispatch; ln -s /etc/nginx/sites-available/meshcore-weather-dispatch /etc/nginx/sites-enabled/meshcore-weather-dispatch
nginx -t; systemctl daemon-reload; systemctl enable --now nginx meshcore-weather-dispatch
for attempt in $(seq 1 15); do curl --fail --max-time 2 -I http://127.0.0.1/ >/dev/null 2>&1 && break; sleep 1; done
curl --fail --max-time 5 -I http://127.0.0.1/ >/dev/null
echo "Installed: http://$(hostname -I | awk '{print $1}')/"
