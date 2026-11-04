import os, stat, tomllib
from pathlib import Path
import pytest
from werkzeug.security import generate_password_hash, check_password_hash

from dispatch import create_app, load_config
from dispatch.bootstrap import initialize
from dispatch.poller import fetch
from dispatch.runtime import runtime_config, user_agent
from dispatch.version import VERSION


def cfg(tmp_path):
    return {"TESTING":True,"secret_key":"test","database":str(tmp_path/"settings.sqlite"),"admin_username":"admin","admin_password_hash":generate_password_hash("current-password"),"nws_user_agent":"MeshCoreWeatherDispatch/0.1 old@example.invalid","states":["AL","TN"],"alert_types":["Tornado Warning"],"counties":[],"max_message_bytes":80,"transmit_enabled":False,"poll_seconds":60,"start_poller":False}

def token(client): return client.get("/settings").data.decode().split('name="csrf_token" value="')[1].split('"')[0]

def signed_in(client):
    with client.session_transaction() as session: session["user"]="admin"

def test_bootstrap_generates_private_secret_once_and_seeds_sqlite(tmp_path):
    config_path=tmp_path/"etc"/"config.yml"; environment=tmp_path/"etc"/"environment";database=tmp_path/"state"/"dispatch.sqlite3"
    initialize(config_path,environment,database,"weatheradmin","password-long-enough","operator@example.org","AL,TN")
    first=environment.read_text(); assert "WEATHER_DISPATCH_SECRET_KEY=" in first and "MESHCORE_SEND_ENABLED" not in first
    if os.name != "nt": assert stat.S_IMODE(environment.stat().st_mode)==0o600 and stat.S_IMODE(config_path.stat().st_mode)==0o600
    initialize(config_path,environment,database,"weatheradmin","changed-password-long","operator@example.org","AL,TN")
    assert environment.read_text()==first
    app=create_app({**cfg(tmp_path),"database":str(database)})
    username,password_hash=app.extensions["db"].credentials()
    assert username=="weatheradmin" and check_password_hash(password_hash,"changed-password-long") and app.extensions["db"].setting("transmit_enabled")=="true"

def test_bootstrap_accepts_simple_six_character_password_and_rejects_shorter_one(tmp_path):
    config_path=tmp_path/"etc"/"config.yml"; environment=tmp_path/"etc"/"environment";database=tmp_path/"state"/"dispatch.sqlite3"
    initialize(config_path,environment,database,"weatheradmin","simple","operator@example.org","AL")
    assert check_password_hash(create_app({**cfg(tmp_path),"database":str(database)}).extensions["db"].credentials()[1],"simple")
    with pytest.raises(ValueError,match="at least 6 characters"):
        initialize(tmp_path/"short.yml",tmp_path/"short.env",tmp_path/"short.sqlite3","weatheradmin","short","operator@example.org","AL")

def test_nws_settings_are_dynamic_and_do_not_discard_out_of_state_counties(tmp_path):
    app=create_app(cfg(tmp_path));db=app.extensions["db"];client=app.test_client();signed_in(client)
    db.replace_catalog([{"ugc":"ALC089","state":"AL","name":"Madison","same":"001089"},{"ugc":"TNC103","state":"TN","name":"Lincoln","same":"047103"}],{"source":"test"});db.save_coverage(["ALC089","TNC103"])
    response=client.post("/settings/nws",data={"csrf_token":token(client),"contact_email":"wx@example.org","poll_seconds":"90","states":"AL"})
    assert response.status_code==302 and runtime_config(app.config,db)["states"]==["AL","TN"]
    response=client.post("/settings/nws",data={"csrf_token":token(client),"contact_email":"wx@example.org","poll_seconds":"90","states":"AL","confirm_retained_counties":"yes"})
    runtime=runtime_config(app.config,db)
    assert runtime["states"]==["AL"] and runtime["poll_seconds"]==90 and "wx@example.org" in runtime["nws_user_agent"]
    assert {row["ugc"] for row in db.coverage()}=={"ALC089","TNC103"}

def test_account_change_requires_current_password_and_rehashes(tmp_path):
    app=create_app(cfg(tmp_path));db=app.extensions["db"];client=app.test_client();signed_in(client)
    client.post("/settings/account",data={"csrf_token":token(client),"username":"newadmin","current_password":"wrong","new_password":"new-password-long","confirm_password":"new-password-long"})
    assert db.credentials()[0]=="admin"
    client.post("/settings/account",data={"csrf_token":token(client),"username":"newadmin","current_password":"current-password","new_password":"new-password-long","confirm_password":"new-password-long"})
    user=db.user("newadmin",active_only=True);assert user and check_password_hash(user["password_hash"],"new-password-long")

def test_account_password_mismatch_is_rejected_and_simple_six_character_password_is_accepted(tmp_path):
    app=create_app(cfg(tmp_path));db=app.extensions["db"];client=app.test_client();signed_in(client)
    response=client.post("/settings/account",data={"csrf_token":token(client),"username":"admin","current_password":"current-password","new_password":"simple","confirm_password":"different"},follow_redirects=True)
    assert "confirmation does not match" in response.data.decode() and check_password_hash(db.credentials()[1],"current-password")
    client.post("/settings/account",data={"csrf_token":token(client),"username":"admin","current_password":"current-password","new_password":"simple","confirm_password":"simple"})
    assert check_password_hash(db.user("admin",active_only=True)["password_hash"],"simple")

def test_unconfigured_new_install_never_polls_all_states(tmp_path,monkeypatch):
    fresh=cfg(tmp_path);fresh.pop("states");fresh.pop("nws_user_agent")
    app=create_app(fresh);called=[];monkeypatch.setattr("dispatch.poller.requests.get",lambda *a,**k: called.append((a,k)))
    fetch(app)
    assert not called and "not configured" in app.extensions["db"].setting("nws_last_error")

def test_manual_transmission_defaults_enabled_and_existing_setting_is_preserved(tmp_path,monkeypatch):
    app=create_app(cfg(tmp_path));db=app.extensions["db"];client=app.test_client();signed_in(client)
    assert runtime_config(app.config,db)["transmit_enabled"] is False
    client.post("/settings/transmission",data={"csrf_token":token(client),"transmit_enabled":"yes"})
    assert runtime_config(app.config,db)["transmit_enabled"] is True
    db.save_setting("transmit_enabled","false")
    db.bootstrap_operational_settings({"transmit_enabled":True})
    assert db.setting("transmit_enabled")=="false"

def test_release_version_is_used_in_the_nws_user_agent():
    metadata=tomllib.loads((Path(__file__).parents[1]/"pyproject.toml").read_text(encoding="utf-8"))
    assert metadata["project"]["version"]=="1.0.0rc2"
    assert VERSION=="1.0.0-rc.2"
    assert user_agent("operator@example.org")=="MeshCoreWeatherDispatch/1.0.0-rc.2 (operator@example.org)"

def test_transmission_settings_show_manual_permission_and_readiness_requirements(tmp_path):
    app=create_app(cfg(tmp_path));db=app.extensions["db"]
    client=app.test_client();signed_in(client)
    page=client.get("/settings").data.decode()
    assert "Companion:" in page and "Transmit channel:" in page and "Manual transmissions disabled" in page
    assert "Allow Manual Transmissions" in page
    assert 'class="inline-check"' in page
    assert "dry-run" not in page.lower() and "interlock" not in page.lower()

def test_settings_promotes_sqlite_backed_alert_type_selection(tmp_path):
    app=create_app(cfg(tmp_path));client=app.test_client();signed_in(client)
    page=client.get("/settings").data.decode()
    assert "Alert Types" in page and "1</b> alert types enabled" in page
    assert 'href="/settings/alert-types"' in page
