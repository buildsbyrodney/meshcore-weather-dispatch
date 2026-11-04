"""Operational alert-type selection, catalog caching, and revision continuity."""
from __future__ import annotations

import json

import requests

from dispatch import create_app
from dispatch.alert_types import DEFAULT_ALERT_TYPES, AlertTypeCatalogError, normalize_event_types
from dispatch.poller import fetch, ingest
from dispatch.runtime import runtime_config


def config(tmp_path, **changes):
    value={
        "TESTING": True, "secret_key": "test", "database": str(tmp_path / "alert-types.sqlite"),
        "admin_username": "admin", "admin_password_hash": "hash", "nws_user_agent": "tests@example.invalid",
        "states": ["AL"], "counties": [{"name":"Madison","same":"001089","ugc":"ALC089"}],
        "max_message_bytes": 180, "transmit_enabled": False, "poll_seconds": 60, "start_poller": False,
    }
    value.update(changes)
    return value


def signed_in(client):
    with client.session_transaction() as session:
        session["user"]="admin"


def csrf(client, page="/settings/alert-types"):
    return client.get(page).data.decode().split('name="csrf_token" value="')[1].split('"')[0]


def feature(alert_id, event="Tornado Warning", references="", description="Shelter now"):
    return {"properties":{"id":alert_id,"event":event,"areaDesc":"Madison","headline":event,"sent":"2026-01-01T00:00:00+00:00","effective":"2026-01-01T00:00:00+00:00","expires":"2099-01-01T00:00:00+00:00","description":description,"messageType":"Alert","references":references,"geocode":{"SAME":["001089"],"UGC":["ALC089"]}}}


def test_new_install_defaults_and_legacy_yaml_migration_are_preserved(tmp_path):
    app=create_app(config(tmp_path))
    db=app.extensions["db"]
    assert runtime_config(app.config,db)["alert_types"]==sorted(DEFAULT_ALERT_TYPES)
    legacy_path=tmp_path / "legacy";legacy_path.mkdir()
    legacy=create_app(config(legacy_path, alert_types=["Flood Warning", "Tornado Warning"]))
    legacy_db=legacy.extensions["db"]
    assert runtime_config(legacy.config,legacy_db)["alert_types"]==["Flood Warning","Tornado Warning"]
    legacy_db.save_setting("nws_alert_types",json.dumps(["Special Weather Statement"]))
    legacy_db.bootstrap_operational_settings({"alert_types":["Tornado Warning"]})
    assert runtime_config(legacy.config,legacy_db)["alert_types"]==["Special Weather Statement"]


def test_alert_type_page_saves_selection_persists_and_audits(tmp_path):
    app=create_app(config(tmp_path));db=app.extensions["db"];client=app.test_client();signed_in(client)
    db.replace_alert_type_catalog(["Tornado Warning","Tornado Watch","Heat Advisory","Special Weather Statement"],{"source":"test","retrieved_at":"now","version":"v1"})
    page=client.get("/settings/alert-types").data.decode()
    assert "Warnings" in page and "Watches" in page and "Advisories" in page and "Other" in page
    response=client.post("/settings/alert-types",data={"csrf_token":csrf(client),"action":"save","event":["Tornado Watch","Heat Advisory"]},follow_redirects=True)
    assert "Alert types saved: 2 enabled" in response.data.decode()
    assert runtime_config(app.config,db)["alert_types"]==["Heat Advisory","Tornado Watch"]


def test_previous_selection_missing_from_refreshed_catalog_stays_visible_and_selectable(tmp_path):
    app=create_app(config(tmp_path));db=app.extensions["db"];client=app.test_client();signed_in(client)
    db.save_setting("nws_alert_types",json.dumps(["Retired Local Warning"]))
    db.replace_alert_type_catalog(["Tornado Warning"],{"source":"test","retrieved_at":"now","version":"v2"})
    page=client.get("/settings/alert-types").data.decode()
    assert "Previously Configured" in page
    assert 'value="Retired Local Warning" checked' in page
    client.post("/settings/alert-types",data={"csrf_token":csrf(client),"action":"save","event":"Retired Local Warning"})
    assert runtime_config(app.config,db)["alert_types"]==["Retired Local Warning"]
    assert db.history()[0]["action"]=="alert_types_saved" and db.history()[0]["actor"]=="admin"
    rejected=client.post("/settings/alert-types",data={"csrf_token":csrf(client),"action":"save","event":"Not an NWS event"},follow_redirects=True)
    assert "not in the available NWS catalog" in rejected.data.decode()
    assert runtime_config(app.config,db)["alert_types"]==["Heat Advisory","Tornado Watch"]


def test_empty_and_malformed_selection_fail_closed_and_test_alerts_use_runtime_selection(tmp_path):
    app=create_app(config(tmp_path));db=app.extensions["db"];client=app.test_client();signed_in(client)
    db.save_setting("nws_alert_types","not-json")
    assert runtime_config(app.config,db)["alert_types"]==[]
    db.save_setting("nws_alert_types",json.dumps([]))
    page=client.get("/test-alerts").data.decode()
    assert '<option value="Tornado Warning"' not in page
    choice="same:001089|ugc:ALC089"
    client.post("/test-alerts",data={"csrf_token":csrf(client,"/test-alerts"),"event":"Tornado Warning","county":choice})
    assert not db.actionable()
    db.save_setting("nws_alert_types",json.dumps(["Tornado Warning"]))
    client.post("/test-alerts",data={"csrf_token":csrf(client,"/test-alerts"),"event":"Tornado Warning","county":choice})
    assert db.actionable() and db.actionable()[0]["test_only"]==1


def test_catalog_failure_does_not_replace_cached_catalog(tmp_path, monkeypatch):
    app=create_app(config(tmp_path));db=app.extensions["db"];client=app.test_client();signed_in(client)
    db.replace_alert_type_catalog(["Tornado Warning"],{"source":"old","retrieved_at":"before","version":"old"})
    monkeypatch.setattr("dispatch.routes.fetch_event_types",lambda ua: (_ for _ in ()).throw(requests.RequestException("offline")))
    response=client.post("/settings/alert-types",data={"csrf_token":csrf(client),"action":"refresh"},follow_redirects=True)
    assert "Alert types were not changed" in response.data.decode()
    assert [row["event"] for row in db.alert_type_catalog()]==["Tornado Warning"]


def test_saving_alert_types_never_opens_or_sends_to_the_companion(tmp_path, monkeypatch):
    app=create_app(config(tmp_path));db=app.extensions["db"];client=app.test_client();signed_in(client)
    db.replace_alert_type_catalog(["Tornado Warning","Heat Advisory"],{"source":"test"})
    monkeypatch.setattr("dispatch.routes.send",lambda *args: (_ for _ in ()).throw(AssertionError("settings must never transmit")))
    monkeypatch.setattr("dispatch.routes.verify_connection",lambda *args: (_ for _ in ()).throw(AssertionError("settings must not open a radio")))
    client.post("/settings/alert-types",data={"csrf_token":csrf(client),"action":"save","event":"Heat Advisory"})
    assert runtime_config(app.config,db)["alert_types"]==["Heat Advisory"]


def test_catalog_parser_rejects_malformed_data():
    assert normalize_event_types({"eventTypes":["Tornado Warning"," Tornado Warning ","Heat Advisory"]})==["Heat Advisory","Tornado Warning"]
    try: normalize_event_types({"eventTypes":"not-a-list"})
    except AlertTypeCatalogError: pass
    else: assert False, "malformed NWS catalog must be rejected"


def test_deselection_blocks_new_alerts_but_keeps_known_cap_updates_and_cancellations(tmp_path, monkeypatch):
    app=create_app(config(tmp_path));db=app.extensions["db"]
    db.save_setting("nws_alert_types",json.dumps(["Tornado Warning"]))
    ingest(app,[feature("old")])
    db.save_setting("nws_alert_types",json.dumps([]))
    update=feature("update",references="sender,old,when",description="Updated guidance")
    new=feature("new")
    class Response:
        def raise_for_status(self): pass
        def json(self): return {"features":[update,new]}
    monkeypatch.setattr("dispatch.poller.requests.get",lambda *args,**kwargs: Response())
    fetch(app)
    assert db.get("old")["status"]=="superseded" and db.get("update")["status"]=="pending" and db.get("new") is None
    cancel=feature("cancel",references="sender,update,when");cancel["properties"].update({"messageType":"Cancel","geocode":{}})
    class CancelResponse:
        def raise_for_status(self): pass
        def json(self): return {"features":[cancel]}
    monkeypatch.setattr("dispatch.poller.requests.get",lambda *args,**kwargs: CancelResponse())
    fetch(app)
    assert db.get("update")["status"]=="canceled" and db.get("cancel")["status"]=="canceled"
