"""Official NWS alert event-type catalog and safe selection helpers."""
from __future__ import annotations

from datetime import datetime, timezone

import requests

NWS_ALERT_TYPES_URL = "https://api.weather.gov/alerts/types"
DEFAULT_ALERT_TYPES = (
    "Tornado Warning",
    "Severe Thunderstorm Warning",
    "Flash Flood Warning",
)


class AlertTypeCatalogError(ValueError):
    pass


def category(event: str) -> str:
    """Group NWS event labels without attempting to reinterpret their meaning."""
    if event.endswith(" Warning"):
        return "Warnings"
    if event.endswith(" Watch"):
        return "Watches"
    if event.endswith(" Advisory"):
        return "Advisories"
    return "Other"


def normalize_event_types(data) -> list[str]:
    """Validate the NWS JSON-LD ``eventTypes`` list and retain exact labels."""
    values = data.get("eventTypes") if isinstance(data, dict) else None
    if not isinstance(values, list):
        raise AlertTypeCatalogError("NWS alert-type catalog has no eventTypes list")
    result = sorted({str(value).strip() for value in values if isinstance(value, str) and str(value).strip()})
    if not result:
        raise AlertTypeCatalogError("NWS alert-type catalog contains no usable event names")
    if any(len(value) > 160 for value in result):
        raise AlertTypeCatalogError("NWS alert-type catalog contains an invalid event name")
    return result


def fetch_event_types(user_agent: str) -> tuple[list[str], dict]:
    """Fetch the authoritative catalog; callers retain the old cache on failure."""
    response = requests.get(
        NWS_ALERT_TYPES_URL,
        headers={"User-Agent": user_agent, "Accept": "application/ld+json"},
        timeout=30,
    )
    response.raise_for_status()
    events = normalize_event_types(response.json())
    return events, {
        "source": NWS_ALERT_TYPES_URL,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "version": response.headers.get("ETag") or response.headers.get("Last-Modified") or "not supplied",
    }
