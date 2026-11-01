"""Application factory for the manual MeshCore weather dispatcher."""
from __future__ import annotations
import os
from pathlib import Path
import yaml
from flask import Flask
from flask_wtf.csrf import CSRFProtect
from .db import Database
from .routes import bp

csrf = CSRFProtect()

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
    app.config.update(config, SECRET_KEY=config["secret_key"], WTF_CSRF_TIME_LIMIT=3600)
    app.extensions["db"] = Database(app.config["database"])
    app.extensions["db"].initialize()
    csrf.init_app(app)
    app.register_blueprint(bp)
    return app
