import asyncio
import sys
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from dispatch import create_app
from dispatch.messages import append_comment, truncate_utf8
from dispatch.messages import radio_message
from dispatch.devices import resolve_device
from dispatch.poller import ingest, fetch, eligible, cap_reference_ids
from dispatch.poller import coverage_counties
from dispatch.test_alerts import build_test_alert
from dispatch.coverage import normalize_catalog
import dispatch.meshcore_adapter as adapter
from dispatch.meshcore_adapter import RadioError, verify_channel, hashtag_secret, validate_hashtag, set_device_name
from dispatch.timefmt import local_time, compact_local_time

def config(tmp_path):
 return {"TESTING":True,"secret_key":"test","database":str(tmp_path/"test.sqlite"),"admin_username":"admin","admin_password_hash":"scrypt:32768:8:1$X$C8YciwLCAazhj6PeXTvoUHl0LTB7qm4JzB47lqYsOiwD9sEzKJzQx9QHdT1yTQ","nws_user_agent":"tests@example.invalid","states":["AL"],"alert_types":["Tornado Warning"],"counties":[{"name":"Madison County, AL","same":"001089","ugc":"ALC089"}],"max_message_bytes":80,"transmit_enabled":False,"poll_seconds":60,"start_poller":False}

def csrf_token(client, page="/"):
 return client.get(page).data.decode().split('name="csrf_token" value="')[1].split('"')[0]

def alert_preview(client, cap_id, token, comment=""):
 return client.post(f"/alerts/{cap_id}/preview",data={"csrf_token":token,"comment":comment})

def feature(alert_id="new", refs="", kind="Alert", description="Shelter now", expires="2099-01-01T00:00:00+00:00"):
 return {"properties":{"id":alert_id,"event":"Tornado Warning","areaDesc":"Madison","headline":"Tornado warning","sent":"2026-01-01T00:00:00+00:00","expires":expires,"description":description,"messageType":kind,"references":refs,"geocode":{"SAME":["001089"],"UGC":["ALC089"]}}}

def test_utf8_cap_and_marked_comment():
 try: append_comment("A", "é"*100, 20)
 except Exception: pass
 else: assert False
 assert len(truncate_utf8("é"*20,10).encode())<=10

def test_radio_message_uses_local_timezone_and_keeps_essential_area():
 f=feature();f["properties"]["expires"]="2026-07-01T18:00:00+00:00";f["properties"]["areaDesc"]="Madison County, AL"
 text=radio_message(f,"America/Chicago")
 assert "Madison County, AL" in text and "CDT" in text and "UTC" not in text

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

def test_later_revision_retains_previously_sent_context(tmp_path):
 app=create_app(config(tmp_path)); ingest(app,[feature("sent")]); db=app.extensions["db"]; db.finish("sent","sent","operator")
 ingest(app,[feature("middle","sender,sent,when",description="First update")])
 ingest(app,[feature("latest","sender,middle,when",description="Second update")])
 assert db.get("middle")["status"]=="superseded"
 assert db.get("latest")["revision_label"]=="Updated Warning — Previously Sent"

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

def test_test_alert_single_county_uses_configured_identifiers(tmp_path):
 cfg=config(tmp_path);cfg["counties"]=[{"name":"Madison County, AL","same":"001089","ugc":"ALC089"}]
 choice=coverage_counties(cfg)[0]; feature=build_test_alert(cfg,[choice["id"]],"Tornado Warning")
 props=feature["properties"]
 assert props["test_only"] and props["geocode"]=={"SAME":["001089"],"UGC":["ALC089"]}
 assert props["instruction"]=="" and radio_message(feature,"America/Chicago").count("TEST ONLY")==1
 app=create_app(cfg);ingest(app,[feature]);alert=app.extensions["db"].get(props["id"])
 assert alert["test_only"]==1 and alert["status"]=="pending" and alert["message"].startswith("TEST ONLY")

def test_test_alert_multi_county_cross_state_is_one_alert(tmp_path):
 cfg=config(tmp_path);cfg["states"]=["AL","TN"];cfg["counties"]=[{"name":"Madison County, AL","same":"001089","ugc":"ALC089"},{"name":"Lincoln County, TN","same":"047103","ugc":"TNC103"}]
 choices=coverage_counties(cfg); feature=build_test_alert(cfg,[x["id"] for x in choices],"Tornado Warning")
 assert feature["properties"]["geocode"]=={"SAME":["001089","047103"],"UGC":["ALC089","TNC103"]}
 app=create_app(cfg);ingest(app,[feature]); alerts=app.extensions["db"].actionable()
 assert len(alerts)==1 and "Madison County, AL, Lincoln County, TN"==alerts[0]["areas"]

def test_test_alert_route_requires_county_and_uses_manual_workflow(tmp_path):
 cfg=config(tmp_path);cfg["counties"]=[{"name":"Madison County, AL","same":"001089","ugc":"ALC089"}]
 app=create_app(cfg);client=app.test_client()
 with client.session_transaction() as s:s["user"]="admin"
 page=client.get("/test-alerts").data.decode();token=page.split('name="csrf_token" value="')[1].split('"')[0]
 client.post("/test-alerts",data={"csrf_token":token,"event":"Tornado Warning"})
 assert not app.extensions["db"].actionable()
 choice=coverage_counties(cfg)[0]
 client.post("/test-alerts",data={"csrf_token":token,"event":"Tornado Warning","county":choice["id"]})
 alert=app.extensions["db"].actionable()[0]
 assert "TEST ONLY" in alert["revision_label"] and app.extensions["db"].history()[0]["action"]=="test_alert_created"

def test_test_alert_maximum_length_keeps_test_marker_and_all_counties(tmp_path):
 cfg=config(tmp_path);cfg["max_message_bytes"]=80;cfg["counties"]= [{"name":f"Very Long County Name {n}","same":f"01{n:04d}"} for n in range(20)]
 feature=build_test_alert(cfg,[x["id"] for x in coverage_counties(cfg)],"Tornado Warning")
 app=create_app(cfg);ingest(app,[feature]);alert=app.extensions["db"].get(feature["properties"]["id"])
 assert len(alert["message"].encode("utf-8"))>80 and alert["message"].startswith("TEST ONLY")
 assert len(feature["properties"]["geocode"]["SAME"])==20

def test_test_alert_revisions_and_cancel_do_not_alter_genuine_family(tmp_path):
 cfg=config(tmp_path);cfg["counties"]=[{"name":"Madison County, AL","same":"001089","ugc":"ALC089"}]
 app=create_app(cfg);db=app.extensions["db"];ingest(app,[feature("real")])
 choice=coverage_counties(cfg)[0]; first=build_test_alert(cfg,[choice["id"]],"Tornado Warning")
 # A forged reference to a real CAP ID must not join or alter that family.
 first["properties"]["references"]="sender,real,when";ingest(app,[first])
 update=build_test_alert(cfg,[choice["id"]],"Tornado Warning")
 update["properties"].update({"references":f"sender,{first['properties']['id']},when","description":"TEST ONLY changed instruction"});ingest(app,[update])
 cancel=build_test_alert(cfg,[choice["id"]],"Tornado Warning")
 cancel["properties"].update({"references":f"sender,{update['properties']['id']},when","messageType":"Cancel"});ingest(app,[cancel])
 assert db.get("real")["status"]=="pending"
 assert db.get(first["properties"]["id"])["status"]=="superseded"
 assert db.get(update["properties"]["id"])["status"]=="canceled"
 assert db.get(cancel["properties"]["id"])["status"]=="canceled"
 assert all("TEST ONLY" in (row["detail"] or "") for row in db.history() if row["cap_id"] != "real")

def test_test_alert_creation_never_uses_radio_and_manual_comment_stays_capped(tmp_path,monkeypatch):
 cfg=config(tmp_path);cfg["max_message_bytes"]=120;cfg["counties"]=[{"name":"Madison County, AL","same":"001089","ugc":"ALC089"}]
 app=create_app(cfg);db=app.extensions["db"];client=app.test_client()
 with client.session_transaction() as s:s["user"]="admin"
 monkeypatch.setattr("dispatch.routes.send",lambda *a: (_ for _ in ()).throw(AssertionError("test-alert creation must not send")))
 monkeypatch.setattr("dispatch.routes.verify_channel",lambda *a: (_ for _ in ()).throw(AssertionError("test-alert creation must not verify radio")))
 token=client.get("/test-alerts").data.decode().split('name="csrf_token" value="')[1].split('"')[0]
 choice=coverage_counties(cfg)[0]
 client.post("/test-alerts",data={"csrf_token":token,"event":"Tornado Warning","county":choice["id"]})
 alert=db.actionable()[0];captured=[]
 monkeypatch.setattr("dispatch.routes.device_state",lambda db: ({},{"device":"/dev/fake"},"Connected"))
 monkeypatch.setattr("dispatch.routes.verify_channel",lambda *a:None)
 monkeypatch.setattr("dispatch.routes.send",lambda config,device,channel,message: captured.append(message) or "accepted")
 db.save_setting("tx_channel",'{"index":1,"name":"#bench"}')
 page=client.get("/").data.decode();token=page.split('name="csrf_token" value="')[1].split('"')[0]
 client.post(f"/alerts/{alert['cap_id']}/send",data={"csrf_token":token,"comment":"é"*100,"confirm":"yes"})
 assert not captured and db.get(alert["cap_id"])["status"]=="pending"

def test_manual_send_is_audited(tmp_path,monkeypatch):
 cfg=config(tmp_path);cfg["max_message_bytes"]=180;app=create_app(cfg); ingest(app,[feature()]); client=app.test_client()
 with client.session_transaction() as s: s["user"]="admin"
 app.extensions["db"].save_setting("tx_channel",'{"index": 2, "name": "#wx-test"}')
 app.extensions["db"].save_setting("transmit_enabled","true")
 token=csrf_token(client)
 monkeypatch.setattr("dispatch.routes.device_state",lambda db: ({}, {"device":"/dev/ttyACM0"}, "Connected"))
 monkeypatch.setattr("dispatch.routes.verify_channel",lambda dev, channel: channel)
 monkeypatch.setattr("dispatch.routes.send",lambda *args:"accepted")
 preview=alert_preview(client,"new",token,"Test").get_json()
 assert client.post("/alerts/new/send",data={"csrf_token":token,"comment":"Test","confirm":"yes","preview_token":preview["token"]}).status_code==302
 assert app.extensions["db"].get("new")["status"]=="sent"

def test_send_preview_is_authoritative_and_one_time(tmp_path,monkeypatch):
 cfg=config(tmp_path);cfg["max_message_bytes"]=180;app=create_app(cfg);ingest(app,[feature()]);db=app.extensions["db"];client=app.test_client()
 with client.session_transaction() as s:s["user"]="admin"
 db.save_setting("tx_channel",'{"index": 2, "name": "#wx-test"}')
 monkeypatch.setattr("dispatch.routes.device_state",lambda db: ({},{"device":"/dev/fake"},"Connected"))
 monkeypatch.setattr("dispatch.routes.verify_channel",lambda *a:None)
 sent=[];monkeypatch.setattr("dispatch.routes.send",lambda *a:sent.append(a[-1]) or "accepted")
 token=csrf_token(client);preview=alert_preview(client,"new",token,"Observe").get_json()
 assert preview["message"].endswith("[OPERATOR: Observe]") and preview["bytes"]==len(preview["message"].encode())
 data={"csrf_token":token,"comment":"Observe","confirm":"yes","preview_token":preview["token"]}
 client.post("/alerts/new/send",data=data)
 client.post("/alerts/new/send",data=data)
 assert sent==[preview["message"]] and db.get("new")["status"]=="sent"

def test_send_rejects_changed_comment_channel_or_expired_alert(tmp_path,monkeypatch):
 cfg=config(tmp_path);cfg["max_message_bytes"]=180;app=create_app(cfg);ingest(app,[feature()]);db=app.extensions["db"];client=app.test_client()
 with client.session_transaction() as s:s["user"]="admin"
 db.save_setting("tx_channel",'{"index": 2, "name": "#wx-test"}')
 monkeypatch.setattr("dispatch.routes.device_state",lambda db: ({},{"device":"/dev/fake"},"Connected"))
 monkeypatch.setattr("dispatch.routes.verify_channel",lambda *a:None)
 sent=[];monkeypatch.setattr("dispatch.routes.send",lambda *a:sent.append(a[-1]) or "accepted")
 token=csrf_token(client);preview=alert_preview(client,"new",token,"Reviewed").get_json()
 client.post("/alerts/new/send",data={"csrf_token":token,"comment":"Altered","confirm":"yes","preview_token":preview["token"]})
 assert not sent and db.get("new")["status"]=="pending"
 preview=alert_preview(client,"new",token,"Reviewed").get_json();db.save_setting("tx_channel",'{"index": 3, "name": "#other"}')
 client.post("/alerts/new/send",data={"csrf_token":token,"comment":"Reviewed","confirm":"yes","preview_token":preview["token"]})
 assert not sent
 db.save_setting("tx_channel",'{"index": 2, "name": "#wx-test"}');preview=alert_preview(client,"new",token,"Reviewed").get_json()
 with db.connect() as c:c.execute("UPDATE alerts SET expires='2000-01-01T00:00:00+00:00' WHERE cap_id='new'")
 client.post("/alerts/new/send",data={"csrf_token":token,"comment":"Reviewed","confirm":"yes","preview_token":preview["token"]})
 assert not sent and db.get("new")["status"]=="pending"

def test_preview_rejects_an_alert_that_expired_between_polls(tmp_path):
 cfg=config(tmp_path);cfg["max_message_bytes"]=180;app=create_app(cfg);ingest(app,[feature()]);db=app.extensions["db"];client=app.test_client()
 with client.session_transaction() as s:s["user"]="admin"
 db.save_setting("tx_channel",'{"index": 2, "name": "#wx-test"}')
 with db.connect() as c:c.execute("UPDATE alerts SET expires='2000-01-01T00:00:00+00:00' WHERE cap_id='new'")
 assert alert_preview(client,"new",csrf_token(client)).status_code==409

def test_simulated_preview_is_labeled_and_oversize_warning_is_blocked(tmp_path):
 cfg=config(tmp_path);cfg["max_message_bytes"]=40;cfg["counties"]=[{"name":"Madison County, AL","same":"001089","ugc":"ALC089"}]
 app=create_app(cfg);client=app.test_client();db=app.extensions["db"]
 with client.session_transaction() as s:s["user"]="admin"
 test_feature=build_test_alert(cfg,[coverage_counties(cfg)[0]["id"]],"Tornado Warning");ingest(app,[test_feature]);db.save_setting("tx_channel",'{"index": 1, "name": "#bench"}')
 response=alert_preview(client,test_feature["properties"]["id"],csrf_token(client))
 assert response.status_code==400 and "essential warning details" in response.get_json()["error"]
 assert db.get(test_feature["properties"]["id"])["status"]=="pending"

def test_compact_dashboard_includes_accessible_send_modal(tmp_path):
 cfg=config(tmp_path);cfg["max_message_bytes"]=180;app=create_app(cfg);ingest(app,[feature()]);client=app.test_client()
 with client.session_transaction() as s:s["user"]="admin"
 page=client.get("/").data.decode()
 assert 'id="send-dialog"' in page and 'aria-labelledby="send-dialog-title"' in page and "Confirm Send" in page
 assert 'type="button" class="send open-send"' in page and 'id="ignore-dialog"' in page and "open-ignore" in page
 assert "confirm('Ignore this warning?')" not in page

def test_connection_state_and_manual_permission_are_compact(tmp_path,monkeypatch):
 app=create_app(config(tmp_path));client=app.test_client()
 with client.session_transaction() as s:s["user"]="admin"
 monkeypatch.setattr("dispatch.routes.device_state",lambda db: ({},{"device":"/dev/x"},"Connected by stable path"))
 monkeypatch.setattr("dispatch.routes.discover_channels",lambda dev:{"capacity":1,"slots":[]})
 monkeypatch.setattr("dispatch.routes.read_device_name",lambda dev:"WXALERT")
 settings=client.get("/settings").data.decode()
 messaging=client.get("/test-messaging").data.decode()
 assert 'class="status-line"' in settings and "Connected by stable path" in settings and "Manual transmissions disabled" in settings
 assert "Manual transmission is disabled or configuration is incomplete." not in messaging
 assert 'class="status-line"' in messaging and "TX Disabled" in messaging
 assert "Manual dispatch only. A Companion accepting" not in client.get("/").data.decode()

def test_settings_promotes_coverage_counties_and_warns_when_empty(tmp_path,monkeypatch):
 cfg=config(tmp_path);cfg["counties"]=[];app=create_app(cfg);client=app.test_client()
 with client.session_transaction() as s:s["user"]="admin"
 db=app.extensions["db"];db.replace_catalog([{"ugc":"ALC089","state":"AL","name":"Madison","same":"001089"}],{"source":"test"})
 monkeypatch.setattr("dispatch.routes.device_state",lambda db: (None,None,"No Companion selected"))
 page=client.get("/settings").data.decode()
 assert '<h2>Coverage Counties</h2>' in page and 'class="coverage-action"' in page
 assert "0</b> selected" in page and "Allowed states: <b>AL</b>" in page
 assert "Live alert filtering and Test Alerts have no county coverage." in page
 db.save_coverage(["ALC089"])
 page=client.get("/settings").data.decode()
 assert "1</b> selected" in page and "Live NWS filtering and Test Alerts use these selected counties." in page

def test_ignore_requires_one_time_server_confirmation(tmp_path):
 app=create_app(config(tmp_path));ingest(app,[feature()]);db=app.extensions["db"];client=app.test_client()
 with client.session_transaction() as s:s["user"]="admin"
 token=csrf_token(client)
 client.post("/alerts/new/ignore",data={"csrf_token":token,"confirm":"yes"})
 assert db.get("new")["status"]=="pending"
 preview=client.post("/alerts/new/ignore-preview",data={"csrf_token":token}).get_json()
 data={"csrf_token":token,"confirm":"yes","ignore_token":preview["token"]}
 client.post("/alerts/new/ignore",data=data)
 client.post("/alerts/new/ignore",data=data)
 assert db.get("new")["status"]=="ignored"

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
 assert response.headers["X-Content-Type-Options"]=="nosniff" and response.headers["X-Frame-Options"]=="SAMEORIGIN"

def test_rejects_unsafe_configuration(tmp_path):
 from dispatch import validate_config
 invalid=config(tmp_path); invalid["states"]=[]
 try: validate_config(invalid)
 except ValueError as error: assert "state" in str(error)
 else: assert False, "empty state list must fail closed"

def test_channel_selection_persists_name_and_slot(tmp_path,monkeypatch):
 app=create_app(config(tmp_path)); client=app.test_client()
 with client.session_transaction() as s: s["user"]="admin"
 monkeypatch.setattr("dispatch.routes.device_state",lambda db: ({}, {"device":"/dev/ttyACM0"}, "Connected"))
 monkeypatch.setattr("dispatch.routes.read_device_name",lambda dev:"WXALERT")
 monkeypatch.setattr("dispatch.routes.discover_channels",lambda dev:{"capacity":4,"slots":[{"index":2,"name":"#wx-northal","occupied":True,"known":True}]})
 monkeypatch.setattr("dispatch.routes.list_channels",lambda dev:[{"index":2,"name":"#wx-northal"}])
 monkeypatch.setattr("dispatch.routes.read_device_name",lambda dev:"WXALERT")
 token=client.get("/settings").data.decode().split('name="csrf_token" value="')[1].split('"')[0]
 assert client.post("/settings/channel",data={"csrf_token":token,"channel_index":"2"}).status_code==302
 assert app.extensions["db"].setting("tx_channel")=="{\"index\": 2, \"name\": \"#wx-northal\"}"

def test_channel_mismatch_blocks_transmission(monkeypatch):
 monkeypatch.setattr("dispatch.meshcore_adapter.list_channels",lambda dev:[{"index":2,"name":"#other"}])
 try: verify_channel({"device":"/dev/ttyACM0"},{"index":2,"name":"#wx-northal"})
 except RadioError as error: assert "no longer matches" in str(error)
 else: assert False, "mismatched slot must be blocked"

def test_channel_discovery_and_connection_errors_are_safe(tmp_path,monkeypatch):
 app=create_app(config(tmp_path)); client=app.test_client()
 with client.session_transaction() as s: s["user"]="admin"
 monkeypatch.setattr("dispatch.routes.device_state",lambda db: ({}, {"device":"/dev/ttyACM0"}, "Connected"))
 monkeypatch.setattr("dispatch.routes.read_device_name",lambda dev:"WXALERT")
 monkeypatch.setattr("dispatch.routes.discover_channels",lambda dev: (_ for _ in ()).throw(RadioError("serial permission denied")))
 monkeypatch.setattr("dispatch.routes.read_device_name",lambda dev:"WXALERT")
 page=client.get("/settings").data.decode(); assert "serial permission denied" in page
 monkeypatch.setattr("dispatch.routes.scan_devices",lambda:[])
 assert "options_html" in client.get("/api/devices").get_json()

def test_local_time_has_zone_abbreviation():
 assert local_time("2026-01-01T18:30:00+00:00","America/Chicago").endswith("CST")
 assert compact_local_time("2026-01-01T18:30:00+00:00","America/Chicago").endswith("CST")

def test_compact_alert_row_shows_event_once_and_keeps_comment_in_modal(tmp_path):
 cfg=config(tmp_path);cfg["max_message_bytes"]=180;cfg["timezone"]="America/Chicago";app=create_app(cfg);ingest(app,[feature()]);client=app.test_client()
 with client.session_transaction() as s:s["user"]="admin"
 row=client.get("/api/state").get_json()["alerts_html"]
 collapsed=row.split("<details>",1)[0]
 assert 'class="alert compact-alert"' in row and "Tornado Warning" in collapsed and "New Warning" in collapsed
 assert "Expires Dec 31, 6:00 PM CST" in collapsed and '<label>Comment' not in row and 'name="comment"' in row
 page=client.get("/").data.decode()
 assert '<label for="send-comment">Operator comment</label>' in page and 'dialog.open||ignoreDialog.open||document.querySelector(\'#alerts details[open]\')' in page

def test_synthetic_alert_has_one_collapsed_test_only_badge(tmp_path):
 cfg=config(tmp_path);cfg["counties"]=[{"name":"Madison County, AL","same":"001089","ugc":"ALC089"}]
 app=create_app(cfg);client=app.test_client()
 with client.session_transaction() as s:s["user"]="admin"
 synthetic=build_test_alert(cfg,[coverage_counties(cfg)[0]["id"]],"Tornado Warning");ingest(app,[synthetic])
 row=client.get("/api/state").get_json()["alerts_html"].split("<details>",1)[0]
 assert row.count("TEST ONLY")==1 and "Tornado Warning" in row

def test_hashtag_validation_and_official_derivation():
 assert hashtag_secret("#test").hex()=="9cd8fcf22a47333b591d96a2b848b73f"
 try: validate_hashtag("not-a-hashtag")
 except RadioError: pass
 else: assert False

def test_unsaved_device_channel_discovery(tmp_path,monkeypatch):
 app=create_app(config(tmp_path)); client=app.test_client()
 with client.session_transaction() as s:s["user"]="admin"
 device={"device":"/dev/ttyACM0","serial_number":"x"}
 monkeypatch.setattr("dispatch.routes.scan_devices",lambda:[device])
 monkeypatch.setattr("dispatch.routes.discover_channels",lambda d:{"capacity":4,"slots":[{"index":0,"name":"#wx","occupied":True},{"index":1,"name":"","occupied":False}]})
 token=client.get("/settings").data.decode().split('name="csrf_token" value="')[1].split('"')[0]
 r=client.post("/settings/discover",data={"csrf_token":token,"device":__import__('json').dumps(device)})
 assert r.status_code==200 and r.get_json()["discovery"]["capacity"]==4

def test_channel_add_requires_confirmation_and_clears_tx(tmp_path,monkeypatch):
 app=create_app(config(tmp_path));db=app.extensions["db"];db.save_setting("tx_channel",'{"index":1,"name":"#old"}');client=app.test_client()
 with client.session_transaction() as s:s["user"]="admin"
 monkeypatch.setattr("dispatch.routes.device_state",lambda db: ({},{"device":"/dev/x"},"Connected"))
 monkeypatch.setattr("dispatch.routes.read_device_name",lambda dev:"WXALERT")
 monkeypatch.setattr("dispatch.routes.discover_channels",lambda dev:{"capacity":4,"slots":[{"index":2,"name":"","occupied":False,"known":True}]})
 monkeypatch.setattr("dispatch.routes.add_hashtag_channel",lambda d,s,n:{"index":s,"name":n})
 monkeypatch.setattr("dispatch.routes.read_device_name",lambda dev:"WXALERT")
 token=client.get("/settings").data.decode().split('name="csrf_token" value="')[1].split('"')[0]
 client.post("/settings/channels/add",data={"csrf_token":token,"slot":"2","name":"#new"})
 assert db.setting("tx_channel") is not None
 client.post("/settings/channels/add",data={"csrf_token":token,"slot":"2","name":"#new","confirm":"yes"})
 assert db.setting("tx_channel")=="null"

def test_timezone_persistence_and_dst(tmp_path):
 app=create_app(config(tmp_path));db=app.extensions["db"];db.save_setting("timezone","America/Chicago")
 assert local_time("2026-07-01T18:00:00+00:00",db.setting("timezone")).endswith("CDT")

def test_channel_delete_protects_tx_and_confirms(tmp_path,monkeypatch):
 app=create_app(config(tmp_path));db=app.extensions["db"];db.save_setting("tx_channel",'{"index":2,"name":"#wx"}');client=app.test_client()
 with client.session_transaction() as s:s["user"]="admin"
 monkeypatch.setattr("dispatch.routes.device_state",lambda db: ({},{"device":"/dev/x","stable_path":"/dev/by-id/x"},"Connected"))
 monkeypatch.setattr("dispatch.routes.read_device_name",lambda dev:"WXALERT")
 monkeypatch.setattr("dispatch.routes.discover_channels",lambda dev:{"capacity":4,"slots":[]})
 monkeypatch.setattr("dispatch.routes.read_device_name",lambda dev:"WXALERT")
 token=client.get("/settings").data.decode().split('name="csrf_token" value="')[1].split('"')[0]
 called=[];monkeypatch.setattr("dispatch.routes.delete_channel",lambda *a:called.append(a))
 client.post("/settings/channels/delete",data={"csrf_token":token,"slot":"2","name":"#wx","confirm":"yes"})
 assert not called
 client.post("/settings/channels/delete",data={"csrf_token":token,"slot":"0","name":"Public","confirm":"yes"})
 assert not called
 db.save_setting("tx_channel","null")
 client.post("/settings/channels/delete",data={"csrf_token":token,"slot":"3","name":"#other","confirm":"yes"})
 assert called and db.history()[0]["action"]=="channel_deleted"

def test_manual_test_message_and_utf8_limit(tmp_path,monkeypatch):
 app=create_app(config(tmp_path));db=app.extensions["db"];db.save_setting("tx_channel",'{"index":1,"name":"#bench"}');client=app.test_client()
 with client.session_transaction() as s:s["user"]="admin"
 monkeypatch.setattr("dispatch.routes.device_state",lambda db: ({},{"device":"/dev/x"},"Connected"))
 monkeypatch.setattr("dispatch.routes.verify_channel",lambda *a:None)
 monkeypatch.setattr("dispatch.routes.send",lambda *a:"accepted")
 token=client.get("/test-messaging").data.decode().split('name="csrf_token" value="')[1].split('"')[0]
 client.post("/test-messaging",data={"csrf_token":token,"message":"é"*41,"confirm":"yes"})
 assert not db.test_history()
 client.post("/test-messaging",data={"csrf_token":token,"message":"bench","confirm":"yes"})
 assert db.test_history()[0]["outcome"]=="accepted"

def test_test_message_channel_failure_is_separate_history(tmp_path,monkeypatch):
 app=create_app(config(tmp_path));db=app.extensions["db"];db.save_setting("tx_channel",'{"index":1,"name":"#bench"}');client=app.test_client()
 with client.session_transaction() as s:s["user"]="admin"
 monkeypatch.setattr("dispatch.routes.device_state",lambda db: ({},{"device":"/dev/x"},"Connected"))
 monkeypatch.setattr("dispatch.routes.verify_channel",lambda *a: (_ for _ in ()).throw(RadioError("channel changed")))
 token=client.get("/test-messaging").data.decode().split('name="csrf_token" value="')[1].split('"')[0]
 client.post("/test-messaging",data={"csrf_token":token,"message":"bench","confirm":"yes"})
 assert db.test_history()[0]["outcome"]=="failed" and not db.history()

def test_device_name_change_is_explicit_and_persists(tmp_path,monkeypatch):
 app=create_app(config(tmp_path));db=app.extensions["db"];client=app.test_client()
 with client.session_transaction() as s:s["user"]="admin"
 monkeypatch.setattr("dispatch.routes.device_state",lambda db: ({},{"device":"/dev/x"},"Connected"))
 monkeypatch.setattr("dispatch.routes.read_device_name",lambda dev:"WXALERT")
 monkeypatch.setattr("dispatch.routes.discover_channels",lambda d:{"capacity":4,"slots":[]})
 monkeypatch.setattr("dispatch.routes.set_device_name",lambda d,n:n)
 monkeypatch.setattr("dispatch.routes.read_device_name",lambda dev:"WXALERT")
 token=client.get("/settings").data.decode().split('name="csrf_token" value="')[1].split('"')[0]
 response=client.post("/settings/device-name",data={"csrf_token":token,"name":"WXALERT"},follow_redirects=True)
 assert db.setting("device_name")=="WXALERT"
 assert "requested &#39;WXALERT&#39;; radio now reports &#39;WXALERT&#39;" in response.data.decode()

def test_xiao_unknown_channel_replies_are_not_counted_as_occupied_or_empty(monkeypatch):
 class EventType: ERROR="error"; OK="ok"
 class Event:
  def __init__(self,type,payload): self.type=type;self.payload=payload
 class Commands:
  async def send_device_query(self): return Event(EventType.OK,{"max_channels":40})
  async def get_channel(self,index):
   if index==0: return Event(EventType.OK,{"channel_name":"Public","channel_secret":bytes.fromhex("8b3387e9c5cdea6ac9e5edbaa115cd72")})
   if index==1: return Event(EventType.OK,{"channel_name":"#myreach","channel_secret":bytes([1])*16})
   if index==39: return Event(EventType.ERROR,{"code":"not_found"})
   return Event(EventType.OK,{"channel_name":"","channel_secret":bytes(16)})
 class Radio:
  commands=Commands()
  async def disconnect(self): pass
 class MeshCore:
  @staticmethod
  async def create_serial(path): return Radio()
 monkeypatch.setitem(sys.modules,"meshcore",SimpleNamespace(MeshCore=MeshCore,EventType=EventType))
 discovery=asyncio.run(adapter._slots_serial("/dev/fake"))
 assert discovery["capacity"]==40
 assert [slot["state"] for slot in discovery["slots"]].count("occupied")==2
 assert [slot["state"] for slot in discovery["slots"]].count("empty")==37
 assert [slot["state"] for slot in discovery["slots"]].count("unknown")==1
 assert not discovery["slots"][2]["occupied"] and discovery["slots"][2]["known"]
 assert discovery["slots"][39]["state"]=="unknown" and not discovery["slots"][39]["occupied"]

def test_settings_channel_table_hides_slots_and_protects_public_and_tx(tmp_path,monkeypatch):
 app=create_app(config(tmp_path));client=app.test_client()
 with client.session_transaction() as s:s["user"]="admin"
 app.extensions["db"].save_setting("tx_channel",'{"index": 1, "name": "#myreach"}')
 slots=[{"index":0,"name":"Public","occupied":True,"known":True,"state":"occupied"},{"index":1,"name":"#myreach","occupied":True,"known":True,"state":"occupied"},{"index":2,"name":"#owe-wx-test","occupied":True,"known":True,"state":"occupied"}]
 slots.extend({"index":i,"name":"","occupied":False,"known":True,"state":"empty"} for i in range(3,40))
 monkeypatch.setattr("dispatch.routes.device_state",lambda db: ({},{"device":"/dev/x"},"Connected"))
 monkeypatch.setattr("dispatch.routes.discover_channels",lambda dev:{"capacity":40,"slots":slots})
 monkeypatch.setattr("dispatch.routes.read_device_name",lambda dev:"WXALERT")
 page=client.get("/settings").data.decode()
 assert "40</b> total capacity" in page and "3</b> configured" in page and "37</b> available" in page
 assert "Current radio name (read from the connected Companion): <b>WXALERT</b>" in page
 assert 'value="WXALERT"' in page and "#myreach" in page and "#owe-wx-test" in page
 assert "Slot 0" not in page and "Deselect TX channel first" in page and page.count(">Delete</button>")==1
 assert 'name="slot" value="3"' in page and "delete-channel-dialog" in page

def test_channel_delete_requires_server_confirmation_and_stale_data_is_safe(tmp_path,monkeypatch):
 app=create_app(config(tmp_path));db=app.extensions["db"];client=app.test_client()
 with client.session_transaction() as s:s["user"]="admin"
 monkeypatch.setattr("dispatch.routes.device_state",lambda db: ({},{"device":"/dev/x","stable_path":"/dev/by-id/x"},"Connected"))
 monkeypatch.setattr("dispatch.routes.read_device_name",lambda dev:"WXALERT")
 monkeypatch.setattr("dispatch.routes.discover_channels",lambda dev:{"capacity":4,"slots":[]})
 monkeypatch.setattr("dispatch.routes.read_device_name",lambda dev:"WXALERT")
 token=client.get("/settings").data.decode().split('name="csrf_token" value="')[1].split('"')[0]
 called=[];monkeypatch.setattr("dispatch.routes.delete_channel",lambda *a:called.append(a))
 client.post("/settings/channels/delete",data={"csrf_token":token,"slot":"1","name":"#owe-wx-test"})
 assert not called and not db.history()
 monkeypatch.setattr("dispatch.routes.delete_channel",lambda *a: (_ for _ in ()).throw(RadioError("channel changed since confirmation; deletion blocked")))
 client.post("/settings/channels/delete",data={"csrf_token":token,"slot":"1","name":"#owe-wx-test","confirm":"yes"})
 assert not db.history()

def test_channel_table_full_capacity_disables_add(tmp_path,monkeypatch):
 app=create_app(config(tmp_path));client=app.test_client()
 with client.session_transaction() as s:s["user"]="admin"
 slots=[{"index":i,"name":f"#channel-{i}","occupied":True,"known":True,"state":"occupied"} for i in range(4)]
 monkeypatch.setattr("dispatch.routes.device_state",lambda db: ({},{"device":"/dev/x"},"Connected"))
 monkeypatch.setattr("dispatch.routes.discover_channels",lambda dev:{"capacity":4,"slots":slots})
 monkeypatch.setattr("dispatch.routes.read_device_name",lambda dev:"WXALERT")
 page=client.get("/settings").data.decode()
 assert "No confirmed empty slots; Add Channel is disabled." in page and 'action="/settings/channels/add"' not in page

def test_none_serial_session_becomes_useful_radio_error(monkeypatch):
 class MeshCore:
  @staticmethod
  async def create_serial(path): return None
 monkeypatch.setitem(sys.modules,"meshcore",SimpleNamespace(MeshCore=MeshCore))
 try: asyncio.run(adapter._open("/dev/fake"))
 except RadioError as error: assert "usable session" in str(error)
 else: assert False, "a None radio must not reach .commands"

def test_device_name_uses_readback_when_firmware_omits_a_name_limit(monkeypatch):
 state={"name":"OLD"}
 created=[]
 class EventType: ERROR="error"; OK="ok"
 class Event:
  def __init__(self,type,payload=None): self.type=type;self.payload=payload or {}
 class Commands:
  async def send_device_query(self): return Event(EventType.OK,{"max_channels":40})
  async def set_name(self,name): state["name"]=name; return Event(EventType.OK)
  async def send_appstart(self): return Event(EventType.OK,{"name":state["name"]})
 class Radio:
  commands=Commands()
  async def disconnect(self): pass
 class MeshCore:
  @staticmethod
  async def create_serial(path):
   radio=Radio();created.append(radio);return radio
 monkeypatch.setitem(sys.modules,"meshcore",SimpleNamespace(MeshCore=MeshCore,EventType=EventType))
 assert set_device_name({"device":"/dev/fake"},"WXALERT")=="WXALERT"
 assert state["name"]=="WXALERT" and len(created)>=2 # write session, then fresh read-back session

def test_device_name_rejected_by_companion_is_reported(monkeypatch):
 class EventType: ERROR="error"; OK="ok"
 class Event:
  def __init__(self,type,payload=None): self.type=type;self.payload=payload or {}
 class Commands:
  async def send_appstart(self): return Event(EventType.OK,{"name":"W4OWE-BOT"})
  async def send_device_query(self): return Event(EventType.OK,{"max_channels":40})
  async def set_name(self,name): return Event(EventType.ERROR,{"code":"illegal_arg"})
 class Radio:
  commands=Commands()
  async def disconnect(self): pass
 class MeshCore:
  @staticmethod
  async def create_serial(path): return Radio()
 monkeypatch.setitem(sys.modules,"meshcore",SimpleNamespace(MeshCore=MeshCore,EventType=EventType))
 try: set_device_name({"device":"/dev/fake"},"OWE-WX-TEST")
 except RadioError as error: assert "Companion rejected name" in str(error)
 else: assert False, "a rejected set_name command must not be reported as saved"

def test_device_name_readback_mismatch_reports_actual_radio_name(monkeypatch):
 class EventType: ERROR="error"; OK="ok"
 class Event:
  def __init__(self,type,payload=None): self.type=type;self.payload=payload or {}
 class Commands:
  async def send_appstart(self): return Event(EventType.OK,{"name":"W4OWE-BOT"})
  async def send_device_query(self): return Event(EventType.OK,{"max_channels":40})
  async def set_name(self,name): return Event(EventType.OK)
 class Radio:
  commands=Commands()
  async def disconnect(self): pass
 class MeshCore:
  @staticmethod
  async def create_serial(path): return Radio()
 monkeypatch.setitem(sys.modules,"meshcore",SimpleNamespace(MeshCore=MeshCore,EventType=EventType))
 try: set_device_name({"device":"/dev/fake"},"OWE-WX-TEST")
 except RadioError as error:
  assert "W4OWE-BOT" in str(error) and "OWE-WX-TEST" in str(error)
 else: assert False, "an unverified rename must not be reported as saved"

def test_county_catalog_import_validation_and_coverage_persistence(tmp_path):
 app=create_app(config(tmp_path));db=app.extensions["db"]
 rows=normalize_catalog({"features":[{"properties":{"id":"ALC089","name":"Madison"}},{"properties":{"id":"TNC103","name":"Lincoln"}}]})
 db.replace_catalog(rows,{"source":"official-test","retrieved_at":"now","version":"v1"})
 db.save_coverage(["ALC089","TNC103"])
 assert [r["ugc"] for r in db.coverage()]==["ALC089","TNC103"] and db.catalog_meta()["version"]=="v1"
 try: db.replace_catalog([],{})
 except ValueError: pass
 else: assert False
 assert len(db.catalog())==2 and eligible({"properties":{"event":"Tornado Warning","geocode":{"UGC":["TNC103"]}}},app.config)

def test_coverage_page_import_state_filter_and_fail_closed_save(tmp_path,monkeypatch):
 cfg=config(tmp_path);cfg["states"]=["AL","TN"];app=create_app(cfg);db=app.extensions["db"];client=app.test_client()
 with client.session_transaction() as s:s["user"]="admin"
 rows=normalize_catalog({"features":[{"properties":{"id":"ALC089","name":"Madison"}},{"properties":{"id":"TNC103","name":"Lincoln"}}]})
 monkeypatch.setattr("dispatch.routes.fetch_catalog",lambda ua:(rows,{"source":"official","retrieved_at":"today","version":"v1"}))
 token=client.get("/settings/coverage-counties").data.decode().split('name="csrf_token" value="')[1].split('"')[0]
 client.post("/settings/coverage-counties",data={"csrf_token":token,"action":"import"})
 page=client.get("/settings/coverage-counties?state=TN").data.decode();assert 'value="TNC103"' in page and "Lincoln, TN" in page
 client.post("/settings/coverage-counties",data={"csrf_token":token,"action":"save","county":["ALC089","TNC103"]})
 assert {r["ugc"] for r in db.coverage()}=={"ALC089","TNC103"}
 failed=client.post("/settings/coverage-counties",data={"csrf_token":token,"action":"save","return_state":"TN","return_query":"Lincoln"})
 assert {r["ugc"] for r in db.coverage()}=={"ALC089","TNC103"}
 failed_page=failed.data.decode()
 assert "Coverage was not changed" in failed_page and 'value="TN" selected' in failed_page
 assert 'const saved=new Set(' in failed_page and "ALC089" in failed_page and "TNC103" in failed_page

def test_coverage_selector_preserves_cross_state_pending_choices_client_side(tmp_path,monkeypatch):
 cfg=config(tmp_path);cfg["states"]=["AL","TN"];app=create_app(cfg);db=app.extensions["db"];client=app.test_client()
 with client.session_transaction() as s:s["user"]="admin"
 rows=normalize_catalog({"features":[{"properties":{"id":"ALC089","name":"Madison"}},{"properties":{"id":"TNC103","name":"Lincoln"}}]})
 db.replace_catalog(rows,{"source":"test","retrieved_at":"now","version":"v1"});db.save_coverage(["ALC089","TNC103"])
 page=client.get("/settings/coverage-counties?state=TN").data.decode()
 assert 'id="county-state"' in page and 'class="county-option" data-state="AL"' in page and 'class="county-option" data-state="TN"' in page
 assert 'value="ALC089" checked' in page and 'value="TNC103" checked' in page
 assert "state.addEventListener('change',update)" in page and "search.addEventListener('input',update)" in page
 assert "Clear filters" in page and "pending change" in page and 'method="get"' not in page
 assert 'class="county-option"' in page and 'name="action" value="save"' in page
 token=page.split('name="csrf_token" value="')[1].split('"')[0]
 response=client.post("/settings/coverage-counties",data={"csrf_token":token,"action":"save","county":["ALC089","TNC103"],"return_state":"TN","return_query":"Lincoln"})
 assert "state=TN" in response.headers["Location"] and "q=Lincoln" in response.headers["Location"]
 after=client.get(response.headers["Location"]).data.decode()
 assert 'value="TN" selected' in after and 'id="county-search" type="search" value="Lincoln"' in after

def test_coverage_page_explains_catalog_storage_and_automatic_filters(tmp_path):
 app=create_app(config(tmp_path));client=app.test_client()
 with client.session_transaction() as s:s["user"]="admin"
 before=client.get("/settings/coverage-counties").data.decode()
 assert "Import catalog first. The official NWS county catalog is stored locally" in before
 assert "Coverage is stored locally and is fail-closed" not in before
 assert 'class="catalog-initial-import"' in before and 'class="coverage-action"' in before
 rows=normalize_catalog({"features":[{"properties":{"id":"ALC089","name":"Madison"}}]})
 app.extensions["db"].replace_catalog(rows,{"source":"official","retrieved_at":"today","version":"v1"})
 after=client.get("/settings/coverage-counties").data.decode()
 assert "Catalog imported and stored locally. Re-import only to refresh the county list." in after
 assert "Filters apply automatically." in after and 'method="get"' not in after
 assert "Advanced / Refresh Catalog" in after and "Source: official · imported today" in after

def test_catalog_refresh_preserves_selected_coverage(tmp_path,monkeypatch):
 app=create_app(config(tmp_path));db=app.extensions["db"];client=app.test_client()
 with client.session_transaction() as s:s["user"]="admin"
 first=normalize_catalog({"features":[{"properties":{"id":"ALC089","name":"Madison"}}]})
 db.replace_catalog(first,{"source":"old","retrieved_at":"before","version":"v1"});db.save_coverage(["ALC089"])
 refreshed=normalize_catalog({"features":[{"properties":{"id":"ALC089","name":"Madison"}},{"properties":{"id":"ALC083","name":"Limestone"}}]})
 monkeypatch.setattr("dispatch.routes.fetch_catalog",lambda ua:(refreshed,{"source":"official","retrieved_at":"after","version":"v2"}))
 token=client.get("/settings/coverage-counties").data.decode().split('name="csrf_token" value="')[1].split('"')[0]
 client.post("/settings/coverage-counties",data={"csrf_token":token,"action":"import"})
 assert [row["ugc"] for row in db.coverage()]==["ALC089"] and db.catalog_meta()["version"]=="v2"

def test_transmission_setting_is_audited_and_never_opens_radio(tmp_path,monkeypatch):
 app=create_app(config(tmp_path));client=app.test_client()
 with client.session_transaction() as s:s["user_id"]=app.extensions["db"].user("admin")["id"]
 monkeypatch.setattr("dispatch.routes.send",lambda *args: (_ for _ in ()).throw(AssertionError("settings must not send")))
 client.post("/settings/transmission",data={"csrf_token":csrf_token(client),"transmit_enabled":"yes"})
 assert app.extensions["db"].setting("transmit_enabled")=="true"
 assert app.extensions["db"].history()[0]["action"]=="transmission_setting_changed"

def test_route_rechecks_manual_permission_after_signed_preview(tmp_path,monkeypatch):
 cfg=config(tmp_path);app=create_app(cfg);ingest(app,[feature()]);db=app.extensions["db"];client=app.test_client()
 with client.session_transaction() as s:s["user"]="admin"
 db.save_setting("tx_channel",'{"index":1,"name":"#bench"}');db.save_setting("transmit_enabled","true")
 monkeypatch.setattr("dispatch.routes.device_state",lambda db: ({},{"device":"/dev/fake"},"Connected"));monkeypatch.setattr("dispatch.routes.verify_channel",lambda *args:None)
 opened=[]
 async def opened_send(*args): opened.append(args);return "accepted"
 monkeypatch.setattr("dispatch.meshcore_adapter._send_serial",opened_send)
 token=csrf_token(client);preview=alert_preview(client,"new",token).get_json()
 db.save_setting("transmit_enabled","false")
 client.post("/alerts/new/send",data={"csrf_token":token,"confirm":"yes","comment":"","preview_token":preview["token"]})
 assert not opened and db.get("new")["status"]=="failed"
 assert db.history()[0]["action"]=="failed"

def test_startup_poll_test_alert_and_settings_never_open_radio(tmp_path,monkeypatch):
 """Only an explicitly confirmed dispatch may reach the serial adapter."""
 cfg=config(tmp_path);app=create_app(cfg);db=app.extensions["db"];client=app.test_client()
 monkeypatch.setattr("dispatch.meshcore_adapter._send_serial",lambda *args: (_ for _ in ()).throw(AssertionError("radio opened")))
 class Response:
  def raise_for_status(self): pass
  def json(self): return {"features":[]}
 monkeypatch.setattr("dispatch.poller.requests.get",lambda *args,**kwargs: Response())
 fetch(app)
 with client.session_transaction() as s:s["user_id"]=db.user("admin")["id"]
 client.post("/settings/transmission",data={"csrf_token":csrf_token(client),"transmit_enabled":"yes"})
 choice=coverage_counties(app.config)[0]
 token=client.get("/test-alerts").data.decode().split('name="csrf_token" value="')[1].split('"')[0]
 client.post("/test-alerts",data={"csrf_token":token,"event":"Tornado Warning","county":choice["id"]})
 assert db.actionable() and db.setting("transmit_enabled")=="true"
