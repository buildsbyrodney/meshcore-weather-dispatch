# Changelog

All notable changes are documented here. This project follows semantic versioning; pre-release versions use the `-rc.N` suffix in public release names.

## v1.0.0-rc.1 — 2026-11-02

First public release candidate.

- Human-approved NWS alert dispatch with no automatic MeshCore transmissions.
- Authenticated web administration for NWS polling, county coverage, Companion selection, channels, device name, time zone, and manual-transmission permission.
- Nginx on port 80, loopback-only Gunicorn, systemd service management, protected secrets, SQLite backups, and guarded upgrades/legacy migration.
- Test Alerts, Test Messaging, signed dispatch previews, explicit confirmation, UTF-8 message limits, duplicate prevention, and audit history.

## Before v1.0.0-rc.1

Development history is represented by the Git history and pull requests.
