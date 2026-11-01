"""Application factory for the manual MeshCore weather dispatcher."""
from __future__ import annotations
import os
import threading
from pathlib import Path
import yaml
from flask import Flask
from flask_wtf.csrf import CSRFProtect
from .db import Database
from .routes import bp
from .timefmt import local_time, system_timezone

csrf = CSRFProtect()

def validate_config(config: dict) -> None:
    if not config.get("states"):
        raise ValueError("at least one NWS state must be configured; refusing to poll every U.S. alert")
    if int(config.get("poll_seconds", 60)) < 30:
        raise ValueError("poll_seconds must be at least 30 to respect NWS rate guidance")
    if int(config.get("max_message_bytes", 180)) < 1:
        raise ValueError("max_message_bytes must be positive")

def load_config(path: str | None = None) -> dict:
    path = path or os.environ.get("WEATHER_DISPATCH_CONFIG", "config.yml")
    with open(path, "r", encoding="utf-8") as fh:
        config = yaml.safe_load(fh) or {}
    env_name = config.get("secret_key_env", "WEATHER_DISPATCH_SECRET_KEY")
    secret = os.environ.get(env_name)
    if not secret:
        raise RuntimeError(f"required secret is missing from environment: {env_name}")
    config["secret_key"] = secret
    config["config_path"] = str(Path(path).absolute())
    return config

def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__)
    config = test_config or load_config()
    validate_config(config)
    app.config.update(config, SECRET_KEY=config["secret_key"], WTF_CSRF_TIME_LIMIT=3600,
                      SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                      SESSION_COOKIE_SECURE=bool(config.get("session_cookie_secure", False)))
    app.extensions["db"] = Database(app.config["database"])
    app.jinja_env.filters["localtime"] = lambda value: local_time(value, app.extensions["db"].setting("timezone") or app.config.get("timezone") or system_timezone())
    app.extensions["db"].initialize()
    csrf.init_app(app)
    app.register_blueprint(bp)
    @app.after_request
    def security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        return response
    # The production unit intentionally uses one Gunicorn worker. Starting here
    # keeps NWS polling active under both Gunicorn and `python -m dispatch`.
    if app.config.get("start_poller", True):
        from .poller import run_poller
        threading.Thread(target=run_poller, args=(app,), daemon=True, name="nws-poller").start()
    return app
