"""Durable NWS county coverage catalog and selection helpers."""
from __future__ import annotations
from datetime import datetime, timezone
import requests

NWS_COUNTIES_URL="https://api.weather.gov/zones?type=county"
# FIPS state code required by NWS SAME: 0 + SSCCC. Territories included.
STATE_FIPS={"AL":"01","AK":"02","AZ":"04","AR":"05","CA":"06","CO":"08","CT":"09","DE":"10","DC":"11","FL":"12","GA":"13","HI":"15","ID":"16","IL":"17","IN":"18","IA":"19","KS":"20","KY":"21","LA":"22","ME":"23","MD":"24","MA":"25","MI":"26","MN":"27","MS":"28","MO":"29","MT":"30","NE":"31","NV":"32","NH":"33","NJ":"34","NM":"35","NY":"36","NC":"37","ND":"38","OH":"39","OK":"40","OR":"41","PA":"42","RI":"44","SC":"45","SD":"46","TN":"47","TX":"48","UT":"49","VT":"50","VA":"51","WA":"53","WV":"54","WI":"55","WY":"56","AS":"60","GU":"66","MP":"69","PR":"72","VI":"78"}

class CatalogError(ValueError): pass

def normalize_catalog(data):
    rows=[]; seen=set()
    for feature in data.get("features",[]):
        p=feature.get("properties") or {}; ugc=str(p.get("id") or "").upper()
        if len(ugc)!=6 or ugc[2]!="C" or ugc[:2] not in STATE_FIPS or not ugc[3:].isdigit(): continue
        state=ugc[:2]; fips=ugc[3:]; same="0"+STATE_FIPS[state]+fips; name=str(p.get("name") or "").strip()
        if not name or ugc in seen: continue
        seen.add(ugc); rows.append({"state":state,"name":name,"same":same,"ugc":ugc})
    if not rows: raise CatalogError("catalog contains no valid NWS county UGC records")
    return rows

def fetch_catalog(user_agent):
    response=requests.get(NWS_COUNTIES_URL,headers={"User-Agent":user_agent,"Accept":"application/geo+json"},timeout=30)
    response.raise_for_status(); rows=normalize_catalog(response.json())
    return rows,{"source":NWS_COUNTIES_URL,"retrieved_at":datetime.now(timezone.utc).isoformat(),"version":response.headers.get("ETag") or response.headers.get("Last-Modified") or "not supplied"}

def config_rows(values):
    rows=[]
    for value in values or []:
        if isinstance(value,dict):
            same=str(value.get("same") or value.get("SAME") or "").zfill(6);ugc=str(value.get("ugc") or value.get("UGC") or "").upper();name=str(value.get("name") or same or ugc)
        else:
            code=str(value).upper();same=code.zfill(6) if code.isdigit() else "";ugc="" if same else code;name=code
        if same or ugc: rows.append({"state":ugc[:2] if len(ugc)>=2 else "","name":name,"same":same,"ugc":ugc})
    return rows
