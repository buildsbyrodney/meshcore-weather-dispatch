"""SQLite-backed operational settings with service-config and emergency overrides."""
from __future__ import annotations
import json, os, re

EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
STATE = re.compile(r"^[A-Z]{2}$")

def contact_email(value: str) -> str:
    value=(value or "").strip()
    if not EMAIL.fullmatch(value) or len(value)>254: raise ValueError("enter a valid monitored contact email address")
    return value

def states(value) -> list[str]:
    values=value.split(",") if isinstance(value,str) else (value or [])
    result=sorted({str(item).strip().upper() for item in values if str(item).strip()})
    if not result or any(not STATE.fullmatch(item) for item in result): raise ValueError("select one or more two-letter NWS state codes")
    return result

def user_agent(email: str) -> str: return f"MeshCoreWeatherDispatch/0.1 ({contact_email(email)})"

def _json(value, fallback):
    try: return json.loads(value) if value is not None else fallback
    except (TypeError, ValueError): return fallback

def runtime_config(config: dict, db) -> dict:
    """Read dynamic choices on demand; service-managed config is never web writable."""
    out=dict(config)
    selected=_json(db.setting("nws_states"), config.get("states",[]))
    out["states"]=states(selected) if selected else []
    interval=db.setting("nws_poll_seconds")
    out["poll_seconds"]=max(60,int(interval or config.get("poll_seconds",60)))
    email=db.setting("nws_contact_email")
    if email:
        out["nws_user_agent"]=user_agent(email)
    out["transmit_enabled"]=(db.setting("transmit_enabled") or "false").lower()=="true"
    return out

def transmissions_enabled(config: dict, db) -> bool:
    return bool(runtime_config(config,db).get("transmit_enabled") and os.environ.get("MESHCORE_SEND_ENABLED")=="1")
