"""Free route verification from FlightAware's public flight-history pages.

Batch-fetches recent tracks with js/fa_track.cjs (slow on purpose: FlightAware's
Cloudflare front challenges after ~10 rapid loads) and classifies them with
geo.classify_track. Writes verdicts into the same cache schema as routes.py.
"""
from __future__ import annotations

import datetime as dt
import json
import subprocess
import tempfile
from pathlib import Path

import geo

JS = Path(__file__).parent / "js" / "fa_track.cjs"

ICAO = {
    "EWR": "KEWR", "IAD": "KIAD", "JFK": "KJFK", "ORD": "KORD", "SFO": "KSFO", "BOS": "KBOS",
    "DEL": "VIDP", "BOM": "VABB", "PNQ": "VAPO", "BLR": "VOBL", "HYD": "VOHS", "MAA": "VOMM",
    "FRA": "EDDF", "MUC": "EDDM", "ZRH": "LSZH", "CDG": "LFPG", "LHR": "EGLL", "AMS": "EHAM",
    "IST": "LTFM", "VIE": "LOWW", "FCO": "LIRF", "CPH": "EKCH", "HEL": "EFHK", "WAW": "EPWA",
    "HND": "RJTT", "NRT": "RJAA", "SIN": "WSSS", "HKG": "VHHH", "ICN": "RKSI", "TPE": "RCTP",
    "YYZ": "CYYZ", "YVR": "CYVR", "DXB": "OMDB", "DOH": "OTHH", "AUH": "OMAA",
}


AIRLINE_ICAO = {
    "UA": "UAL", "AI": "AIC", "AF": "AFR", "LH": "DLH", "LX": "SWR", "KL": "KLM", "BA": "BAW", "VS": "VIR",
    "6E": "IGO", "IX": "AXB", "TK": "THY", "NH": "ANA", "JL": "JAL", "SQ": "SIA", "EK": "UAE", "QR": "QTR",
    "EY": "ETD", "AC": "ACA", "DL": "DAL", "AA": "AAL", "OS": "AUA", "SK": "SAS", "AY": "FIN", "LO": "LOT",
    "IB": "IBE", "AZ": "ITY", "CX": "CPA", "BR": "EVA", "KE": "KAL", "OZ": "AAR", "MS": "MSR", "SV": "SVA",
    "WY": "OMA", "GF": "GFA", "KU": "KAC", "RJ": "RJA", "SN": "BEL", "TP": "TAP", "EI": "EIN", "HY": "UZB",
    "KC": "KZR", "J2": "AHY", "PK": "PIA", "UL": "ALK", "BG": "BBC", "QF": "QFA", "MH": "MAS", "TG": "THA",
}


def fa_ident(flight: str) -> str:
    """FlightAware redirects IATA idents (AF226) to a summary page; history needs ICAO (AFR226)."""
    import re
    m = re.match(r"^([A-Z0-9]{2})(\d+)$", flight)
    if m and m.group(1) in AIRLINE_ICAO:
        return AIRLINE_ICAO[m.group(1)] + m.group(2)
    return flight


def verify_batch(flights: list[tuple[str, str, str]], cfg: dict, cache: dict,
                 max_flights: int = 4, timeout_s: int = 600) -> dict:
    """flights: [(flight, origin, dest)] in priority order. Updates cache in place."""
    a = cfg["airspace"]
    now = dt.datetime.now(dt.timezone.utc)
    todo = []
    for f, o, d in flights:
        key = f"{f}:{o}-{d}"
        hit = cache.get(key)
        if hit and hit.get("status") != "unverified":
            age_h = (now - dt.datetime.fromisoformat(hit["checked_at"])).total_seconds() / 3600
            ttl = a["cache_ttl_hours"] * (2 if hit["status"] == "compliant" else 1)
            if age_h < ttl:
                continue
        if (f, o, d) not in todo:
            todo.append((f, o, d))
    todo = todo[:max_flights]
    if not todo:
        return {"checked": 0, "blocked": False}
    with tempfile.TemporaryDirectory() as td:
        job, out = Path(td) / "job.json", Path(td) / "out.json"
        job.write_text(json.dumps({
            "flights": [{"ident": fa_ident(f), "key_flight": f, "origin": o, "dest": d,
                         "origin_icao": ICAO.get(o), "dest_icao": ICAO.get(d),
                         "pattern": (cache.get(f"{f}:{o}-{d}") or {}).get("pattern")}
                        for f, o, d in todo],
            "per_flight": 1, "delay_s": [15, 25]}))
        try:
            subprocess.run(["node", str(JS), str(job), str(out)], timeout=timeout_s,
                           capture_output=True, check=False)
            res = json.loads(out.read_text())
        except (subprocess.TimeoutExpired, FileNotFoundError, ValueError) as e:
            return {"checked": 0, "blocked": False, "error": str(e)}
    for f, o, d in todo:
        key = f"{f}:{o}-{d}"
        r = res.get("results", {}).get(key, {"error": "not attempted"})
        prev_pattern = (cache.get(key) or {}).get("pattern")
        cache[key] = classify(f, o, d, r, cfg, now)
        pat = next((t.get("pattern") for t in r.get("tracks", []) if t.get("pattern")), prev_pattern)
        if pat:
            cache[key]["pattern"] = pat
    return {"checked": len(todo), "blocked": res.get("blocked", False)}


def classify(f, o, d, r, cfg, now) -> dict:
    a = cfg["airspace"]
    v = {"flight": f, "origin": o, "dest": d, "checked_at": now.isoformat(), "tracks": [],
         "source": "flightaware-public"}
    for t in r.get("tracks", []):
        c = geo.classify_track([tuple(p) for p in t["points"]], a["excluded_countries"],
                               a["border_buffer_km"], a["excluded_sea_zones"])
        v["tracks"].append({"date": t["date"], "operator": t.get("operator", ""),
                            "countries": c["countries_crossed"], "violations": c["violations"],
                            "warnings": c["warnings"]})
    if not v["tracks"]:
        v.update(status="unverified", reason=r.get("error") or "no usable tracks")
    elif any(t["violations"] for t in v["tracks"]):
        v.update(status="violates", reason="; ".join(sorted({k for t in v["tracks"] for k in t["violations"]})))
    else:
        warn = sorted({k for t in v["tracks"] for k in t["warnings"]})
        v.update(status="compliant", reason=f"track {v['tracks'][0]['date']} clean"
                 + (f" (skirts: {', '.join(warn)})" if warn else ""))
    return v
