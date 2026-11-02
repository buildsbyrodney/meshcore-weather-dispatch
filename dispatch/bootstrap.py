"""One-time production initializer used by the package installer."""
from __future__ import annotations
import argparse, os, secrets, sys
from pathlib import Path
import yaml
from werkzeug.security import generate_password_hash
from .db import Database
from .runtime import contact_email, states

SERVICE_DEFAULTS={"database":"/var/lib/meshcore-weather-dispatch/dispatch.sqlite3","host":"127.0.0.1","port":8080,"secret_key_env":"WEATHER_DISPATCH_SECRET_KEY","poll_seconds":60,"timezone":"UTC","start_poller":True,"session_cookie_secure":True,"alert_types":["Tornado Warning","Severe Thunderstorm Warning","Flash Flood Warning"],"max_message_bytes":180,"dry_run":True,"device":None}

def _write_private(path: Path, text: str):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text,encoding="utf-8");os.chmod(path,0o600)

def initialize(config_path, environment_path, database, username, password, email, selected_states):
    if len(username)<3 or len(username)>64 or not username.replace("_","").replace("-","").isalnum(): raise ValueError("username must be 3–64 letters, digits, hyphens, or underscores")
    if len(password)<12: raise ValueError("password must be at least 12 characters")
    email=contact_email(email);selected_states=states(selected_states)
    config_path,environment_path,database=Path(config_path),Path(environment_path),Path(database)
    if not config_path.exists():
        config=dict(SERVICE_DEFAULTS);config["database"]=str(database);_write_private(config_path,yaml.safe_dump(config,sort_keys=False))
    else: os.chmod(config_path,0o600)
    existing=environment_path.read_text(encoding="utf-8") if environment_path.exists() else ""
    if "WEATHER_DISPATCH_SECRET_KEY=" not in existing:
        existing+=("\n" if existing and not existing.endswith("\n") else "")+f"WEATHER_DISPATCH_SECRET_KEY={secrets.token_urlsafe(48)}\n"
    if "MESHCORE_SEND_ENABLED=" not in existing: existing+="MESHCORE_SEND_ENABLED=0\n"
    _write_private(environment_path,existing)
    database.parent.mkdir(parents=True,exist_ok=True)
    db=Database(str(database));db.initialize();db.save_settings({"auth_username":username,"auth_password_hash":generate_password_hash(password),"nws_contact_email":email,"nws_states":__import__("json").dumps(selected_states),"nws_poll_seconds":"60","transmit_enabled":"false"})

def main():
    parser=argparse.ArgumentParser(description="Initialize MeshCore Weather Dispatch production state")
    parser.add_argument("--config",required=True);parser.add_argument("--environment",required=True);parser.add_argument("--database",required=True);parser.add_argument("--username",required=True);parser.add_argument("--email",required=True);parser.add_argument("--states",required=True);parser.add_argument("--password-stdin",action="store_true")
    args=parser.parse_args();password=sys.stdin.readline().rstrip("\r\n") if args.password_stdin else ""
    initialize(args.config,args.environment,args.database,args.username,password,args.email,args.states)
    print("Initialized protected service files and administrator settings. The secret was not displayed.")
if __name__=="__main__": main()
