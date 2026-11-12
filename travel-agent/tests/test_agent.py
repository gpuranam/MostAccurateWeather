import datetime as dt
import json
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import geo  # noqa: E402
import rank  # noqa: E402
import run  # noqa: E402
import search  # noqa: E402

CFG = yaml.safe_load((ROOT / "trip.yaml").read_text())
EXCL = CFG["airspace"]["excluded_countries"]

# (lon, lat) waypoints
EWR_DEL_CAUCASUS = [(-74.2, 40.7), (-50, 50), (-20, 55), (0, 52), (10, 50), (20, 48), (30, 44.5),
                    (40, 42.5), (46, 41.5), (49.9, 40.4), (53, 40.2), (61, 39.6), (67, 36.7),
                    (71.5, 34.0), (74.5, 31.5), (77.1, 28.6)]
EWR_DEL_IRAQ = [(-74.2, 40.7), (-20, 52), (10, 48), (28.9, 41.0), (38, 38.5), (44.4, 33.3),
                (50.5, 28.5), (58, 24), (66, 23), (72.8, 19.1), (77.1, 28.6)]
RED_SEA_ONLY = [(32.5, 29.9), (34.6, 27.5), (36.8, 24.0), (38.6, 20.5), (40.5, 17.0)]
PACIFIC = [(-77.4, 38.9), (-120, 55), (-170, 55), (160, 45), (140.4, 35.8)]


def test_country_names_resolve():
    cs = geo.countries()
    for n in EXCL:
        assert n in cs, n


def test_caucasus_route_is_compliant():
    c = geo.classify_track(EWR_DEL_CAUCASUS, EXCL, 20)
    assert c["compliant"], c["violations"]
    assert "Azerbaijan" in c["countries_crossed"]
    assert "Pakistan" in c["countries_crossed"]


def test_iraq_route_violates():
    c = geo.classify_track(EWR_DEL_IRAQ, EXCL, 20)
    assert not c["compliant"]
    assert "Iraq" in c["violations"]


def test_red_sea_corridor_violates_even_over_water():
    c = geo.classify_track(RED_SEA_ONLY, ["Iran"], 0)  # Egypt/Saudi not listed; sea zone must catch it
    assert "Red Sea" in c["violations"]


def test_pacific_route_crosses_antimeridian_and_is_clean():
    c = geo.classify_track(PACIFIC, EXCL, 20)
    assert c["compliant"]
    assert "Japan" in c["countries_crossed"]


# ---------------------------------------------------------------------------
# Synthetic Google Flights responses
def seg(fn, a, b, dep, arr, mins, airline="United"):
    return {"airline": airline, "flight_number": fn, "departure_airport": {"id": a, "time": dep},
            "arrival_airport": {"id": b, "time": arr}, "duration": mins}


def itin(flights, layovers, price, token=None):
    return {"flights": flights, "layovers": [{"id": l[0], "duration": l[1]} for l in layovers],
            "total_duration": sum(f["duration"] for f in flights) + sum(l[1] for l in layovers),
            "price": price, "departure_token": token}


def fake_query(self, **p):
    self.used += 1
    multi = p.get("type") == 3
    if multi:
        legs = json.loads(p["multi_city_json"])
        d0, rdate = legs[0]["date"], legs[1]["date"]
        home = legs[1]["arrival_id"]
        if not p.get("departure_token"):
            return {"best_flights": [
                itin([seg("UA 2133", "IAD", "EWR", f"{d0} 11:00", f"{d0} 12:20", 80),
                      seg("UA 82", "EWR", "DEL", f"{d0} 21:15", f"{d0} 21:30", 855),
                      seg("AI 2802", "DEL", "BOM", f"{d0} 23:59", f"{d0} 02:10", 130, "Air India")],
                     [("EWR", 535), ("DEL", 209)], 1500, token="B1"),
                itin([seg("AI 106", "EWR", "BOM", f"{d0} 20:00", f"{d0} 21:00", 900, "Air India")], [], 1100,
                     token="B2")]}
        return {"best_flights": [
            itin([seg("AI 861", "PNQ", "DEL", f"{rdate} 14:00", f"{rdate} 16:10", 130, "Air India"),
                  seg("UA 83", "DEL", "EWR", f"{rdate} 23:55", f"{rdate} 05:30", 960)]
                 + ([seg("UA 2134", "EWR", "IAD", f"{rdate} 09:00", f"{rdate} 10:15", 75)] if home == "IAD" else []),
                 [("DEL", 465)] + ([("EWR", 210)] if home == "IAD" else []), 1600)]}
    o, d0, rdate = p["departure_id"], p["outbound_date"], p["return_date"]
    if not p.get("departure_token"):
        out = [itin(([seg("UA 2133", "IAD", "EWR", f"{d0} 11:00", f"{d0} 12:20", 80)] if o == "IAD" else [])
                    + [seg("UA 82", "EWR", "DEL", f"{d0} 21:15", f"{d0} 21:30", 855),
                       seg("AI 861", "DEL", "PNQ", f"{d0} 06:00", f"{d0} 08:10", 130, "Air India")],
                    ([("EWR", 535)] if o == "IAD" else []) + [("DEL", 510)], 3000 if o == "IAD" else 2700,
                    token=f"A-{o}-UA"),
               itin([seg("AI 106", "EWR", "DEL", f"{d0} 20:00", f"{d0} 21:00", 840, "Air India"),
                     seg("AI 863", "DEL", "PNQ", f"{d0} 23:30", f"{d0} 01:40", 130, "Air India")],
                    [("DEL", 150)], 2200, token=f"A-{o}-AI"),
               itin([seg("EK 232", "IAD", "DXB", f"{d0} 22:00", f"{d0} 19:00", 780, "Emirates"),
                     seg("EK 506", "DXB", "PNQ", f"{d0} 21:00", f"{d0} 01:40", 190, "Emirates")],
                    [("DXB", 120)], 1800, token=f"A-{o}-EK")]
        return {"best_flights": out}
    tail = [seg("UA 2134", "EWR", "IAD", f"{rdate} 09:00", f"{rdate} 10:15", 75)] if o == "IAD" else []
    base = 3100 if o == "IAD" else 2800
    if p["departure_token"].endswith("AI"):
        return {"best_flights": [itin([seg("AI 862", "PNQ", "DEL", f"{rdate} 14:00", f"{rdate} 16:10", 130, "Air India"),
                                       seg("AI 105", "DEL", "EWR", f"{rdate} 02:00", f"{rdate} 07:00", 960, "Air India")],
                                      [("DEL", 590)], 2300)]}
    return {"best_flights": [itin([seg("AI 861", "PNQ", "DEL", f"{rdate} 14:00", f"{rdate} 16:10", 130, "Air India"),
                                   seg("UA 83", "DEL", "EWR", f"{rdate} 23:55", f"{rdate} 05:30", 960)] + tail,
                                  [("DEL", 465)] + ([("EWR", 210)] if o == "IAD" else []), base)]}


def verdicts_now():
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    v = {}
    for f, o, d, st in [("UA82", "EWR", "DEL", "compliant"), ("UA83", "DEL", "EWR", "compliant"),
                        ("AI106", "EWR", "DEL", "violates"), ("AI105", "DEL", "EWR", "violates"),
                        ("AI106", "EWR", "BOM", "violates")]:
        v[f"{f}:{o}-{d}"] = {"flight": f, "origin": o, "dest": d, "status": st, "checked_at": now,
                             "reason": "test", "tracks": []}
    return v


def test_end_to_end_ranking(monkeypatch, tmp_path):
    cfg = yaml.safe_load((ROOT / "trip.yaml").read_text())
    cfg["search"]["provider"] = "serpapi"
    cfg["search"]["max_queries_per_run"] = {"full": 45, "light": 12}
    cfg["airspace"]["track_source"] = "aeroapi"
    cfg_path = tmp_path / "trip.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg))
    monkeypatch.setattr(search.SerpApiClient, "query", fake_query)
    monkeypatch.setattr(run.routes, "load_cache", verdicts_now)
    monkeypatch.setattr(run.routes, "save_cache", lambda c: None)
    monkeypatch.setattr(run.routes, "verify_flight", lambda *a, **k: pytest.fail("should use cache"))
    monkeypatch.setattr(run, "HISTORY", tmp_path / "history.json")
    monkeypatch.setattr(run, "HERE", tmp_path)
    out = tmp_path / "report.md"
    assert run.main(["--mode", "light", "--out", str(out), "--config", str(cfg_path)]) == 0
    latest = json.loads((tmp_path / "latest.json").read_text())
    top = latest["plans"][0]
    # Emirates (DXB) and Air India (violates) never rank; United via EWR wins.
    assert top["status"] == "compliant"
    assert "UA82" in top["a"]["out"] and "UA83" in top["a"]["back"]
    assert top["b"] is not None and top["b"]["back"] == top["a"]["back"]
    for p in latest["plans"]:
        assert not any(f.startswith("EK") or f in ("AI106", "AI105") for f in p["a"]["out"] + p["a"]["back"])
    text = out.read_text()
    assert "Rejected for airspace" in text


def test_parse_flags_separate_tickets_and_basic():
    payload = {"best_flights": [{"flights": [seg("UA 82", "EWR", "DEL", "2026-12-11 21:15", "2026-12-12 21:30", 855)
                                             | {"extensions": ["Basic economy"]}],
                                 "layovers": [], "total_duration": 855, "price": 999,
                                 "extensions": ["Separate tickets booked together"]}]}
    o = search.parse_options(payload)[0]
    assert o.flights == ["UA82"] and o.basic_economy and o.separate_tickets


def test_tight_international_to_domestic_connection_penalised():
    s1 = search.Segment("United", "UA82", "EWR", "DEL", "", "", 855)
    s2 = search.Segment("Air India", "AI861", "DEL", "PNQ", "", "", 130)
    opt = search.Option([s1, s2], [{"airport": "DEL", "minutes": 95, "overnight": False}], 1080, 1000)
    pen, notes = rank.connection_issues(opt, CFG)
    assert pen > 0 and "tight" in notes[0]


# ---------------------------------------------------------------------------
# Free providers, offline (real payloads captured 2026-11-12)
FIX = ROOT / "tests" / "fixtures"


def test_gflights_parses_real_round_trip_payload():
    import gflights
    opts = gflights.parse_body((FIX / "gf_rt_iad_pnq_stage1.txt").read_text())
    assert len(opts) >= 6
    cheapest = min(opts, key=lambda o: o.price)
    assert cheapest.price == 6888
    assert cheapest.flights == ["AF51", "AF226", "6E5276"]
    assert [l["airport"] for l in cheapest.layovers] == ["CDG", "DEL"]
    assert cheapest.segments[0].origin == "IAD" and cheapest.segments[-1].dest == "PNQ"
    assert cheapest.segments[0].dep.startswith("2026-12-11")
    assert any("also sold as" in e for s in cheapest.segments for e in s.extensions)


def test_gflights_url_roundtrip_and_multicity():
    import gflights
    c = gflights.GFlightsClient(budget=1)
    u = c.url([("IAD", "PNQ", "2026-12-11"), ("PNQ", "IAD", "2027-01-05")], "round-trip", {"adults": 2}, 2)
    assert u.startswith("https://www.google.com/travel/flights/search?tfs=") and "curr=USD" in u
    m = c.url([("IAD", "BOM", "2026-12-18"), ("PNQ", "EWR", "2027-01-05")], "multi-city", {"adults": 1}, 2)
    assert m != u


def test_real_ua82_track_classifies_compliant():
    import fa_public
    t = json.loads((FIX / "ua82_track_20261111.json").read_text())
    v = fa_public.classify("UA82", "EWR", "DEL", {"tracks": [t]}, CFG, dt.datetime.now(dt.timezone.utc))
    assert v["status"] == "compliant", v
    assert "Azerbaijan" in v["tracks"][0]["countries"] and "Pakistan" in v["tracks"][0]["countries"]


def test_gflights_choose_prefers_verified_routing():
    import gflights
    mk = lambda fn, price, carrier: search.Option(
        [search.Segment(carrier, fn, "EWR", "DEL", "2026-12-11 20:35", "2026-12-12 21:50", 885)], [], 885, price)
    opts = [mk("AI106", 900, "Air India"), mk("LH403", 1000, "Lufthansa"), mk("XX1", 1100, "X"),
            mk("UA82", 1500, "United")]
    v = verdicts_now()
    picks = gflights._choose(CFG, v, 3)(opts)
    flights = [p.flights[0] for p in picks]
    assert "AI106" not in flights          # known violator never expanded
    assert "UA82" in flights               # verified routing always expanded


def test_attach_domestic_enforces_self_transfer_buffer(monkeypatch):
    import gflights
    seg = lambda fn, a, b, dep, arr, m: search.Segment("X", fn, a, b, dep, arr, m)
    intl_out = search.Option([seg("UA82", "EWR", "DEL", "2026-12-11 20:35", "2026-12-12 21:50", 885)], [], 885, 5947)
    intl_back = search.Option([seg("UA83", "DEL", "EWR", "2027-01-05 23:55", "2027-01-06 05:30", 960)], [], 960, 5947)
    doms = {
        ("DEL", "PNQ"): [search.Option([seg("6E2618", "DEL", "PNQ", "2026-12-12 22:55", "2026-12-13 01:10", 135)], [], 135, 151),
                         search.Option([seg("6E2343", "DEL", "PNQ", "2026-12-13 02:15", "2026-12-13 04:25", 130)], [], 130, 151)],
        ("PNQ", "DEL"): [search.Option([seg("6E2285", "PNQ", "DEL", "2027-01-05 14:25", "2027-01-05 16:35", 130)], [], 130, 159),
                         search.Option([seg("6E9999", "PNQ", "DEL", "2027-01-05 20:00", "2027-01-05 22:10", 130)], [], 130, 159)],
    }
    monkeypatch.setattr(gflights, "domestic_options",
                        lambda a, b, d, pax, errs: [o for o in doms[(a, b)] if o.segments[0].dep[:10] == d])
    # 02:15 is 265 min after a 21:50 landing: too tight for a 270-min self-transfer...
    assert gflights.attach_domestic(intl_out, "DEL", "PNQ", {"adults": 2}, 270, True, []) is None
    # ...but fine at 240; 22:55 (65 min) is always rejected.
    out = gflights.attach_domestic(intl_out, "DEL", "PNQ", {"adults": 2}, 240, True, [])
    assert out.flights == ["UA82", "6E2343"] and out.separate_tickets
    assert out.layovers[-1]["minutes"] == 265 and out.price == 151
    back = gflights.attach_domestic(intl_back, "PNQ", "DEL", {"adults": 2}, 270, False, [])
    assert back.flights == ["6E2285", "UA83"]          # 22:10 arrival leaves < 270 min before 23:55
