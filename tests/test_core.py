import asyncio
import sys
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from dispatch import create_app
from dispatch.messages import append_comment, truncate_utf8
from dispatch.devices import resolve_device
from dispatch.poller import ingest, fetch, eligible, cap_reference_ids
import dispatch.meshcore_adapter as adapter
from dispatch.meshcore_adapter import RadioError, verify_channel, hashtag_secret, validate_hashtag, set_device_name
from dispatch.timefmt import local_time

def config(tmp_path):
 return {"TESTING":True,"secret_key":"test","database":str(tmp_path/"test.sqlite"),"admin_username":"admin","admin_password_hash":"scrypt:32768:8:1$X$C8YciwLCAazhj6PeXTvoUHl0LTB7qm4JzB47lqYsOiwD9sEzKJzQx9QHdT1yTQ","nws_user_agent":"tests@example.invalid","states":["AL"],"alert_types":["Tornado Warning"],"counties":[],"max_message_bytes":80,"dry_run":True,"transmit_enabled":False,"poll_seconds":60,"start_poller":False}

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

def test_dry_run_send_is_audited(tmp_path,monkeypatch):
 app=create_app(config(tmp_path)); ingest(app,[feature()]); client=app.test_client()
 with client.session_transaction() as s: s["user"]="admin"
 app.extensions["db"].save_setting("tx_channel",'{"index": 2, "name": "#wx-test"}')
 token=client.get("/").data.decode().split('name="csrf_token" value="')[1].split('"')[0]
 monkeypatch.setattr("dispatch.routes.device_state",lambda db: ({}, {"device":"/dev/ttyACM0"}, "Connected"))
 monkeypatch.setattr("dispatch.routes.verify_channel",lambda dev, channel: channel)
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
 monkeypatch.setattr("dispatch.routes.discover_channels",lambda dev:{"capacity":4,"slots":[{"index":2,"name":"#wx-northal","occupied":True,"known":True}]})
 monkeypatch.setattr("dispatch.routes.list_channels",lambda dev:[{"index":2,"name":"#wx-northal"}])
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
 monkeypatch.setattr("dispatch.routes.discover_channels",lambda dev: (_ for _ in ()).throw(RadioError("serial permission denied")))
 page=client.get("/settings").data.decode(); assert "serial permission denied" in page
 monkeypatch.setattr("dispatch.routes.scan_devices",lambda:[])
 assert "options_html" in client.get("/api/devices").get_json()

def test_local_time_has_zone_abbreviation():
 assert local_time("2026-01-01T18:30:00+00:00","America/Chicago").endswith("CST")

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
 monkeypatch.setattr("dispatch.routes.discover_channels",lambda dev:{"capacity":4,"slots":[{"index":2,"name":"","occupied":False,"known":True}]})
 monkeypatch.setattr("dispatch.routes.add_hashtag_channel",lambda d,s,n:{"index":s,"name":n})
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
 monkeypatch.setattr("dispatch.routes.discover_channels",lambda dev:{"capacity":4,"slots":[]})
 token=client.get("/settings").data.decode().split('name="csrf_token" value="')[1].split('"')[0]
 called=[];monkeypatch.setattr("dispatch.routes.delete_channel",lambda *a:called.append(a))
 client.post("/settings/channels/delete",data={"csrf_token":token,"slot":"2","name":"#wx","confirm":"yes"})
 assert not called
 db.save_setting("tx_channel","null")
 client.post("/settings/channels/delete",data={"csrf_token":token,"slot":"3","name":"#other","confirm":"yes"})
 assert called and db.history()[0]["action"]=="channel_deleted"

def test_manual_test_message_simulated_and_utf8_limit(tmp_path,monkeypatch):
 app=create_app(config(tmp_path));db=app.extensions["db"];db.save_setting("tx_channel",'{"index":1,"name":"#bench"}');client=app.test_client()
 with client.session_transaction() as s:s["user"]="admin"
 monkeypatch.setattr("dispatch.routes.device_state",lambda db: ({},{"device":"/dev/x"},"Connected"))
 monkeypatch.setattr("dispatch.routes.verify_channel",lambda *a:None)
 monkeypatch.setattr("dispatch.routes.send",lambda *a:"dry-run: no serial connection or RF transmission attempted")
 token=client.get("/test-messaging").data.decode().split('name="csrf_token" value="')[1].split('"')[0]
 client.post("/test-messaging",data={"csrf_token":token,"message":"é"*41,"confirm":"yes"})
 assert not db.test_history()
 client.post("/test-messaging",data={"csrf_token":token,"message":"bench","confirm":"yes"})
 assert db.test_history()[0]["outcome"]=="simulated"

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
 monkeypatch.setattr("dispatch.routes.discover_channels",lambda d:{"capacity":4,"slots":[]})
 monkeypatch.setattr("dispatch.routes.set_device_name",lambda d,n:n)
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

def test_settings_shows_radio_name_slot_states_and_delete_for_non_public(tmp_path,monkeypatch):
 app=create_app(config(tmp_path));client=app.test_client()
 with client.session_transaction() as s:s["user"]="admin"
 slots=[{"index":0,"name":"Public","occupied":True,"known":True,"state":"occupied"},{"index":1,"name":"#myreach","occupied":True,"known":True,"state":"occupied"}]
 slots.extend({"index":i,"name":"","occupied":False,"known":False,"state":"unknown"} for i in range(2,40))
 monkeypatch.setattr("dispatch.routes.device_state",lambda db: ({},{"device":"/dev/x"},"Connected"))
 monkeypatch.setattr("dispatch.routes.discover_channels",lambda dev:{"capacity":40,"slots":slots})
 monkeypatch.setattr("dispatch.routes.read_device_name",lambda dev:"WXALERT")
 page=client.get("/settings").data.decode()
 assert "2 occupied, 0 confirmed available, 38 unknown" in page
 assert "Current radio name (read from the connected Companion): <b>WXALERT</b>" in page
 assert 'value="WXALERT"' in page and "unknown (not eligible for changes)" in page
 assert page.count(">Delete</button>")==1 and "Confirm delete #myreach (slot 1)" in page

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
