from __future__ import annotations
import json, time, requests, hashlib, re
from datetime import datetime, timezone
from .db import now
from .messages import radio_message
from .runtime import runtime_config

NWS = "https://api.weather.gov/alerts/active"
def iso_past(value):
    try: return datetime.fromisoformat(value.replace("Z", "+00:00")) <= datetime.now(timezone.utc)
    except (ValueError, AttributeError): return False

def cap_reference_ids(references) -> list[str]:
    """Extract identifiers from CAP `sender,identifier,sent` reference tuples."""
    if not isinstance(references, str): return []
    return [parts[1].strip() for item in references.split() if len(parts := item.strip().split(",")) >= 2 and parts[1].strip()]

def _codes(value):
    values=value if isinstance(value,(list,tuple)) else [value]
    return [normalize_geo_code(str(code).rstrip("/").rsplit("/",1)[-1]) for code in values if str(code).strip()]

def coverage_counties(cfg) -> list[dict]:
    """Return configured coverage choices without inventing county identities."""
    db=cfg.get("coverage_db")
    if db is not None:
        return [{"id":f"same:{r['same']}|ugc:{r['ugc']}","label":f"{r['name']}, {r['state']}","same":[r["same"]],"ugc":[r["ugc"]]} for r in db.coverage()]
    choices=[]; seen=set()
    for value in cfg.get("counties",[]):
        if isinstance(value,dict):
            same=_codes(value.get("same",value.get("SAME",[])))
            ugc=_codes(value.get("ugc",value.get("UGC",[])))
            label=str(value.get("name") or " / ".join(same+ugc)).strip()
        else:
            code=_codes(value); same=[x for x in code if x.isdigit()]; ugc=[x for x in code if not x.isdigit()]
            label=" / ".join(same+ugc)
        if not label or not (same or ugc): continue
        key="same:"+",".join(sorted(same))+"|ugc:"+",".join(sorted(ugc))
        if key in seen: continue
        seen.add(key); choices.append({"id":key,"label":label,"same":same,"ugc":ugc})
    return choices

def configured_counties(cfg) -> set[str]:
    """Accept NWS SAME FIPS, UGC codes, and the final segment of an NWS URL."""
    return {code for county in coverage_counties(cfg) for code in county["same"]+county["ugc"]}

def normalize_geo_code(value: str) -> str:
    value=value.upper()
    # SAME is six digits (subdivision + state FIPS + county FIPS); accept a
    # shorter user-entered numeric code while comparing it in canonical form.
    return value.zfill(6) if value.isdigit() and len(value) <= 6 else value

def eligible(feature, cfg):
    p=feature["properties"]
    if p.get("event") not in cfg.get("alert_types", []): return False
    return geographically_eligible(feature,cfg)

def geographically_eligible(feature, cfg):
    p=feature["properties"]
    counties=configured_counties(cfg)
    # An empty selected coverage list is fail-closed; it never means all counties.
    if not counties: return False
    geocode=p.get("geocode") or {}
    # A null/missing geometry is immaterial: no polygon parsing is performed.
    codes={normalize_geo_code(str(code)) for code in (geocode.get("SAME") or [])+(geocode.get("UGC") or [])}
    return bool(codes & set(counties))

def related_to_existing_alert(db, feature) -> bool:
    """Keep CAP revisions/cancellations for an existing family after deselection.

    Operators stop *new* alert types by deselecting them. A later CAP revision or
    cancellation still needs to close the already-recorded family safely.
    """
    props=feature.get("properties") or {}
    references=cap_reference_ids(props.get("references"))
    if not references:
        return False
    with db.connect() as c:
        return bool(c.execute("SELECT 1 FROM alerts WHERE cap_id IN (%s) AND test_only=0 LIMIT 1" % ",".join("?"*len(references)),references).fetchone())

def content_fingerprint(props: dict) -> str:
    """Stable comparison that deliberately excludes alert ID, CAP references and sent time."""
    fields=("event","areaDesc","headline","description","instruction","effective","onset","expires","severity","urgency","certainty")
    normalized="\n".join(re.sub(r"\s+"," ",(props.get(x) or "").strip()).casefold() for x in fields)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

def mark(c, cap_id, status, detail=None):
    row=c.execute("SELECT test_only FROM alerts WHERE cap_id=?",(cap_id,)).fetchone()
    if row and row["test_only"]:
        detail=f"TEST ONLY — {detail or status}"
    c.execute("UPDATE alerts SET status=?,updated_at=? WHERE cap_id=?",(status,now(),cap_id))
    c.execute("INSERT INTO audit(cap_id,action,detail,created_at) VALUES(?,?,?,?)",(cap_id,status,detail,now()))

def ingest(app, features):
    db=app.extensions["db"]; cfg=app.config
    for f in features:
        p=f["properties"]; cap_id=p["id"]
        test_only=bool(p.get("test_only")); references=cap_reference_ids(p.get("references"))
        with db.connect() as c:
            existing=c.execute("SELECT cap_id FROM alerts WHERE cap_id=?",(cap_id,)).fetchone()
            if existing: continue
            prior_rows=c.execute("SELECT * FROM alerts WHERE cap_id IN (%s) AND test_only=? ORDER BY created_at DESC" % ",".join("?"*len(references)),(*references,int(test_only))).fetchall() if references else []
            family_id=prior_rows[0]["family_id"] if prior_rows and prior_rows[0]["family_id"] else (prior_rows[0]["cap_id"] if prior_rows else cap_id)
            fingerprint=content_fingerprint(p)
            cancel=(p.get("messageType") or "").lower()=="cancel"
            # A cancellation closes actionable revisions but never changes sent/ignored history.
            if cancel:
                for row in c.execute("SELECT cap_id FROM alerts WHERE family_id=? AND test_only=? AND status IN ('pending','sending')",(family_id,int(test_only))).fetchall(): mark(c,row["cap_id"],"canceled",cap_id)
                status="canceled"; label="Cancellation"
            else:
                same=next((r for r in prior_rows if r["content_hash"]==fingerprint),None)
                if same:
                    status="unchanged"; label="Unchanged revision"
                else:
                    # References sometimes point only at the immediately prior CAP
                    # revision. Preserve the operator context from the full family.
                    prior_statuses={r["status"] for r in c.execute("SELECT status FROM alerts WHERE family_id=? AND test_only=?",(family_id,int(test_only))).fetchall()}
                    for row in c.execute("SELECT cap_id FROM alerts WHERE family_id=? AND test_only=? AND status IN ('pending','sending')",(family_id,int(test_only))).fetchall(): mark(c,row["cap_id"],"superseded",cap_id)
                    status="pending"
                    label="Updated Warning — Previously Sent" if "sent" in prior_statuses else ("Updated Warning — Previously Ignored" if "ignored" in prior_statuses else ("Updated Warning" if prior_rows else "New Warning"))
            if iso_past(p.get("expires")): status="expired"
            if test_only: label=f"TEST ONLY — {label}"
            message=radio_message(f,cfg.get("timezone","UTC"))
            c.execute("INSERT INTO alerts(cap_id,family_id,content_hash,revision_label,event,headline,description,areas,sent,effective,expires,status,message,replaces_id,raw_json,test_only,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(cap_id,family_id,fingerprint,label,p.get("event",""),p.get("headline"),p.get("description"),p.get("areaDesc"),p.get("sent"),p.get("effective"),p.get("expires"),status,message,references[0] if references else None,json.dumps(f),int(test_only),now(),now()))
            c.execute("INSERT INTO audit(cap_id,action,detail,message,created_at) VALUES(?,?,?,?,?)",(cap_id,status,label,message,now()))
    with db.connect() as c:
        rows=c.execute("SELECT cap_id FROM alerts WHERE status='pending' AND expires IS NOT NULL AND expires <= ?",(now(),)).fetchall()
        for r in rows:
            mark(c,r[0],"expired","local expiration")

def fetch(app):
    cfg=runtime_config(app.config,app.extensions["db"]); params=[]
    if not cfg["states"]:
        app.extensions["db"].save_setting("nws_last_error","NWS polling is not configured: select one or more states in Settings")
        return
    if not cfg.get("nws_user_agent"):
        app.extensions["db"].save_setting("nws_last_error","NWS polling is not configured: set a monitored contact email in Settings")
        return
    for state in cfg.get("states",[]): params.append(("area",state.upper()))
    try:
        response=requests.get(NWS, params=params, headers={"User-Agent":cfg["nws_user_agent"],"Accept":"application/geo+json"}, timeout=15)
        response.raise_for_status(); data=response.json()
        features=[]
        for feature in data.get("features",[]):
            known_family=related_to_existing_alert(app.extensions["db"],feature)
            # A CAP cancellation/revision can omit geographic codes.  Once a
            # family was legitimately ingested, keep applying its lifecycle
            # updates even if the operator later deselects the event type.
            if known_family or eligible(feature,cfg):
                features.append(feature)
        ingest(app,features)
        app.extensions["db"].save_setting("nws_last_success",now())
        app.extensions["db"].save_setting("nws_last_error","")
    except Exception as exc:
        app.extensions["db"].save_setting("nws_last_error",str(exc)[:500])
        raise

def run_poller(app):
    while True:
        try:
            with app.app_context(): fetch(app)
        except Exception as exc:
            app.logger.exception("NWS polling failed: %s",exc)
        time.sleep(runtime_config(app.config,app.extensions["db"])["poll_seconds"])
