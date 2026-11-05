from datetime import datetime, timezone
from dispatch import create_app
from dispatch.poller import ingest
from dispatch.automatic import AutomaticQueue

def config(tmp_path):
    return {"TESTING":True,"secret_key":"test","database":str(tmp_path/"auto.sqlite"),"admin_username":"admin","admin_password_hash":"hash","nws_user_agent":"tests@example.invalid","states":["AL"],"alert_types":["Tornado Warning","Tornado Watch"],"counties":[{"name":"Bibb County, AL","same":"001007","ugc":"ALC007"},{"name":"Jefferson County, AL","same":"001073","ugc":"ALC073"}],"max_message_bytes":180,"transmit_enabled":True,"poll_seconds":60,"start_poller":False}

def feature(identifier="one", kind="Alert", refs="", codes=None):
    return {"properties":{"id":identifier,"event":"Tornado Warning","sent":datetime.now(timezone.utc).isoformat(),"expires":"2099-01-01T00:00:00+00:00","messageType":kind,"references":refs,"senderName":"NWS Birmingham AL","geocode":{"SAME":codes or ["001007","001083"]}}}

def enabled(app):
    db=app.extensions["db"]
    db.save_settings({"auto_sending_enabled":"true","auto_warning_types":"[\"Tornado Warning\"]","auto_dispatch_not_before":datetime.now(timezone.utc).isoformat(),"tx_channel":'{"index": 2, "name": "#bmx"}'})

def csrf(client): return client.get("/").data.decode().split('name="csrf_token" value="')[1].split('"')[0]

def test_console_switch_defaults_off_requires_confirmation_and_persists(tmp_path):
    app=create_app(config(tmp_path));db=app.extensions["db"];client=app.test_client()
    with client.session_transaction() as session: session["user_id"]=db.user("admin")["id"]
    assert "Automatic Sending: OFF" in client.get("/").data.decode()
    client.post("/automatic-sending",data={"csrf_token":csrf(client),"automatic_enabled":"yes"})
    assert db.setting("auto_sending_enabled")=="false"
    db.save_setting("auto_warning_types",'["Tornado Warning"]')
    client.post("/automatic-sending",data={"csrf_token":csrf(client),"automatic_enabled":"yes","confirm":"yes"})
    assert db.setting("auto_sending_enabled")=="true" and create_app(config(tmp_path)).extensions["db"].setting("auto_sending_enabled")=="true"

def test_auto_is_off_by_default_and_uses_only_matching_counties(tmp_path,monkeypatch):
    app=create_app(config(tmp_path));sent=[]
    monkeypatch.setattr("dispatch.automatic.auto_device_state",lambda db: ({},{"device":"fake"},"connected"))
    monkeypatch.setattr("dispatch.automatic.verify_channel",lambda *a: None)
    monkeypatch.setattr("dispatch.automatic.send",lambda *a: sent.append(a[-1]) or "accepted")
    ingest(app,[feature()]);assert not sent
    enabled(app); ingest(app,[feature("two")])
    assert sent and "Bibb County, AL" in sent[-1] and "Jefferson County" not in sent[-1]
    assert app.extensions["db"].get("two")["status"]=="auto_sent"

def test_watch_is_never_automatic_and_failure_is_not_retried(tmp_path,monkeypatch):
    app=create_app(config(tmp_path));enabled(app);called=[]
    monkeypatch.setattr("dispatch.automatic.auto_device_state",lambda db: ({},{"device":"fake"},"connected"))
    monkeypatch.setattr("dispatch.automatic.verify_channel",lambda *a: None)
    monkeypatch.setattr("dispatch.automatic.send",lambda *a: called.append(1) or (_ for _ in ()).throw(__import__("dispatch.meshcore_adapter",fromlist=["RadioError"]).RadioError("gone")))
    watch=feature("watch");watch["properties"]["event"]="Tornado Watch";ingest(app,[watch])
    ingest(app,[feature("fail")]);assert called==[1] and app.extensions["db"].get("fail")["status"]=="auto_failed"

def test_cancel_uses_prior_official_counties_only_after_auto_send(tmp_path,monkeypatch):
    app=create_app(config(tmp_path));enabled(app);sent=[]
    monkeypatch.setattr("dispatch.automatic.auto_device_state",lambda db: ({},{"device":"fake"},"connected"))
    monkeypatch.setattr("dispatch.automatic.verify_channel",lambda *a: None)
    monkeypatch.setattr("dispatch.automatic.send",lambda *a: sent.append(a[-1]) or "accepted")
    ingest(app,[feature("original")]);cancel=feature("cancel","Cancel","sender,original,when",codes=[]);cancel["properties"]["geocode"]={};ingest(app,[cancel])
    queue=app.extensions["auto_queue"];queue.last_attempt-=5;queue._timer_fired()
    assert len(sent)==2 and sent[-1].startswith("CANCELED TORNADO WARNING") and "Bibb County, AL" in sent[-1]

def test_simultaneous_auto_work_is_spaced_and_old_cancel_is_rejected(tmp_path,monkeypatch):
    app=create_app(config(tmp_path));enabled(app);sent=[];clock=[0.0]
    class Timer:
        def __init__(self,delay,fn): self.delay=delay;self.fn=fn;self.daemon=False
        def start(self): pass
    queue=AutomaticQueue(app,clock=lambda:clock[0],timer=Timer);app.extensions["auto_queue"]=queue
    monkeypatch.setattr("dispatch.automatic.auto_device_state",lambda db: ({},{"device":"fake"},"connected"))
    monkeypatch.setattr("dispatch.automatic.verify_channel",lambda *a: None)
    monkeypatch.setattr("dispatch.automatic.send",lambda *a: sent.append((clock[0],a[-2])) or "accepted")
    ingest(app,[feature("one"),feature("two")]);assert [x[0] for x in sent]==[0.0]
    clock[0]=4.9;queue._timer_fired();assert len(sent)==1
    clock[0]=5.0;queue._timer_fired();assert [x[0] for x in sent]==[0.0,5.0]
    old=feature("old-cancel","Cancel","sender,one,when",codes=[]);old["properties"].update({"sent":"2000-01-01T00:00:00+00:00","geocode":{}});ingest(app,[old])
    assert len(sent)==2
