from __future__ import annotations
import json, os, requests
from uuid import uuid4
from datetime import datetime, timezone
from functools import wraps
from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for, jsonify
from werkzeug.security import check_password_hash
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from .devices import scan_devices, resolve_device
from .messages import append_comment, radio_message, MessageError
from .meshcore_adapter import send, verify_connection, verify_channel, list_channels, discover_channels, add_hashtag_channel, delete_channel, read_device_name, set_device_name, message_limit, RadioError
from .timefmt import local_time, valid_timezones, system_timezone
from .poller import ingest, eligible, coverage_counties
from .test_alerts import build_test_alert, TestAlertError
from .coverage import fetch_catalog, CatalogError

bp=Blueprint("console",__name__)
def admin(fn):
 @wraps(fn)
 def wrapped(*a,**kw):
  if not session.get("user"): return redirect(url_for("console.login"))
  return fn(*a,**kw)
 return wrapped
def saved_channel(db): return json.loads(db.setting("tx_channel") or "null")
def app_timezone(db): return db.setting("timezone") or current_app.config.get("timezone") or system_timezone()
def device_state(db):
 saved=json.loads(db.setting("device") or "null"); dev,status=resolve_device(saved,scan_devices()); return saved,dev,status
def feed_state(db):
 last=db.setting("nws_last_success"); error=db.setting("nws_last_error"); stale=True
 if last:
  try: stale=(datetime.now(timezone.utc)-datetime.fromisoformat(last)).total_seconds()>current_app.config.get("poll_seconds",60)*3
  except ValueError: pass
 return last,error,"stale" if stale else ("error" if error else "healthy")
def alert_actionability_error(alert):
 if not alert or alert["status"]!="pending": return "alert is no longer actionable"
 expires=alert["expires"]
 if expires:
  try:
   if datetime.fromisoformat(expires).astimezone(timezone.utc)<=datetime.now(timezone.utc): return "alert has expired"
  except (TypeError,ValueError): return "alert expiration is invalid; refusing to dispatch"
 return None
def final_alert_message(alert, comment=""):
 return append_comment(radio_message(json.loads(alert["raw_json"]),app_timezone(current_app.extensions["db"])),comment,current_app.config.get("max_message_bytes",180))
def preview_signer(): return URLSafeTimedSerializer(current_app.config["SECRET_KEY"],salt="dispatch-preview-v1")

@bp.route("/login",methods=["GET","POST"])
def login():
 if request.method=="POST":
  if request.form.get("username")==current_app.config["admin_username"] and check_password_hash(current_app.config["admin_password_hash"],request.form.get("password", "")):
   session.clear();session["user"]=request.form["username"];return redirect(url_for("console.index"))
  flash("Invalid credentials")
 return render_template("login.html")
@bp.post("/logout")
@admin
def logout(): session.clear();return redirect(url_for("console.login"))
@bp.get("/")
@admin
def index():
 db=current_app.extensions["db"];last,error,health=feed_state(db)
 return render_template("index.html",alerts=db.actionable(),history=db.history(),cap=message_limit(current_app.config),last_sync=last,feed_error=error,feed_health=health,tx_channel=saved_channel(db),device_name=db.setting("device_name") or "unknown")
@bp.get("/api/state")
@admin
def state():
 db=current_app.extensions["db"];alerts=db.actionable();history=db.history();last,error,health=feed_state(db)
 return jsonify(alerts_html=render_template("_alerts.html",alerts=alerts,cap=current_app.config.get("max_message_bytes",180),tx_channel=saved_channel(db)),history_html=render_template("_history.html",history=history),nws_last_success=last,nws_last_success_display=local_time(last,app_timezone(db)),nws_last_error=error,feed_health=health)
@bp.post("/alerts/<cap_id>/ignore")
@admin
def ignore(cap_id):
 db=current_app.extensions["db"]
 if request.form.get("confirm")!="yes": flash("No change: explicit confirmation is required.");return redirect(url_for("console.index"))
 try: preview=preview_signer().loads(request.form.get("ignore_token",""),max_age=300)
 except (SignatureExpired,BadSignature): flash("No change: ignore confirmation expired or is invalid.");return redirect(url_for("console.index"))
 alert=db.get(cap_id)
 if preview.get("action")!="ignore" or preview.get("cap_id")!=cap_id or not alert or alert["status"]!="pending" or preview.get("updated_at")!=alert["updated_at"]: flash("No change: warning changed; review it again.");return redirect(url_for("console.index"))
 if not db.consume_preview(preview.get("jti", "")): flash("No change: this confirmation was already used; review the warning again.");return redirect(url_for("console.index"))
 if db.claim(cap_id,session["user"]): db.finish(cap_id,"ignored",session["user"])
 else: flash("Alert is no longer actionable.")
 return redirect(url_for("console.index"))
@bp.post("/alerts/<cap_id>/ignore-preview")
@admin
def preview_ignore(cap_id):
 db=current_app.extensions["db"];alert=db.get(cap_id)
 if error:=alert_actionability_error(alert): return jsonify(error=error),409
 return jsonify(token=preview_signer().dumps({"jti":uuid4().hex,"action":"ignore","cap_id":cap_id,"updated_at":alert["updated_at"]}))
@bp.post("/alerts/<cap_id>/preview")
@admin
def preview_transmit(cap_id):
 alert=current_app.extensions["db"].get(cap_id)
 if error:=alert_actionability_error(alert): return jsonify(error=error),409
 try:
  comment=request.form.get("comment","");message=final_alert_message(alert,comment);channel=saved_channel(current_app.extensions["db"])
  if not channel: return jsonify(error="no transmit channel selected"),409
  payload={"jti":uuid4().hex,"cap_id":cap_id,"updated_at":alert["updated_at"],"message":message,"comment":comment,"channel":channel,"cap":message_limit(current_app.config)}
  token=preview_signer().dumps(payload);return jsonify(message=message,bytes=len(message.encode("utf-8")),cap=message_limit(current_app.config),token=token,channel=channel,test_only=bool(alert["test_only"]),event=alert["event"],areas=alert["areas"],expires=local_time(alert["expires"],app_timezone(current_app.extensions["db"])))
 except MessageError as exc:return jsonify(error=str(exc)),400
@bp.post("/alerts/<cap_id>/send")
@admin
def transmit(cap_id):
 db=current_app.extensions["db"];actor=session["user"];_,dev,status=device_state(db);channel=saved_channel(db)
 if request.form.get("confirm")!="yes": flash("No transmission: explicit confirmation is required.");return redirect(url_for("console.index"))
 try: preview=preview_signer().loads(request.form.get("preview_token",""),max_age=300)
 except (SignatureExpired,BadSignature): flash("No transmission: preview confirmation expired or is invalid.");return redirect(url_for("console.index"))
 alert=db.get(cap_id)
 if preview.get("cap_id")!=cap_id or alert_actionability_error(alert) or preview.get("updated_at")!=alert["updated_at"] or preview.get("channel")!=channel or preview.get("cap")!=message_limit(current_app.config): flash("No transmission: warning, transmit channel, or message limit changed; review it again.");return redirect(url_for("console.index"))
 try: message=final_alert_message(alert,request.form.get("comment",""))
 except MessageError as exc: flash(f"No transmission: {exc}");return redirect(url_for("console.index"))
 if preview.get("comment")!=request.form.get("comment","") or preview.get("message")!=message: flash("No transmission: message changed; review it again.");return redirect(url_for("console.index"))
 if not db.consume_preview(preview.get("jti", "")): flash("No transmission: this confirmation was already used; review the warning again.");return redirect(url_for("console.index"))
 try: verify_channel(dev,channel)
 except RadioError as exc: flash(f"No transmission: {status}. {exc}");return redirect(url_for("console.index"))
 if not db.claim(cap_id,actor): flash("Alert is no longer actionable.");return redirect(url_for("console.index"))
 try:
  detail=send(current_app.config,dev,channel,message);db.finish(cap_id,"sent",actor,detail,message);flash("Dispatch recorded. Radio acceptance is not proof of mesh delivery.")
 except RadioError as exc: db.finish(cap_id,"failed",actor,str(exc),message);flash(f"No transmission: {exc}")
 return redirect(url_for("console.index"))
@bp.route("/settings",methods=["GET","POST"])
@admin
def settings():
 db=current_app.extensions["db"]
 if request.method=="POST":
  value=request.form.get("device");db.save_setting("device",value or "null");db.save_setting("tx_channel","null")
  flash("Device selection saved. Transmit channel cleared and must be selected for this device.");return redirect(url_for("console.settings"))
 saved,dev,status=device_state(db);channels=[];channel_error=None;discovery=None;radio_name=None;name_error=None;cached_radio_name=db.setting("device_name") or ""
 if dev:
  try: discovery=discover_channels(dev);channels=[{"index":x["index"],"name":x["name"]} for x in discovery["slots"] if x["name"]]
  except RadioError as exc: channel_error=str(exc)
  try: radio_name=read_device_name(dev);db.save_setting("device_name",radio_name)
  except RadioError as exc: name_error=str(exc)
 return render_template("settings.html",devices=scan_devices(),saved=saved,status=status,tx_enabled=bool(current_app.config.get("transmit_enabled") and os.environ.get("MESHCORE_SEND_ENABLED")=="1"),channels=channels,discovery=discovery,tx_channel=saved_channel(db),channel_error=channel_error,radio_name=radio_name,cached_radio_name=cached_radio_name,name_error=name_error,timezones=valid_timezones(),timezone=app_timezone(db))
@bp.route("/settings/coverage-counties",methods=["GET","POST"])
@admin
def coverage_counties_page():
 db=current_app.extensions["db"]
 if request.method=="POST":
  action=request.form.get("action")
  try:
   if action=="import":
    rows,meta=fetch_catalog(current_app.config["nws_user_agent"]);db.replace_catalog(rows,meta);db.audit(None,"county_catalog_imported",session["user"],f"{len(rows)} counties; {meta['source']}");flash("County catalog imported. Coverage was not changed.")
   elif action=="save":
    selected=request.form.getlist("county")
    if len(selected)>25 and request.form.get("confirm_expand")!="yes": raise CatalogError("confirm this coverage expansion before saving")
    db.save_coverage(selected,{str(x).upper() for x in current_app.config.get("states",[])});db.audit(None,"coverage_counties_saved",session["user"],f"{len(selected)} counties selected");flash("Coverage counties saved. New NWS polls and Test Alerts use them immediately.")
  except (CatalogError,ValueError,requests.RequestException) as exc: flash(f"Coverage was not changed: {exc}")
  return redirect(url_for("console.coverage_counties_page"))
 state=request.args.get("state","");query=request.args.get("q","");selected={r["ugc"] for r in db.coverage()}
 catalog=db.catalog(state,query)
 return render_template("coverage_counties.html",catalog=catalog,visible={r["ugc"] for r in catalog},states=db.catalog_states(),state=state,query=query,selected=selected,coverage=db.coverage(),meta=db.catalog_meta())
@bp.get("/api/devices")
@admin
def devices_api():
 db=current_app.extensions["db"];saved=json.loads(db.setting("device") or "null")
 return jsonify(options_html=render_template("_device_options.html",devices=scan_devices(),saved=saved))
@bp.post("/settings/discover")
@admin
def discover_unsaved():
 try:
  requested=json.loads(request.form.get("device",""));device=next((d for d in scan_devices() if d["device"]==requested.get("device") and d.get("serial_number")==requested.get("serial_number")),None)
  if not device: raise RadioError("select a currently detected USB device")
  return jsonify(discovery=discover_channels(device),error=None)
 except (ValueError,TypeError,RadioError) as exc: return jsonify(discovery=None,error=str(exc)),400
@bp.post("/settings/timezone")
@admin
def select_timezone():
 db=current_app.extensions["db"];name=request.form.get("timezone","")
 if name not in valid_timezones(): flash("Time zone was not saved: invalid IANA time zone.")
 else: db.save_setting("timezone",name);flash(f"Time zone saved: {name}.")
 return redirect(url_for("console.settings"))
@bp.post("/settings/channel")
@admin
def select_channel():
 db=current_app.extensions["db"];_,dev,status=device_state(db)
 try:
  index=int(request.form.get("channel_index","-1"));channels=list_channels(dev);selected=next((c for c in channels if c["index"]==index),None)
  if not selected: raise RadioError("selected channel is unavailable; rescan the connected Companion")
  db.save_setting("tx_channel",json.dumps(selected));db.audit(None,"tx_channel_selected",session["user"],f"{selected['name']} (slot {selected['index']})");flash(f"Transmit channel saved: {selected['name']}.")
 except (ValueError,RadioError) as exc: flash(f"Channel not saved: {status}. {exc}")
 return redirect(url_for("console.settings"))
@bp.post("/settings/channels/add")
@admin
def add_channel():
 db=current_app.extensions["db"];_,dev,status=device_state(db)
 try:
  if request.form.get("confirm") != "yes": raise RadioError("explicit confirmation is required")
  created=add_hashtag_channel(dev,int(request.form.get("slot","-1")),request.form.get("name",""))
  db.audit(None,"channel_added",session["user"],f"{created['name']} (slot {created['index']})")
  # Existing saved TX choice must be rechecked after any configuration change.
  db.save_setting("tx_channel","null");flash(f"Added {created['name']} in slot {created['index']}. Re-select transmit channel before sending.")
 except (ValueError,RadioError) as exc: flash(f"Channel was not added: {status}. {exc}")
 return redirect(url_for("console.settings"))
@bp.post("/settings/channels/delete")
@admin
def remove_channel():
 db=current_app.extensions["db"];_,dev,status=device_state(db);selected=saved_channel(db)
 try:
  slot=int(request.form.get("slot","-1"));name=request.form.get("name","")
  if request.form.get("confirm")!="yes": raise RadioError("explicit confirmation is required")
  # Protect the built-in channel before delegating to the radio adapter too.
  # This keeps a crafted HTTP request from reaching any future adapter path.
  if name.strip().casefold() in {"public","#public"}: raise RadioError("Public channel is protected from deletion")
  if selected and selected.get("index")==slot: raise RadioError("deselect the current transmit channel before deleting it")
  delete_channel(dev,slot,name);db.audit(None,"channel_deleted",session["user"],f"device={dev.get('stable_path') or dev.get('device')}; channel={name}; slot={slot}");flash(f"Deleted {name} from slot {slot}.")
 except (ValueError,RadioError) as exc: flash(f"Channel was not deleted: {status}. {exc}")
 return redirect(url_for("console.settings"))
@bp.post("/settings/device-name")
@admin
def device_name():
 db=current_app.extensions["db"];_,dev,status=device_state(db)
 try:
  requested=request.form.get("name","");name=set_device_name(dev,requested);db.save_setting("device_name",name);flash(f"Companion device name verified: requested {requested!r}; radio now reports {name!r}.")
 except RadioError as exc: flash(f"Device name not saved: {status}. {exc}")
 return redirect(url_for("console.settings"))
@bp.route("/test-messaging",methods=["GET","POST"])
@admin
def test_messaging():
 db=current_app.extensions["db"];channel=saved_channel(db);_,dev,status=device_state(db)
 if request.method=="POST":
  message=request.form.get("message","")
  if not message or len(message.encode("utf-8"))>message_limit(current_app.config): flash("Test message rejected: empty or exceeds byte limit.")
  elif request.form.get("confirm")!="yes": flash("Test message not sent: confirmation required.")
  else:
   try:
    verify_channel(dev,channel);detail=send(current_app.config,dev,channel,message);outcome="simulated" if current_app.config.get("dry_run") else "accepted";db.test_message(session["user"],channel,message,outcome,detail);flash(f"Test result: {outcome}. {detail}")
   except RadioError as exc: db.test_message(session["user"],channel or {"name":"none","index":-1},message,"failed",str(exc));flash(f"Test message failed: {status}. {exc}")
 return render_template("test_messaging.html",channel=channel,device_name=db.setting("device_name") or "unknown",status=status,cap=message_limit(current_app.config),history=db.test_history(),enabled=bool(channel and dev and current_app.config.get("transmit_enabled") and os.environ.get("MESHCORE_SEND_ENABLED")=="1"))
@bp.route("/test-alerts",methods=["GET","POST"])
@admin
def test_alerts():
 db=current_app.extensions["db"]; counties=coverage_counties(current_app.config)
 if request.method=="POST":
  try:
   feature=build_test_alert(current_app.config,request.form.getlist("county"),request.form.get("event",""))
   # Reuse the real eligibility, shortening, expiry, and manual-dispatch path.
   # This route never contacts NWS or the Companion.
   if not eligible(feature,current_app.config): raise TestAlertError("synthetic warning does not match configured coverage")
   ingest(current_app,[feature]); db.audit(feature["properties"]["id"],"test_alert_created",session["user"],"TEST ONLY synthetic warning created")
   flash("TEST ONLY warning created. It is awaiting the normal manual Send to Mesh or Ignore decision.")
   return redirect(url_for("console.index"))
  except TestAlertError as exc: flash(f"TEST ONLY warning was not created: {exc}")
 return render_template("test_alerts.html",counties=counties,alert_types=current_app.config.get("alert_types",[]))
@bp.post("/settings/test")
@admin
def test_device():
 db=current_app.extensions["db"];_,dev,status=device_state(db)
 try: flash(status+". "+verify_connection(current_app.config,dev))
 except RadioError as exc: flash(status+". "+str(exc))
 return redirect(url_for("console.settings"))
