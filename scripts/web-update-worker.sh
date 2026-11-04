#!/usr/bin/env bash
set -eu
[[ $# -eq 0 && ${EUID} -eq 0 ]] || exit 64
STATE=/var/lib/meshcore-weather-dispatch/web-updates; APP=/opt/meshcore-weather-dispatch
install -d -o weatherdispatch -g weatherdispatch -m 750 "$STATE"
if [[ -e "$STATE/lock" ]] && kill -0 "$(cat "$STATE/lock" 2>/dev/null || echo 0)" 2>/dev/null; then exit 75; fi
nohup bash -c 'echo $$ > "$1/lock"; printf "{\"status\":\"running\",\"message\":\"Update running\"}\n" > "$1/status.json"; if bash "$2/scripts/update.sh" > "$1/update.log" 2>&1; then printf "{\"status\":\"success\",\"message\":\"Update completed\"}\n" > "$1/status.json"; else printf "{\"status\":\"failed\",\"message\":\"Update failed; see console\"}\n" > "$1/status.json"; fi; rm -f "$1/lock"' bash "$STATE" "$APP" >/dev/null 2>&1 &
