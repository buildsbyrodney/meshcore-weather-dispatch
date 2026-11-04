#!/usr/bin/env bash
set -eu
[[ $# -eq 0 && ${EUID} -eq 0 ]] || exit 64
nohup sh -c 'sleep 2; systemctl reboot' >/dev/null 2>&1 &
