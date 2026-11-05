from __future__ import annotations
import json, requests
from uuid import uuid4
from datetime import datetime, timezone
from functools import wraps
from flask import Blueprint, current_app, flash, g, redirect, render_template, request, session, url_for, jsonify
from werkzeug.security import check_password_hash, generate_password_hash
from .auth import validate_password
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from .devices import scan_devices, resolve_device
from .messages import append_comment, radio_message, MessageError
from .meshcore_adapter import send, verify_connection, verify_channel, list_channels, discover_channels, add_hashtag_channel, delete_channel, read_device_name, set_device_name, message_limit, RadioError
from .timefmt import local_time, valid_timezones, system_timezone
from .poller import ingest, eligible, coverage_counties
from .test_alerts import build_test_alert, TestAlertError
from .coverage import fetch_catalog, CatalogError
from .runtime import runtime_config, transmissions_enabled, contact_email, states
from .alert_types import DEFAULT_ALERT_TYPES, category as alert_type_category, fetch_event_types, AlertTypeCatalogError
from .updates import installed as installed_update, available as available_update, status as update_status, launch as launch_update

bp=Blueprint("console",__name__)
def admin(fn):
 @wraps(fn)
 def wrapped(*a,**kw):
  user_id=session.get("user_id")
  # Test fixtures from earlier releases used a username-only session.  Never
  # accept that format in production: it could bind a stale session to a newly
  # recreated username.  Real sessions always carry the immutable SQLite ID.
  if user_id is None and current_app.config.get("TESTING"):
   legacy=current_app.extensions["db"].user(session.get("user"),active_only=True)
   user_id=legacy["id"] if legacy else None
  user=current_app.extensions["db"].user_by_id(user_id,active_only=True) if user_id is not None else None
  if not user:
   session.clear();flash("Sign in with an active account.");return redirect(url_for("console.login"))
  g.current_user=user
  return fn(*a,**kw)
 return wrapped
def actor(): return g.current_user["username"]
def saved_channel(db): return json.loads(db.setting("tx_channel") or "null")
def app_timezone(db): return db.setting("timezone") or current_app.config.get("timezone") or system_timezone()
def device_state(db):
 saved=json.loads(db.setting("device") or "null"); dev,status=resolve_device(saved,scan_devices()); return saved,dev,status
def feed_state(db):
 last=db.setting("nws_last_success"); error=db.setting("nws_last_error"); stale=True
 if last:
  try: stale=(datetime.now(timezone.utc)-datetime.fromisoformat(last)).total_seconds()>runtime_config(current_app.config,db)["poll_seconds"]*3
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
def reopened_alert_message(alert):
 return append_comment(radio_message(json.loads(alert["raw_json"]),app_timezone(current_app.extensions["db"])),"",current_app.config.get("max_message_bytes",180))
def history_for_display(db):
 """Add a safe display value while preserving the original audit evidence."""
 rows=[]
 for audit in db.history():
  row=dict(audit); message=""
  if row.get("raw_json"):
   try: message=radio_message(json.loads(row["raw_json"]),app_timezone(db))
   except (MessageError, TypeError, ValueError, KeyError, json.JSONDecodeError): pass
  # Legacy rows can lack CAP JSON; use their existing preview without editing it.
  if not message: message=row.get("message") or row.get("alert_message") or ""
  row["alert_display"]=message or row.get("cap_id") or "Alert unavailable"
  rows.append(row)
 return rows
def preview_signer(): return URLSafeTimedSerializer(current_app.config["SECRET_KEY"],salt="dispatch-preview-v1")

@bp.route("/login",methods=["GET","POST"])
def login():
 if request.method=="POST":
  user=current_app.extensions["db"].user(request.form.get("username",""),active_only=True)
  if user and check_password_hash(user["password_hash"],request.form.get("password", "")):
   session.clear();session["user_id"]=user["id"];return redirect(url_for("console.index"))
  flash("Invalid credentials")
 return render_template("login.html")
@bp.post("/logout")
@admin
def logout(): session.clear();return redirect(url_for("console.login"))
@bp.get("/")
@admin
def index():
 db=current_app.extensions["db"];last,error,health=feed_state(db)
 return render_template("index.html",alerts=db.actionable(),history=history_for_display(db),cap=message_limit(current_app.config),last_sync=last,feed_error=error,feed_health=health,tx_channel=saved_channel(db),device_name=db.setting("device_name") or "unknown")
@bp.get("/api/state")
@admin
def state():
 db=current_app.extensions["db"];alerts=db.actionable();history=history_for_display(db);last,error,health=feed_state(db)
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
 if db.claim(cap_id,actor()): db.finish(cap_id,"ignored",actor())
 else: flash("Alert is no longer actionable.")
 return redirect(url_for("console.index"))
@bp.post("/alerts/<cap_id>/ignore-preview")
@admin
def preview_ignore(cap_id):
 db=current_app.extensions["db"];alert=db.get(cap_id)
 if error:=alert_actionability_error(alert): return jsonify(error=error),409
 return jsonify(token=preview_signer().dumps({"jti":uuid4().hex,"action":"ignore","cap_id":cap_id,"updated_at":alert["updated_at"]}))
@bp.post("/alerts/<cap_id>/reopen-preview")
@admin
def preview_reopen(cap_id):
 db=current_app.extensions["db"];alert=db.get(cap_id)
 if not alert or alert["status"]!="ignored" or alert["test_only"]: return jsonify(error="alert is no longer an ignored NWS alert"),409
 if error:=db.reopen_error(cap_id): return jsonify(error=error),409
 try: message=reopened_alert_message(alert)
 except (MessageError, TypeError, ValueError, KeyError, json.JSONDecodeError) as exc: return jsonify(error=f"cannot regenerate a safe radio preview: {exc}"),400
 return jsonify(message=message,bytes=len(message.encode("utf-8")),cap=message_limit(current_app.config),token=preview_signer().dumps({"jti":uuid4().hex,"action":"reopen","cap_id":cap_id,"updated_at":alert["updated_at"],"message":message,"timezone":app_timezone(db),"cap":message_limit(current_app.config)}))
@bp.post("/alerts/<cap_id>/reopen")
@admin
def reopen(cap_id):
 db=current_app.extensions["db"]
 if request.form.get("confirm")!="yes": flash("No change: explicit confirmation is required.");return redirect(url_for("console.index"))
 try: preview=preview_signer().loads(request.form.get("reopen_token",""),max_age=300)
 except (SignatureExpired,BadSignature): flash("No change: reopen confirmation expired or is invalid.");return redirect(url_for("console.index"))
 alert=db.get(cap_id)
 if preview.get("action")!="reopen" or preview.get("cap_id")!=cap_id or not alert or preview.get("updated_at")!=alert["updated_at"] or preview.get("timezone")!=app_timezone(db) or preview.get("cap")!=message_limit(current_app.config): flash("No change: warning, time zone, or message limit changed; review it again.");return redirect(url_for("console.index"))
 try: message=reopened_alert_message(alert)
 except (MessageError, TypeError, ValueError, KeyError, json.JSONDecodeError) as exc: flash(f"No change: cannot regenerate a safe radio preview: {exc}");return redirect(url_for("console.index"))
 if preview.get("message")!=message: flash("No change: warning message changed; review it again.");return redirect(url_for("console.index"))
 if not db.consume_preview(preview.get("jti", "")): flash("No change: this confirmation was already used; review the warning again.");return redirect(url_for("console.index"))
 if error:=db.reopen(cap_id,actor(),message): flash(f"No change: {error}.")
 else: flash("Alert reopened for manual review. Nothing was transmitted.")
 return redirect(url_for("console.index"))
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
 db=current_app.extensions["db"];operator=actor();_,dev,status=device_state(db);channel=saved_channel(db)
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
 if not db.claim(cap_id,operator): flash("Alert is no longer actionable.");return redirect(url_for("console.index"))
 try:
  detail=send(runtime_config(current_app.config,db),dev,channel,message);db.finish(cap_id,"sent",operator,detail,message);flash("Dispatch recorded. Radio acceptance is not proof of mesh delivery.")
 except RadioError as exc: db.finish(cap_id,"failed",operator,str(exc),message);flash(f"No transmission: {exc}")
 return redirect(url_for("console.index"))
@bp.route("/settings",methods=["GET","POST"])
@admin
def settings():
 db=current_app.extensions["db"]
 if request.method=="POST":
  value=request.form.get("device");db.save_setting("device",value or "null");db.save_setting("tx_channel","null")
  flash("Device selection saved. Transmit channel cleared and must be selected for this device.");return redirect(url_for("console.settings"))
 cfg=runtime_config(current_app.config,db);coverage=db.coverage();coverage_states=", ".join(cfg["states"]) or "none"; unsupported=sum(1 for row in coverage if row["state"] not in cfg["states"])
 saved,dev,status=device_state(db);channels=[];channel_error=None;discovery=None;radio_name=None;name_error=None;cached_radio_name=db.setting("device_name") or ""
 if dev:
  try: discovery=discover_channels(dev);channels=[{"index":x["index"],"name":x["name"]} for x in discovery["slots"] if x["name"]]
  except RadioError as exc: channel_error=str(exc)
  try: radio_name=read_device_name(dev);db.save_setting("device_name",radio_name)
  except RadioError as exc: name_error=str(exc)
 return render_template("settings.html",devices=scan_devices(),saved=saved,status=status,tx_enabled=cfg["transmit_enabled"],coverage_count=len(coverage),coverage_states=coverage_states,unsupported_coverage=unsupported,alert_type_count=len(cfg["alert_types"]),channels=channels,discovery=discovery,tx_channel=saved_channel(db),channel_error=channel_error,radio_name=radio_name,cached_radio_name=cached_radio_name,name_error=name_error,timezones=valid_timezones(),timezone=app_timezone(db),nws_email=db.setting("nws_contact_email") or "",poll_seconds=cfg["poll_seconds"],nws_states=", ".join(cfg["states"]),web_tx_enabled=cfg["transmit_enabled"])
@bp.post("/settings/account")
@admin
def account_settings():
 db=current_app.extensions["db"];user=g.current_user;username=user["username"];current=request.form.get("current_password","")
 if not user or not check_password_hash(user["password_hash"],current): flash("Account settings were not changed: current password is incorrect.");return redirect(url_for("console.users_page"))
 requested=request.form.get("username","").strip();new_password=request.form.get("new_password","");confirm=request.form.get("confirm_password","")
 try:
  if requested and (len(requested)<3 or len(requested)>64 or not requested.replace("_","").replace("-","").isalnum()): raise ValueError("username must be 3–64 letters, digits, hyphens, or underscores")
  values={}
  if requested and requested!=username: values["auth_username"]=requested
  if new_password:
   validate_password(new_password,"new password")
   if new_password!=confirm: raise ValueError("new password confirmation does not match")
   values["auth_password_hash"]=generate_password_hash(new_password)
  if not values: raise ValueError("enter a new username or password")
  db.update_user(user["id"],values.get("auth_username"),values.get("auth_password_hash"));session.clear();db.audit(None,"administrator_account_changed",username,"credentials changed");flash("Account settings saved. Sign in again.")
 except ValueError as exc: flash(f"Account settings were not changed: {exc}")
 return redirect(url_for("console.users_page"))
def valid_username(value):
 value=value.strip()
 if len(value)<3 or len(value)>64 or not value.replace("_","").replace("-","").isalnum(): raise ValueError("username must be 3–64 letters, digits, hyphens, or underscores")
 return value
@bp.get("/settings/users")
@admin
def users_page():
 return render_template("users.html",users=current_app.extensions["db"].users(),current_user_id=g.current_user["id"],current_username=actor())
@bp.post("/settings/users/add")
@admin
def add_user():
 db=current_app.extensions["db"]
 try:
  username=valid_username(request.form.get("username", ""));password=request.form.get("password","")
  validate_password(password,"password")
  if password!=request.form.get("confirm_password",""): raise ValueError("password confirmation does not match")
  db.add_user(username,generate_password_hash(password));db.audit(None,"user_added",actor(),f"user={username}");flash(f"User {username} added.")
 except ValueError as exc: flash(f"User was not added: {exc}")
 return redirect(url_for("console.users_page"))
@bp.post("/settings/users/edit")
@admin
def edit_user():
 db=current_app.extensions["db"]
 try:
  target_id=int(request.form.get("target_id",""));target=db.user_by_id(target_id)
  if not target: raise ValueError("user was not found")
  if target_id==g.current_user["id"]: raise ValueError("use My Account to change your own username or password")
  new_username=valid_username(request.form.get("username", ""));password=request.form.get("password","")
  if password:
   validate_password(password,"password")
   if password!=request.form.get("confirm_password",""): raise ValueError("password confirmation does not match")
  db.update_user(target_id,new_username,generate_password_hash(password) if password else None)
  db.audit(None,"user_updated",actor(),f"user={target['username']}; renamed={new_username!=target['username']}; password_changed={bool(password)}");flash(f"User {new_username} updated.")
 except (TypeError,ValueError) as exc: flash(f"User was not updated: {exc}")
 return redirect(url_for("console.users_page"))
@bp.post("/settings/users/status")
@admin
def user_status():
 db=current_app.extensions["db"];disable=request.form.get("disable")=="yes"
 try:
  target_id=int(request.form.get("target_id",""));target=db.user_by_id(target_id)
  if not target: raise ValueError("user was not found")
  db.set_user_disabled(target_id,disable);db.audit(None,"user_disabled" if disable else "user_enabled",actor(),f"user={target['username']}")
  if disable and target_id==g.current_user["id"]: session.clear();flash("Your account was disabled. Sign in with another active account.");return redirect(url_for("console.login"))
  flash(f"User {target['username']} {'disabled' if disable else 'enabled'}.")
 except (TypeError,ValueError) as exc: flash(f"User status was not changed: {exc}")
 return redirect(url_for("console.users_page"))
@bp.post("/settings/users/remove")
@admin
def remove_user():
 db=current_app.extensions["db"]
 try:
  if request.form.get("confirm")!="yes": raise ValueError("explicit confirmation is required")
  target_id=int(request.form.get("target_id",""));target=db.user_by_id(target_id)
  if not target: raise ValueError("user was not found")
  db.remove_user(target_id);db.audit(None,"user_removed",actor(),f"user={target['username']}")
  if target_id==g.current_user["id"]: session.clear();flash("Your account was removed.");return redirect(url_for("console.login"))
  flash(f"User {target['username']} removed.")
 except (TypeError,ValueError) as exc: flash(f"User was not removed: {exc}")
 return redirect(url_for("console.users_page"))
@bp.post("/settings/nws")
@admin
def nws_settings():
 db=current_app.extensions["db"]
 try:
  email=contact_email(request.form.get("contact_email", ""));interval=int(request.form.get("poll_seconds","0"));selected=states(request.form.get("states",""))
  if interval<60: raise ValueError("polling interval must be at least 60 seconds")
  retained=[row for row in db.coverage() if row["state"] not in selected]
  if retained and request.form.get("confirm_retained_counties")!="yes": raise ValueError(f"{len(retained)} selected county or counties remain outside the proposed polling states; confirm to keep them without monitoring them")
  db.save_settings({"nws_contact_email":email,"nws_poll_seconds":str(interval),"nws_states":json.dumps(selected)});db.audit(None,"nws_settings_changed",actor(),f"states={','.join(selected)} interval={interval}");flash("NWS settings saved. The next polling cycle will use them.")
 except (ValueError,TypeError) as exc: flash(f"NWS settings were not changed: {exc}")
 return redirect(url_for("console.settings"))
@bp.post("/settings/transmission")
@admin
def transmission_settings():
 db=current_app.extensions["db"];enabled=request.form.get("transmit_enabled")=="yes";db.save_setting("transmit_enabled","true" if enabled else "false");db.audit(None,"transmission_setting_changed",actor(),"enabled" if enabled else "disabled");flash("Manual transmission setting saved. Every message still requires explicit approval and confirmation.");return redirect(url_for("console.settings"))
@bp.route("/settings/alert-types",methods=["GET","POST"])
@admin
def alert_types_page():
 db=current_app.extensions["db"]
 def page(selected=None):
  cfg=runtime_config(current_app.config,db);catalog=db.alert_type_catalog();available=sorted(set([row["event"] for row in catalog] or list(DEFAULT_ALERT_TYPES)) | set(cfg["alert_types"]))
  chosen=set(cfg["alert_types"] if selected is None else selected)
  groups={name:[row for row in catalog if row["category"]==name] for name in ("Warnings","Watches","Advisories","Other")}
  if not catalog:
   groups={name:[{"event":event,"category":name} for event in available if alert_type_category(event)==name] for name in ("Warnings","Watches","Advisories","Other")}
  else:
   # Never hide an operator's existing choice merely because NWS later changes
   # its catalog. It remains explicitly selectable until the operator removes it.
   previous=sorted(chosen-{row["event"] for row in catalog})
   if previous: groups["Previously Configured"]=[{"event":event,"category":"Previously Configured"} for event in previous]
  return render_template("alert_types.html",groups=groups,selected=chosen,selected_count=len(chosen),catalog_meta=db.alert_type_catalog_meta(),has_catalog=bool(catalog))
 if request.method=="POST":
  action=request.form.get("action")
  try:
   if action=="refresh":
    user_agent=runtime_config(current_app.config,db).get("nws_user_agent")
    if not user_agent: raise AlertTypeCatalogError("set a valid NWS contact email before refreshing the catalog")
    events,meta=fetch_event_types(user_agent);db.replace_alert_type_catalog(events,meta);db.audit(None,"alert_type_catalog_imported",actor(),f"{len(events)} event types; {meta['source']}");flash("Official NWS alert-type catalog refreshed. Enabled alert types were not changed.")
   elif action=="save":
    catalog=db.alert_type_catalog();available=sorted(set([row["event"] for row in catalog] or list(DEFAULT_ALERT_TYPES)) | set(runtime_config(current_app.config,db)["alert_types"]))
    selected=request.form.getlist("event");db.save_alert_types(selected,available);db.audit(None,"alert_types_saved",actor(),f"{len(set(selected))} alert types enabled");flash(f"Alert types saved: {len(set(selected))} enabled. Changes apply to the next poll.")
   else: raise AlertTypeCatalogError("unknown alert-type action")
  except (AlertTypeCatalogError,ValueError,requests.RequestException) as exc:
   flash(f"Alert types were not changed: {exc}")
   if action=="save": return page(request.form.getlist("event"))
  return redirect(url_for("console.alert_types_page"))
 return page()
@bp.get("/settings/software-updates")
@admin
def software_updates(): return render_template("software_updates.html",installed=installed_update(),available=available_update(),update=update_status(current_app.config))
@bp.get("/api/software-updates")
@admin
def software_update_status(): return jsonify(update_status(current_app.config))
@bp.post("/settings/software-updates/install")
@admin
def install_software_update():
 if request.form.get("confirm")!="yes": flash("Update was not started: explicit confirmation is required.")
 else:
  try: launch_update(current_app.config);current_app.extensions["db"].audit(None,"software_update_requested",actor(),"web update helper launched");flash("Update started. The console retains output while services restart.")
  except RuntimeError as exc: flash(f"Update was not started: {exc}")
 return redirect(url_for("console.software_updates"))
@bp.post("/settings/software-updates/reboot")
@admin
def reboot_host():
 if request.form.get("confirm")!="yes": flash("Reboot was not requested: explicit confirmation is required.")
 else:
  try: launch_update(current_app.config,reboot=True);current_app.extensions["db"].audit(None,"reboot_requested",actor(),"web reboot helper launched");flash("Reboot requested.")
  except RuntimeError as exc: flash(f"Reboot was not requested: {exc}")
 return redirect(url_for("console.software_updates"))
@bp.route("/settings/coverage-counties",methods=["GET","POST"])
@admin
def coverage_counties_page():
 db=current_app.extensions["db"]
 def page(state="",query="",selected=None):
  coverage=db.coverage();saved={r["ugc"] for r in coverage}
  return render_template("coverage_counties.html",catalog=db.catalog(),states=db.catalog_states(),state=state,query=query,selected=saved if selected is None else set(selected),saved_selected=saved,coverage=coverage,meta=db.catalog_meta())
 if request.method=="POST":
  action=request.form.get("action");return_filters={"state":request.form.get("return_state",""),"q":request.form.get("return_query","")}
  try:
   if action=="import":
    rows,meta=fetch_catalog(runtime_config(current_app.config,db)["nws_user_agent"]);db.replace_catalog(rows,meta);db.audit(None,"county_catalog_imported",actor(),f"{len(rows)} counties; {meta['source']}");flash("County catalog imported. Coverage was not changed.")
   elif action=="save":
    selected=request.form.getlist("county")
    if len(selected)>25 and request.form.get("confirm_expand")!="yes": raise CatalogError("confirm this coverage expansion before saving")
    db.save_coverage(selected,set(runtime_config(current_app.config,db)["states"]));db.audit(None,"coverage_counties_saved",actor(),f"{len(selected)} counties selected");flash(f"Coverage saved: {len(selected)} counties.")
  except (CatalogError,ValueError,requests.RequestException) as exc:
   flash(f"Coverage was not changed: {exc}")
   if action=="save": return page(return_filters["state"],return_filters["q"],request.form.getlist("county"))
  return redirect(url_for("console.coverage_counties_page",**{key:value for key,value in return_filters.items() if value}))
 return page(request.args.get("state",""),request.args.get("q",""))
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
  db.save_setting("tx_channel",json.dumps(selected));db.audit(None,"tx_channel_selected",actor(),f"{selected['name']} (slot {selected['index']})");flash(f"Transmit channel saved: {selected['name']}.")
 except (ValueError,RadioError) as exc: flash(f"Channel not saved: {status}. {exc}")
 return redirect(url_for("console.settings"))
@bp.post("/settings/channels/add")
@admin
def add_channel():
 db=current_app.extensions["db"];_,dev,status=device_state(db)
 try:
  if request.form.get("confirm") != "yes": raise RadioError("explicit confirmation is required")
  created=add_hashtag_channel(dev,int(request.form.get("slot","-1")),request.form.get("name",""))
  db.audit(None,"channel_added",actor(),f"{created['name']} (slot {created['index']})")
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
  delete_channel(dev,slot,name);db.audit(None,"channel_deleted",actor(),f"device={dev.get('stable_path') or dev.get('device')}; channel={name}; slot={slot}");flash(f"Deleted {name} from slot {slot}.")
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
    verify_channel(dev,channel);detail=send(runtime_config(current_app.config,db),dev,channel,message);db.test_message(actor(),channel,message,"accepted",detail);flash(f"Test result: accepted. {detail}")
   except RadioError as exc: db.test_message(actor(),channel or {"name":"none","index":-1},message,"failed",str(exc));flash(f"Test message failed: {status}. {exc}")
 return render_template("test_messaging.html",channel=channel,device_name=db.setting("device_name") or "unknown",status=status,cap=message_limit(current_app.config),history=db.test_history(),enabled=bool(channel and dev and transmissions_enabled(current_app.config,db)))
@bp.route("/test-alerts",methods=["GET","POST"])
@admin
def test_alerts():
 db=current_app.extensions["db"];cfg=runtime_config(current_app.config,db); counties=coverage_counties(cfg)
 if request.method=="POST":
  try:
   feature=build_test_alert(cfg,request.form.getlist("county"),request.form.get("event",""))
   # Reuse the real eligibility, shortening, expiry, and manual-dispatch path.
   # This route never contacts NWS or the Companion.
   if not eligible(feature,cfg): raise TestAlertError("synthetic warning does not match configured coverage")
   ingest(current_app,[feature]); db.audit(feature["properties"]["id"],"test_alert_created",actor(),"TEST ONLY synthetic warning created")
   flash("TEST ONLY warning created. It is awaiting the normal manual Send to Mesh or Ignore decision.")
   return redirect(url_for("console.index"))
  except TestAlertError as exc: flash(f"TEST ONLY warning was not created: {exc}")
 return render_template("test_alerts.html",counties=counties,alert_types=runtime_config(current_app.config,current_app.extensions["db"])["alert_types"])
@bp.post("/settings/test")
@admin
def test_device():
 db=current_app.extensions["db"];_,dev,status=device_state(db)
 try: flash(status+". "+verify_connection(current_app.config,dev))
 except RadioError as exc: flash(status+". "+str(exc))
 return redirect(url_for("console.settings"))
