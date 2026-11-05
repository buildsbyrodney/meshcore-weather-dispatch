"""Small, fail-closed automatic warning dispatcher.

It deliberately has no durable retry queue: a restart, disconnect, or failed
attempt cannot turn into delayed transmissions.  The single process and the
adapter's radio lock serialize it with manual sends.
"""
from __future__ import annotations
from datetime import datetime, timezone
from .meshcore_adapter import RadioError, verify_channel, send
from .runtime import runtime_config
from .routes_support import auto_device_state, auto_saved_channel

MAX_AGE_SECONDS=300

def _parsed(value):
    try: return datetime.fromisoformat(str(value).replace("Z","+00:00")).astimezone(timezone.utc)
    except (TypeError,ValueError): return None

def eligible_auto(app, alert):
    db=app.extensions["db"];cfg=runtime_config(app.config,db)
    if alert["test_only"] or not cfg["auto_sending_enabled"] or alert["event"] not in cfg["auto_warning_types"]: return False
    props=__import__("json").loads(alert["raw_json"])["properties"]
    kind=str(props.get("_mesh_kind") or "").upper()
    if kind=="CANCELED": return db.family_was_auto_sent(alert["family_id"])
    # A direct ingest caller must not bypass the poller's official overlap
    # filter.  Empty codes are never treated as all selected counties.
    if not str(props.get("_mesh_matched_areas") or "").strip(): return False
    if alert["status"]!="pending": return False
    sent=_parsed(alert["sent"])
    floor=_parsed(db.setting("auto_dispatch_not_before"))
    current=datetime.now(timezone.utc)
    return bool(sent and floor and sent>=floor and 0 <= (current-sent).total_seconds() <= MAX_AGE_SECONDS)

def dispatch(app, cap_id):
    db=app.extensions["db"];alert=db.get(cap_id)
    if not alert or not eligible_auto(app,alert): return False
    # One bounded, non-retrying attempt per newly observed CAP revision.
    if alert["status"]=="pending" and not db.claim(cap_id,"automatic"): return False
    _,device,status=auto_device_state(db);channel=auto_saved_channel(db)
    try:
        verify_channel(device,channel)
        from .messages import radio_message
        message=radio_message(__import__("json").loads(alert["raw_json"]),db.setting("timezone") or app.config.get("timezone","UTC"),app.config.get("max_message_bytes",180))
        detail=send(runtime_config(app.config,db),device,channel,message)
        db.finish(cap_id,"auto_sent","automatic",detail,message)
        return True
    except (RadioError,ValueError,KeyError) as exc:
        # A failed attempt is terminal: no reconnect/backlog retry storm.
        db.finish(cap_id,"auto_failed","automatic",f"{status}. {exc}",alert["message"])
        return False
