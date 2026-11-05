#!/usr/bin/env bash
set -euo pipefail
[[ ${EUID} -eq 0 ]] || { echo 'Run with sudo.' >&2; exit 1; }
APP=/opt/meshcore-weather-dispatch; ETC=/etc/meshcore-weather-dispatch; STATE=/var/lib/meshcore-weather-dispatch
REPO_URL=${REPO_URL:-https://github.com/buildsbyrodney/meshcore-weather-dispatch.git}
APP_BRANCH=${APP_BRANCH:-main}
if [[ -e "$APP" ]]; then
 if [[ -e "$ETC/environment" || -e "$STATE/dispatch.sqlite3" ]]; then
  echo "$APP already exists with application state; use scripts/update.sh instead." >&2
 else
  cat >&2 <<EOF
An incomplete initial installation was found at $APP. No files were changed.
Inspect it before removal. If this is only the interrupted checkout and neither
$ETC/environment nor $STATE/dispatch.sqlite3 exists, remove that checkout with:
  sudo test ! -e $ETC/environment && sudo test ! -e $STATE/dispatch.sqlite3 && sudo rm -rf $APP
Then rerun this installer from a fresh source checkout.
EOF
 fi
 exit 1
fi
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
install -d -o weatherdispatch -g weatherdispatch -m 750 "$APP" "$ETC" "$STATE"
runuser -u weatherdispatch -- sh -c 'cd /tmp && exec git clone --branch "$1" --single-branch "$2" "$3"' sh "$APP_BRANCH" "$REPO_URL" "$APP"
runuser -u weatherdispatch -- sh -c 'cd /tmp && exec git -C "$1" remote get-url origin' sh "$APP" >/dev/null
runuser -u weatherdispatch -- sh -c 'cd "$1" && python3 -m venv .venv && .venv/bin/pip install --upgrade -e .' sh "$APP"
read -rp 'Administrator username [weatheradmin]: ' USER; USER=${USER:-weatheradmin}; read -rp 'NWS contact email: ' EMAIL; read -rp 'NWS states (e.g. AL,TN): ' STATES
while :; do
 read -rsp 'Initial administrator password (at least 6 characters): ' PASS; echo
 read -rsp 'Confirm initial administrator password: ' CONFIRM; echo
 if [[ "$PASS" != "$CONFIRM" ]]; then echo 'Passwords did not match. Please try again.' >&2; unset PASS CONFIRM; continue; fi
 if (( ${#PASS} < 6 )); then echo 'Password must be at least 6 characters. Please try again.' >&2; unset PASS CONFIRM; continue; fi
 break
done
printf '%s\n' "$PASS" | "$APP/.venv/bin/python" -m dispatch.bootstrap --config "$ETC/config.yml" --environment "$ETC/environment" --database "$STATE/dispatch.sqlite3" --username "$USER" --email "$EMAIL" --states "$STATES" --password-stdin; unset PASS CONFIRM
chown weatherdispatch:weatherdispatch "$ETC/config.yml" "$ETC/environment" "$STATE/dispatch.sqlite3"
install -d -m 755 /usr/local/libexec
# The web service talks only to the socket below.  It has no sudoers entry and
# never executes a root-owned command directly.
install -m 755 "$APP/scripts/update.sh" /usr/local/libexec/meshcore-weather-dispatch-update
install -m 755 "$APP/scripts/systemd-update-worker.sh" /usr/local/libexec/meshcore-weather-dispatch-update-worker
rm -f /etc/sudoers.d/meshcore-weather-dispatch-web-updates
install -m 644 "$APP/systemd/meshcore-weather-dispatch.service" /etc/systemd/system/meshcore-weather-dispatch.service
install -m 644 "$APP/systemd/meshcore-weather-dispatch-update.socket" /etc/systemd/system/meshcore-weather-dispatch-update.socket
install -m 644 "$APP/systemd/meshcore-weather-dispatch-update@.service" /etc/systemd/system/meshcore-weather-dispatch-update@.service
install -m 644 "$APP/nginx/meshcore-weather-dispatch.conf" /etc/nginx/sites-available/meshcore-weather-dispatch; ln -s /etc/nginx/sites-available/meshcore-weather-dispatch /etc/nginx/sites-enabled/meshcore-weather-dispatch
nginx -t; systemctl daemon-reload; systemctl enable --now nginx meshcore-weather-dispatch meshcore-weather-dispatch-update.socket
for attempt in $(seq 1 15); do curl --fail --max-time 2 -I http://127.0.0.1/ >/dev/null 2>&1 && break; sleep 1; done
curl --fail --max-time 5 -I http://127.0.0.1/ >/dev/null
echo "Installed: http://$(hostname -I | awk '{print $1}')/"
