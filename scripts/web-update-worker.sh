#!/usr/bin/env bash
set -eu
[[ $# -eq 0 && ${EUID} -eq 0 ]] || exit 64
STATE=/var/lib/meshcore-weather-dispatch/web-updates; APP=/opt/meshcore-weather-dispatch
install -d -o weatherdispatch -g weatherdispatch -m 750 "$STATE"
# mkdir is atomic.  It prevents two confirmation clicks from launching two
# independent updaters before either background process can write a PID file.
mkdir "$STATE/run.lock" 2>/dev/null || exit 75
nohup bash -c 'trap "rmdir \"$1/run.lock\"" EXIT; printf "{\"status\":\"running\",\"message\":\"Update running\"}\n" > "$1/status.json"; if bash "$2/scripts/update.sh" > "$1/update.log" 2>&1; then printf "{\"status\":\"success\",\"message\":\"Update completed\"}\n" > "$1/status.json"; else printf "{\"status\":\"failed\",\"message\":\"Update failed; see console\"}\n" > "$1/status.json"; fi' bash "$STATE" "$APP" >/dev/null 2>&1 &
