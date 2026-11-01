from concurrent.futures import ThreadPoolExecutor
from dispatch import create_app
from dispatch.messages import append_comment, truncate_utf8
from dispatch.devices import resolve_device
from dispatch.poller import ingest, fetch, eligible, cap_reference_ids

def config(tmp_path):
 return {"TESTING":True,"secret_key":"test","database":str(tmp_path/"test.sqlite"),"admin_username":"admin","admin_password_hash":"scrypt:32768:8:1$X$C8YciwLCAazhj6PeXTvoUHl0LTB7qm4JzB47lqYsOiwD9sEzKJzQx9QHdT1yTQ","nws_user_agent":"tests@example.invalid","alert_types":["Tornado Warning"],"counties":[],"max_message_bytes":80,"channels":["#wx-test"],"dry_run":True,"transmit_enabled":False,"poll_seconds":60,"start_poller":False}

def feature(alert_id="new", refs="", kind="Alert", description="Shelter now", expires="2099-01-01T00:00:00+00:00"):
 return {"properties":{"id":alert_id,"event":"Tornado Warning","areaDesc":"Madison","headline":"Tornado warning","sent":"2026-01-01T00:00:00+00:00","expires":expires,"description":description,"messageType":kind,"references":refs}}

def test_utf8_cap_and_marked_comment():
 text=append_comment("A", "é"*100, 20); assert len(text.encode())<=20 and "[OPERATOR:" in text
 assert len(truncate_utf8("é"*20,10).encode())<=10

def test_pending_revision_supersedes_and_is_actionable(tmp_path):
 app=create_app(config(tmp_path)); ingest(app,[feature("old")]); ingest(app,[feature("new","sender,old,when",description="Take shelter now")])
 db=app.extensions["db"]
 assert db.get("old")["status"]=="superseded"
 assert db.get("new")["status"]=="pending" and db.get("new")["revision_label"]=="Updated Warning"
 assert [x["cap_id"] for x in db.actionable()]==["new"]

def test_sent_revision_is_labeled_manual_update(tmp_path):
 app=create_app(config(tmp_path)); ingest(app,[feature("old")]); db=app.extensions["db"]; db.finish("old","sent","operator")
 ingest(app,[feature("new","sender,old,when",description="New shelter instruction")])
 assert db.get("old")["status"]=="sent"
 assert db.get("new")["status"]=="pending" and db.get("new")["revision_label"]=="Updated Warning — Previously Sent"

def test_ignored_revision_is_labeled_manual_update(tmp_path):
 app=create_app(config(tmp_path)); ingest(app,[feature("old")]); db=app.extensions["db"]; db.finish("old","ignored","operator")
 ingest(app,[feature("new","sender,old,when",description="Changed instruction")])
 assert db.get("old")["status"]=="ignored" and db.get("new")["revision_label"]=="Updated Warning — Previously Ignored"

def test_unchanged_revision_does_not_create_duplicate_action(tmp_path):
 app=create_app(config(tmp_path)); ingest(app,[feature("old")]); ingest(app,[feature("new","sender,old,when")])
 db=app.extensions["db"]
 assert db.get("old")["status"]=="pending" and db.get("new")["status"]=="unchanged"
 assert [x["cap_id"] for x in db.actionable()]==["old"]

def test_cancel_and_expiration_preserve_history(tmp_path):
 app=create_app(config(tmp_path)); ingest(app,[feature("sent")]); db=app.extensions["db"]; db.finish("sent","sent","operator")
 ingest(app,[feature("pending","sender,sent,when",description="Updated")])
 ingest(app,[feature("cancel","sender,pending,when",kind="Cancel")])
 assert db.get("sent")["status"]=="sent" and db.get("pending")["status"]=="canceled" and db.get("cancel")["status"]=="canceled"
 ingest(app,[feature("expired",expires="2000-01-01T00:00:00+00:00")])
 assert db.get("expired")["status"]=="expired" and not db.actionable()

def test_simultaneous_claim_allows_one_sender(tmp_path):
 app=create_app(config(tmp_path)); ingest(app,[feature()]); db=app.extensions["db"]
 with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(lambda who: db.claim("new",who),["one","two"]))
 assert results.count(True)==1 and db.get("new")["status"]=="sending"

def test_device_never_guesses_ambiguous():
 saved={"vid":"1234","pid":"5678","serial_number":"same"}; found=[dict(saved,device="/dev/a",stable_path=None),dict(saved,device="/dev/b",stable_path=None)]
 assert resolve_device(saved,found)[0] is None and "Ambiguous" in resolve_device(saved,found)[1]

def test_actual_nws_geocodes_support_partial_and_multi_county_alerts():
 partial={"properties":{"event":"Tornado Warning","geocode":{"SAME":["001089"],"UGC":["ALC089"]},"areaDesc":"Madison County"},"geometry":None}
 multi={"properties":{"event":"Tornado Warning","geocode":{"SAME":["001089","001083"]}},"geometry":None}
 assert eligible(partial,{"alert_types":["Tornado Warning"],"counties":["ALC089"]})
 assert eligible(multi,{"alert_types":["Tornado Warning"],"counties":["01083"]})
 assert not eligible(partial,{"alert_types":["Tornado Warning"],"counties":["01083"]})
 assert cap_reference_ids("w-nws.webmaster@noaa.gov,urn:oid:2.49.0.1,2026-01-01T00:00:00-00:00")==["urn:oid:2.49.0.1"]

def test_dry_run_send_is_audited(tmp_path,monkeypatch):
 app=create_app(config(tmp_path)); ingest(app,[feature()]); client=app.test_client()
 with client.session_transaction() as s: s["user"]="admin"
 token=client.get("/").data.decode().split('name="csrf_token" value="')[1].split('"')[0]
 monkeypatch.setattr("dispatch.routes.scan_devices",lambda:[])
 assert client.post("/alerts/new/send",data={"csrf_token":token,"comment":"Test"}).status_code==302
 assert app.extensions["db"].get("new")["status"]=="sent"

def test_repeated_nws_polling_is_idempotent_and_healthy(tmp_path,monkeypatch):
 app=create_app(config(tmp_path)); calls=[]
 class Response:
  def raise_for_status(self): pass
  def json(self): return {"features":[feature("from-nws")]}
 def fake_get(url, **kwargs): calls.append(kwargs); return Response()
 monkeypatch.setattr("dispatch.poller.requests.get",fake_get)
 fetch(app); first=app.extensions["db"].history(); fetch(app)
 assert len(calls)==2 and app.extensions["db"].setting("nws_last_success") and app.extensions["db"].setting("nws_last_error")==""
 assert len(app.extensions["db"].history())==len(first)==1

def test_api_returns_fragments_for_live_update_and_hardened_session(tmp_path):
 app=create_app(config(tmp_path)); ingest(app,[feature()]); client=app.test_client()
 with client.session_transaction() as s: s["user"]="admin"
 response=client.get("/api/state"); payload=response.get_json()
 assert "alerts_html" in payload and "history_html" in payload and "alert" in payload["alerts_html"]
 assert app.config["SESSION_COOKIE_HTTPONLY"] and app.config["SESSION_COOKIE_SAMESITE"]=="Lax"
