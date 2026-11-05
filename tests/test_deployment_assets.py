from pathlib import Path
import subprocess

ROOT=Path(__file__).parents[1]

def test_nginx_keeps_gunicorn_loopback_only_and_forwards_required_headers():
    config=(ROOT/"nginx"/"meshcore-weather-dispatch.conf").read_text()
    service=(ROOT/"systemd"/"meshcore-weather-dispatch.service").read_text()
    assert "listen 80 default_server" in config and "proxy_pass http://127.0.0.1:8080" in config
    assert "X-Forwarded-Proto" in config and "client_max_body_size 64k" in config
    assert "--workers 1" in service and "--bind 127.0.0.1:8080" in service and "--worker-tmp-dir /dev/shm" in service and "--no-control-socket" in service and "nginx.service" in service
    assert "NoNewPrivileges=true" in service and "User=weatherdispatch" in service

def test_web_updates_use_a_fixed_socket_activated_root_worker_without_sudo():
    socket=(ROOT/"systemd"/"meshcore-weather-dispatch-update.socket").read_text()
    worker=(ROOT/"systemd"/"meshcore-weather-dispatch-update@.service").read_text()
    script=(ROOT/"scripts"/"systemd-update-worker.sh").read_text()
    install=(ROOT/"scripts"/"install.sh").read_text(); update=(ROOT/"scripts"/"update.sh").read_text()
    assert "SocketUser=weatherdispatch" in socket and "SocketMode=0660" in socket and "Accept=yes" in socket
    assert "User=root" in worker and "StandardInput=socket" in worker and "StandardOutput=socket" not in worker
    assert 'case "$request" in update|reboot)' in script and "flock -n" in script
    assert "WEB_UPDATE=1" in script and "${WEB_UPDATE:-0} != 1" in update
    assert 'install -o root -g root -m 755 "$SELF" /usr/local/libexec/meshcore-weather-dispatch-update' in update
    assert "systemctl stop meshcore-weather-dispatch" in update
    assert "NOPASSWD" not in install
    assert "NOPASSWD" not in update
    assert "meshcore-weather-dispatch-update.socket" in install and "meshcore-weather-dispatch-update.socket" in update

def test_staged_migration_checks_the_existing_installation_before_delegating_to_the_backed_up_updater():
    migration=(ROOT/"scripts"/"migrate-web-updater.sh").read_text()
    assert 'APP=/opt/meshcore-weather-dispatch' in migration
    assert '[[ -d "$APP/.git" ]]' in migration and 'id weatherdispatch' in migration
    assert 'exec "$(dirname "$0")/update.sh"' in migration

def test_install_and_update_scripts_keep_port_80_and_existing_sites_safe():
    install=(ROOT/"scripts"/"install.sh").read_text(); update=(ROOT/"scripts"/"update.sh").read_text()
    assert "Port 80 is already in use" in install and "Existing Nginx site found at" in install
    assert "for site in /etc/nginx/sites-enabled/*" in install and "shadow it" in install
    assert "APP_BRANCH" in install and "git clone --branch" in install
    assert 'install -d -o weatherdispatch -g weatherdispatch -m 750 "$APP"' in install
    assert "runuser -u weatherdispatch -- sh -c 'cd /tmp && exec git clone" in install
    assert "safe.directory" not in install
    assert "An incomplete initial installation was found" in install
    assert "Confirm initial administrator password" in install
    assert "Passwords did not match. Please try again." in install
    assert "at least 6 characters" in install
    assert "nginx -t" in install and "curl --fail" in install
    assert "nginx -t" in update and "pull --ff-only" in update and "UPDATE_BRANCH" in update
    assert "sqlite3.connect" in update and "rollback(){" in update and "pip-freeze.txt" in update
    assert 'pip install -r /dev/stdin' in update
    assert '< "$BACKUP/pip-freeze.txt"' in update
    assert "Recognized legacy Weather Dispatch unit" in update and "Service unit is unknown or customized" in update
    assert "Port 80 is occupied; cannot safely add Nginx" in update
    modes=subprocess.check_output(["git","ls-files","-s","scripts/install.sh","scripts/update.sh"],cwd=ROOT,text=True)
    assert all(line.startswith("100755 ") for line in modes.splitlines())
