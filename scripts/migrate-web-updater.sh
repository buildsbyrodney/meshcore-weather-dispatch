#!/usr/bin/env bash
# Run from a trusted, root-owned temporary checkout.  update.sh creates its
# normal backup before it changes the live checkout or virtual environment.
set -euo pipefail
[[ ${EUID} -eq 0 ]] || { echo 'Run this migration with sudo.' >&2; exit 1; }
APP=/opt/meshcore-weather-dispatch
[[ -d "$APP/.git" ]] || { echo "Expected managed checkout at $APP; refusing migration." >&2; exit 1; }
id weatherdispatch >/dev/null 2>&1 || { echo 'weatherdispatch service account is missing; refusing migration.' >&2; exit 1; }
echo 'Starting guarded updater migration. A timestamped backup will be created before the live checkout changes; the legacy privileged helper will then be replaced from this staged checkout.'
exec "$(dirname "$0")/update.sh"
