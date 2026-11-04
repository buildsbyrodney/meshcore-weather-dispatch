"""SQLite-backed operational settings with installer-managed service configuration."""
from __future__ import annotations
import json, re
from .version import VERSION
from .alert_types import DEFAULT_ALERT_TYPES

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

def user_agent(email: str) -> str: return f"MeshCoreWeatherDispatch/{VERSION} ({contact_email(email)})"

def _json(value, fallback):
    try: return json.loads(value) if value is not None else fallback
    except (TypeError, ValueError): return fallback

def alert_types(value, fallback=DEFAULT_ALERT_TYPES) -> list[str]:
    """Normalize operator selections; a malformed persisted value fails closed."""
    if value is None:
        value=fallback
    if not isinstance(value, (list, tuple)):
        return []
    return sorted({str(event).strip() for event in value if isinstance(event,str) and str(event).strip()})

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
    saved_alert_types=db.setting("nws_alert_types")
    if saved_alert_types is None:
        out["alert_types"]=alert_types(None,config.get("alert_types",DEFAULT_ALERT_TYPES))
    else:
        try:
            out["alert_types"]=alert_types(json.loads(saved_alert_types))
        except (TypeError, ValueError):
            # A corrupted persisted selection must never broaden monitoring.
            out["alert_types"]=[]
    out["transmit_enabled"]=(db.setting("transmit_enabled") or "false").lower()=="true"
    return out

def transmissions_enabled(config: dict, db) -> bool:
    return bool(runtime_config(config,db).get("transmit_enabled"))
