"""Explicit, local-only synthetic NWS features for operator workflow tests."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from .poller import coverage_counties

class TestAlertError(ValueError): pass

def build_test_alert(config: dict, selected_ids: list[str], event: str) -> dict:
    if event not in config.get("alert_types",[]):
        raise TestAlertError("select a configured alert type")
    options={choice["id"]:choice for choice in coverage_counties(config)}
    selected=[]
    for choice_id in selected_ids:
        choice=options.get(choice_id)
        if choice is None: raise TestAlertError("selected county is not in configured coverage")
        if choice not in selected: selected.append(choice)
    if not selected: raise TestAlertError("select at least one affected county")
    same=[code for choice in selected for code in choice["same"]]
    ugc=[code for choice in selected for code in choice["ugc"]]
    timestamp=datetime.now(timezone.utc); sent=timestamp.isoformat(); expires=(timestamp+timedelta(minutes=30)).isoformat()
    areas=", ".join(choice["label"] for choice in selected)
    return {"type":"Feature","geometry":None,"properties":{
        "id":f"test-alert-{uuid4()}","event":event,"areaDesc":areas,
        "headline":f"TEST ONLY — {event}",
        "description":"TEST ONLY synthetic warning.",
        "instruction":"",
        "sent":sent,"effective":sent,"expires":expires,"messageType":"Alert",
        "geocode":{"SAME":same,"UGC":ugc},"test_only":True,
    }}
