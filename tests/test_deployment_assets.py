from pathlib import Path
import subprocess

ROOT=Path(__file__).parents[1]

def test_nginx_keeps_gunicorn_loopback_only_and_forwards_required_headers():
    config=(ROOT/"nginx"/"meshcore-weather-dispatch.conf").read_text()
    service=(ROOT/"systemd"/"meshcore-weather-dispatch.service").read_text()
    assert "listen 80 default_server" in config and "proxy_pass http://127.0.0.1:8080" in config
    assert "X-Forwarded-Proto" in config and "client_max_body_size 64k" in config
    assert "--workers 1" in service and "--bind 127.0.0.1:8080" in service and "--worker-tmp-dir /dev/shm" in service and "--no-control-socket" in service and "nginx.service" in service

def test_install_and_update_scripts_keep_port_80_and_existing_sites_safe():
    install=(ROOT/"scripts"/"install.sh").read_text(); update=(ROOT/"scripts"/"update.sh").read_text()
    assert "Port 80 is already in use" in install and "Existing Nginx site found at" in install
    assert "for site in /etc/nginx/sites-enabled/*" in install and "shadow it" in install
    assert "APP_BRANCH" in install and "git clone --branch" in install
    assert 'install -d -o weatherdispatch -g weatherdispatch -m 750 "$APP"' in install
    assert "runuser -u weatherdispatch -- sh -c 'cd /tmp && exec git clone" in install
    assert "safe.directory" not in install
    assert "An incomplete initial installation was found" in install
    assert "nginx -t" in install and "curl --fail" in install
    assert "nginx -t" in update and "pull --ff-only" in update and "UPDATE_BRANCH" in update
    assert "sqlite3.connect" in update and "rollback(){" in update and "pip-freeze.txt" in update
    assert "Recognized legacy Weather Dispatch unit" in update and "Service unit is unknown or customized" in update
    assert "Port 80 is occupied; cannot safely add Nginx" in update
    modes=subprocess.check_output(["git","ls-files","-s","scripts/install.sh","scripts/update.sh"],cwd=ROOT,text=True)
    assert all(line.startswith("100755 ") for line in modes.splitlines())
