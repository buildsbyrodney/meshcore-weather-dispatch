#!/usr/bin/env bash
set -euo pipefail
STATE=/var/lib/meshcore-weather-dispatch/web-updates
LOG=$STATE/update.log; STATUS=$STATE/status.json; LOCK=$STATE/run.lock
install -d -o root -g weatherdispatch -m 750 "$STATE"
write_status(){ umask 027; printf '{"status":"%s","message":"%s"}\n' "$1" "$2" > "$STATUS.tmp"; chown root:weatherdispatch "$STATUS.tmp"; chmod 640 "$STATUS.tmp"; mv -f "$STATUS.tmp" "$STATUS"; }
IFS= read -r request || request=""
case "$request" in update|reboot) ;; *) printf 'rejected\n'; exit 0;; esac
exec 9>"$LOCK"
if ! flock -n 9; then exit 0; fi
if [[ "$request" == reboot ]]; then
 write_status running "Reboot requested"; printf 'Reboot requested by the web console.\n' > "$LOG"; chown root:weatherdispatch "$LOG"; chmod 640 "$LOG"; systemd-run --unit=meshcore-weather-dispatch-reboot --on-active=2s /usr/bin/systemctl reboot; write_status success "Reboot scheduled"; exit 0
fi
write_status running "Update running"; : > "$LOG"; chown root:weatherdispatch "$LOG"; chmod 640 "$LOG"
if /usr/local/libexec/meshcore-weather-dispatch-update >> "$LOG" 2>&1; then write_status success "Update completed"; else write_status failed "Update failed; see console"; fi
