from __future__ import annotations
import json
from datetime import datetime, timezone
from functools import wraps
from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for, jsonify
from werkzeug.security import check_password_hash
from .devices import scan_devices, resolve_device
from .messages import append_comment
from .meshcore_adapter import send, verify_connection, verify_channel, list_channels, discover_channels, add_hashtag_channel, delete_channel, read_device_name, set_device_name, message_limit, RadioError
from .timefmt import local_time, valid_timezones, system_timezone

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
 if db.claim(cap_id,session["user"]): db.finish(cap_id,"ignored",session["user"])
 else: flash("Alert is no longer actionable.")
 return redirect(url_for("console.index"))
@bp.post("/alerts/<cap_id>/send")
@admin
def transmit(cap_id):
 db=current_app.extensions["db"];actor=session["user"];_,dev,status=device_state(db);channel=saved_channel(db)
 try: verify_channel(dev,channel)
 except RadioError as exc: flash(f"No transmission: {status}. {exc}");return redirect(url_for("console.index"))
 if not db.claim(cap_id,actor): flash("Alert is no longer actionable.");return redirect(url_for("console.index"))
 alert=db.get(cap_id);message=append_comment(alert["message"],request.form.get("comment",""),current_app.config.get("max_message_bytes",180))
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
 saved,dev,status=device_state(db);channels=[];channel_error=None;discovery=None;radio_name=db.setting("device_name") or ""
 if dev:
  try: discovery=discover_channels(dev);channels=[{"index":x["index"],"name":x["name"]} for x in discovery["slots"] if x["name"]]
  except RadioError as exc: channel_error=str(exc)
  try: radio_name=read_device_name(dev);db.save_setting("device_name",radio_name)
  except RadioError as exc: channel_error=channel_error or f"Device-name read error: {exc}"
 return render_template("settings.html",devices=scan_devices(),saved=saved,status=status,channels=channels,discovery=discovery,tx_channel=saved_channel(db),channel_error=channel_error,radio_name=radio_name,timezones=valid_timezones(),timezone=app_timezone(db))
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
  if selected and selected.get("index")==slot: raise RadioError("deselect the current transmit channel before deleting it")
  delete_channel(dev,slot,name);db.audit(None,"channel_deleted",session["user"],f"device={dev.get('stable_path') or dev.get('device')}; channel={name}; slot={slot}");flash(f"Deleted {name} from slot {slot}.")
 except (ValueError,RadioError) as exc: flash(f"Channel was not deleted: {status}. {exc}")
 return redirect(url_for("console.settings"))
@bp.post("/settings/device-name")
@admin
def device_name():
 db=current_app.extensions["db"];_,dev,status=device_state(db)
 try:
  name=set_device_name(dev,request.form.get("name",""));db.save_setting("device_name",name);flash(f"Companion device name saved: {name}.")
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
 return render_template("test_messaging.html",channel=channel,device_name=db.setting("device_name") or "unknown",status=status,cap=message_limit(current_app.config),history=db.test_history(),enabled=bool(channel and dev and current_app.config.get("transmit_enabled") and __import__('os').environ.get("MESHCORE_SEND_ENABLED")=="1"))
@bp.post("/settings/test")
@admin
def test_device():
 db=current_app.extensions["db"];_,dev,status=device_state(db)
 try: flash(status+". "+verify_connection(current_app.config,dev))
 except RadioError as exc: flash(status+". "+str(exc))
 return redirect(url_for("console.settings"))
