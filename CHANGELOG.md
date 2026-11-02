# Changelog

All notable changes are documented here. This project follows semantic versioning; pre-release versions use the `-rc.N` suffix in public release names.

## v1.0.0-rc.2 — 2026-11-02

Second public release candidate, verified through a clean Raspberry Pi OS 64-bit installation with a USB MeshCore Companion.

- Corrected fresh-install Git checkout ownership: the `weatherdispatch` account owns and clones the managed checkout without weakening Git ownership protection; installer and updater scripts are executable in Git.
- Simplified local administrator setup to a confirmed six-character minimum password while retaining secure hashes, authenticated sessions, CSRF protection, and audit identity.
- Documented the required USB serial permission (`dialout`) and reboot after installation, including recovery guidance for Companion permission errors and Nginx's default welcome page.
- Preserved manual-only transmission, signed preview confirmation, selected-channel validation, duplicate protection, and audit history.

## v1.0.0-rc.1 — 2026-11-02

First public release candidate.

- Human-approved NWS alert dispatch with no automatic MeshCore transmissions.
- Authenticated web administration for NWS polling, county coverage, Companion selection, channels, device name, time zone, and manual-transmission permission.
- Nginx on port 80, loopback-only Gunicorn, systemd service management, protected secrets, SQLite backups, and guarded upgrades/legacy migration.
- Test Alerts, Test Messaging, signed dispatch previews, explicit confirmation, UTF-8 message limits, duplicate prevention, and audit history.

## Before v1.0.0-rc.1

Development history is represented by the Git history and pull requests.
