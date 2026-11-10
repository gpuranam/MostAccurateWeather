"""Verify the airspace a flight number actually flies, from recent ADS-B tracks.

Source: FlightAware AeroAPI v4 (https://aeroapi.flightaware.com/aeroapi).
Auth: the session proxy injects the `x-apikey` header (request_credential); if
FLIGHTAWARE_API_KEY is set locally it is sent directly.

Verdicts are cached per (flight, origin, dest) for `cache_ttl_hours`, because
routings change with NOTAMs and the regional situation.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
from pathlib import Path

import requests

import geo

AEROAPI = "https://aeroapi.flightaware.com/aeroapi"
CACHE = Path(__file__).parent / "route_cache.json"


def _headers():
    k = os.environ.get("FLIGHTAWARE_API_KEY")
    return {"x-apikey": k} if k else {}


def _get(path: str, params: dict | None = None) -> dict:
    for attempt in range(3):
        try:
            r = requests.get(AEROAPI + path, params=params, headers=_headers(), timeout=60)
        except requests.RequestException as e:
            if attempt == 2:
                return {"_error": str(e)}
            time.sleep(2 ** (attempt + 1))
            continue
        if r.status_code == 429:  # personal tier: 10 result sets / minute
            time.sleep(7 * (attempt + 1))
            continue
        try:
            data = r.json()
        except ValueError:
            data = {"_error": r.text[:200]}
        if r.status_code >= 400:
            data["_error"] = f"HTTP {r.status_code}: {data.get('title') or data.get('detail') or ''}"
        return data
    return {"_error": "rate limited"}


def load_cache() -> dict:
    if CACHE.exists():
        return json.loads(CACHE.read_text())
    return {}


def save_cache(c: dict):
    CACHE.write_text(json.dumps(c, indent=1, sort_keys=True))


def verify_flight(flight: str, origin: str, dest: str, cfg: dict, cache: dict,
                  now: dt.datetime | None = None) -> dict:
    """Returns {"status": compliant|violates|unverified, ...evidence}."""
    a = cfg["airspace"]
    now = now or dt.datetime.now(dt.timezone.utc)
    key = f"{flight}:{origin}-{dest}"
    hit = cache.get(key)
    if hit and hit.get("status") != "unverified":
        age_h = (now - dt.datetime.fromisoformat(hit["checked_at"])).total_seconds() / 3600
        if age_h < a["cache_ttl_hours"]:
            return hit
    start = (now - dt.timedelta(days=a["lookback_days"])).strftime("%Y-%m-%dT%H:%M:%SZ")
    end = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    res = _get(f"/flights/{flight}", {"start": start, "end": end, "max_pages": 1})
    verdict = {"flight": flight, "origin": origin, "dest": dest,
               "checked_at": now.isoformat(), "tracks": []}
    if res.get("_error"):
        verdict.update(status="unverified", reason=f"AeroAPI: {res['_error']}")
        cache[key] = verdict
        return verdict
    flights = [f for f in res.get("flights", [])
               if (f.get("origin") or {}).get("code_iata") == origin
               and (f.get("destination") or {}).get("code_iata") == dest
               and f.get("actual_on") and not f.get("cancelled")]
    flights.sort(key=lambda f: f.get("actual_on") or "", reverse=True)
    if not flights:
        verdict.update(status="unverified",
                       reason=f"no completed {origin}-{dest} flights for {flight} in last {a['lookback_days']} days")
        cache[key] = verdict
        return verdict
    any_violation = False
    for f in flights[: a["tracks_per_flight"]]:
        tr = _get(f"/flights/{f['fa_flight_id']}/track")
        pts = [(p["longitude"], p["latitude"]) for p in tr.get("positions", [])
               if p.get("longitude") is not None and p.get("latitude") is not None]
        if len(pts) < 20:
            verdict["tracks"].append({"date": f.get("actual_off", "")[:10], "error": "too few positions"})
            continue
        c = geo.classify_track(pts, a["excluded_countries"], a["border_buffer_km"],
                               a["excluded_sea_zones"])
        any_violation |= not c["compliant"]
        verdict["tracks"].append({"date": (f.get("actual_off") or "")[:10],
                                  "countries": c["countries_crossed"],
                                  "violations": c["violations"],
                                  "warnings": c["warnings"]})
    good = [t for t in verdict["tracks"] if "error" not in t]
    if not good:
        verdict.update(status="unverified", reason="no usable tracks")
    elif any_violation:
        verdict.update(status="violates",
                       reason="; ".join(sorted({k for t in good for k in t["violations"]})))
    else:
        warn = sorted({k for t in good for k in t.get("warnings", {})})
        verdict.update(status="compliant", reason=f"{len(good)} recent tracks clean"
                       + (f" (skirts: {', '.join(warn)})" if warn else ""))
    cache[key] = verdict
    return verdict


def needs_check(seg, cfg: dict) -> bool:
    dom = cfg["airspace"]["domestic_airports"]
    for airports in dom.values():
        if seg.origin in airports and seg.dest in airports:
            return False
    return True
