"""Home Assistant REST API helpers (live device state + control).

Self-contained (httpx + config only) to avoid import cycles with ollama_client/tools.
All functions fail-soft (return a string; never raise into a query).
"""
import json
import re
import httpx
import config

COMMON_DOMAINS = "light, switch, climate, lock, cover, fan, media_player, sensor"
COMMON_AREAS = config.HA_AREAS     # the home's Home Assistant area ids (a setting)
_AREA_RE = re.compile(r"^[a-z0-9_]+$")


def _headers():
    return {"Authorization": f"Bearer {config.HA_TOKEN}", "Content-Type": "application/json"}


def _states():
    r = httpx.get(f"{config.HA_URL}/api/states", headers=_headers(), timeout=config.REACH_TIMEOUT)
    r.raise_for_status()
    return r.json()


def _area_entity_ids(area):
    r = httpx.post(
        f"{config.HA_URL}/api/template", headers=_headers(),
        json={"template": "{{ area_entities('%s') | tojson }}" % area},
        timeout=config.REACH_TIMEOUT,
    )
    r.raise_for_status()
    return set(json.loads(r.text))


def get_devices(domain=None, name_filter=None, area=None):
    domain = (domain or "").strip().lower()
    name_filter = (name_filter or "").strip().lower()
    area = (area or "").strip().lower()
    if not domain and not area:
        return f"Specify a domain and/or area to list, e.g. domain: {COMMON_DOMAINS} / area: {COMMON_AREAS}."
    if area and not _AREA_RE.match(area):
        return f"Invalid area '{area}'. Known areas: {COMMON_AREAS}."
    try:
        states = _states()
    except Exception as e:
        return f"Couldn't reach Home Assistant: {e}"

    if area:
        try:
            entity_ids = _area_entity_ids(area)
        except Exception as e:
            return f"Couldn't look up area '{area}': {e}"
        if not entity_ids:
            return f"No entities found in area '{area}'. Known areas: {COMMON_AREAS}."
        states = [s for s in states if s.get("entity_id") in entity_ids]

    if domain:
        states = [s for s in states if s.get("entity_id", "").startswith(f"{domain}.")]
    if name_filter:
        states = [
            s for s in states
            if name_filter in s.get("attributes", {}).get("friendly_name", "").lower()
        ]
    if not states:
        return "No matching entities found" + (f" in area '{area}'" if area else "") + (f" for domain '{domain}'" if domain else "") + (f" matching '{name_filter}'" if name_filter else "") + "."

    lines = []
    for s in states[:60]:
        name = s.get("attributes", {}).get("friendly_name", s["entity_id"])
        lines.append(f"{name} ({s['entity_id']}): {s['state']}")
    if len(states) > 60:
        lines.append(f"...and {len(states) - 60} more. Use name_filter to narrow the search.")
    return "\n".join(lines)


def call_service(entity_id=None, action=None, data=None, area=None, domain=None):
    entity_id = (entity_id or "").strip()
    action = (action or "").strip()
    area = (area or "").strip().lower()
    domain = (domain or "").strip().lower()
    if not action:
        return "Specify an action (HA service name), e.g. turn_on, turn_off, toggle, lock, unlock, set_temperature."

    payload = {}
    if isinstance(data, dict):
        payload.update(data)

    if entity_id:
        if "." not in entity_id:
            return "entity_id must be like 'light.kitchen_lamp'."
        domain = entity_id.split(".", 1)[0]
        payload["entity_id"] = entity_id
        target_desc = entity_id
    elif area and domain:
        if not _AREA_RE.match(area):
            return f"Invalid area '{area}'. Known areas: {COMMON_AREAS}."
        payload["area_id"] = area
        target_desc = f"all '{domain}' devices in area '{area}'"
    else:
        return "Specify either entity_id (single device), or both area and domain (e.g. area='office', domain='light' to control every light in a room)."

    try:
        r = httpx.post(
            f"{config.HA_URL}/api/services/{domain}/{action}",
            headers=_headers(), json=payload, timeout=config.REACH_TIMEOUT,
        )
        r.raise_for_status()
        return f"OK: called {domain}.{action} on {target_desc}."
    except Exception as e:
        return f"Couldn't call {domain}.{action} on {target_desc}: {e}"
