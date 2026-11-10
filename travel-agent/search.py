"""Fare search via SerpApi's Google Flights engine.

Auth: the session's network proxy injects `Authorization: Bearer <key>` for
serpapi.com (request_credential). If SERPAPI_KEY is set in the environment it is
sent as the `api_key` query parameter instead (local runs).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import requests

SERPAPI_URL = "https://serpapi.com/search.json"
CABIN = {"economy": 1, "premium_economy": 2, "business": 3, "first": 4}
RAW_DIR = Path(__file__).parent / "cache" / "raw"


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class Segment:
    carrier: str
    flight: str          # "UA82"
    origin: str
    dest: str
    dep: str             # "2026-12-11 21:15" (local)
    arr: str
    minutes: int
    aircraft: str = ""
    extensions: list[str] = field(default_factory=list)


@dataclass
class Option:
    """One leg's choice as returned by Google Flights (one direction)."""
    segments: list[Segment]
    layovers: list[dict]
    total_minutes: int
    price: float | None            # Google's price for the whole ticket so far
    departure_token: str | None = None
    booking_token: str | None = None
    separate_tickets: bool = False
    basic_economy: bool = False

    @property
    def flights(self) -> list[str]:
        return [s.flight for s in self.segments]

    @property
    def key(self) -> str:
        return "-".join(f"{s.flight}@{s.dep[:10]}" for s in self.segments)


def norm_flight(fn: str) -> str:
    return (fn or "").replace(" ", "").upper()


def parse_options(payload: dict) -> list[Option]:
    out = []
    for bucket in ("best_flights", "other_flights"):
        for it in payload.get(bucket, []) or []:
            segs = []
            exts_all: list[str] = []
            for f in it.get("flights", []):
                exts = [str(e) for e in f.get("extensions", []) or []]
                exts_all += exts
                segs.append(Segment(
                    carrier=f.get("airline", ""),
                    flight=norm_flight(f.get("flight_number", "")),
                    origin=f.get("departure_airport", {}).get("id", ""),
                    dest=f.get("arrival_airport", {}).get("id", ""),
                    dep=f.get("departure_airport", {}).get("time", ""),
                    arr=f.get("arrival_airport", {}).get("time", ""),
                    minutes=int(f.get("duration") or 0),
                    aircraft=f.get("airplane", "") or "",
                    extensions=exts,
                ))
            if not segs:
                continue
            blob = " ".join(exts_all + [str(e) for e in it.get("extensions", []) or []]).lower()
            out.append(Option(
                segments=segs,
                layovers=[{"airport": l.get("id", ""), "minutes": int(l.get("duration") or 0),
                           "overnight": bool(l.get("overnight"))} for l in it.get("layovers", []) or []],
                total_minutes=int(it.get("total_duration") or sum(s.minutes for s in segs)),
                price=float(it["price"]) if it.get("price") is not None else None,
                departure_token=it.get("departure_token"),
                booking_token=it.get("booking_token"),
                separate_tickets="separate ticket" in blob or "self transfer" in blob
                                 or "self-transfer" in blob,
                basic_economy="basic economy" in blob,
            ))
    return out


class SerpApiClient:
    def __init__(self, budget: int, hl="en", gl="us", currency="USD", fixtures_dir: Path | None = None):
        self.budget = budget
        self.used = 0
        self.hl, self.gl, self.currency = hl, gl, currency
        self.fixtures_dir = fixtures_dir
        self.memo: dict[str, dict] = {}
        self.errors: list[str] = []

    def _fixture(self, params: dict) -> dict:
        # Fixture files are matched on a stable hash of params (minus auth).
        h = _phash(params)
        p = self.fixtures_dir / f"{h}.json"
        if p.exists():
            return json.loads(p.read_text())
        return {}

    def query(self, **params) -> dict:
        params = {"engine": "google_flights", "hl": self.hl, "gl": self.gl,
                  "currency": self.currency, **{k: v for k, v in params.items() if v is not None}}
        h = _phash(params)
        if h in self.memo:
            return self.memo[h]
        if self.fixtures_dir is not None:
            data = self._fixture(params)
            self.memo[h] = data
            return data
        if self.used >= self.budget:
            raise BudgetExceeded(f"query budget {self.budget} reached")
        self.used += 1
        send = dict(params)
        if os.environ.get("SERPAPI_KEY"):
            send["api_key"] = os.environ["SERPAPI_KEY"]
        data: dict = {}
        for attempt in range(3):
            try:
                r = requests.get(SERPAPI_URL, params=send, timeout=90)
                if r.status_code in (429, 500, 502, 503, 504):
                    time.sleep(2 ** (attempt + 1))
                    continue
                data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {"error": r.text[:300]}
                if r.status_code >= 400 and "error" not in data:
                    data["error"] = f"HTTP {r.status_code}"
                break
            except requests.RequestException as e:  # network hiccup
                data = {"error": str(e)}
                time.sleep(2 ** (attempt + 1))
        if data.get("error"):
            self.errors.append(f"{params.get('departure_id')}->{params.get('arrival_id')} "
                               f"{params.get('outbound_date', '')}: {data['error']}")
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        (RAW_DIR / f"{h}.json").write_text(json.dumps({"params": params, "response": data})[:2_000_000])
        self.memo[h] = data
        return data


def _phash(params: dict) -> str:
    clean = {k: v for k, v in params.items() if k != "api_key"}
    return hashlib.sha1(json.dumps(clean, sort_keys=True).encode()).hexdigest()[:16]


def date_window(preferred: dt.date, flex: int) -> list[dt.date]:
    """Preferred first, then +/-1, +/-2 ... so budget cuts drop the extremes."""
    days = [preferred]
    for k in range(1, flex + 1):
        days += [preferred + dt.timedelta(days=k), preferred - dt.timedelta(days=k)]
    return days


def _pax(p: dict) -> dict:
    return {"adults": p.get("adults", 1), "children": p.get("children", 0) or None}


def banned(opt: Option, banned_airports: set[str]) -> bool:
    return any(l["airport"] in banned_airports for l in opt.layovers) or \
        any(s.origin in banned_airports or s.dest in banned_airports for s in opt.segments)


@dataclass
class TicketItin:
    ticket: str                    # "A" or "B"
    origin: str
    out: Option
    back: Option
    price: float                   # total ticket price (all pax on the ticket)
    out_date: str
    back_date: str
    note: str = ""

    def to_json(self):
        d = asdict(self)
        return d


def search_ticket_a(cfg: dict, client: SerpApiClient, mode: str) -> list[TicketItin]:
    a = cfg["ticket_a"]
    banned_ap = set(cfg["airspace"]["banned_connection_airports"])
    cabin = CABIN[cfg.get("cabin", "economy")]
    k = cfg["search"]["outbound_candidates_for_returns"]
    out_pref = dt.date.fromisoformat(str(a["outbound"]["preferred"]))
    ret_pref = dt.date.fromisoformat(str(a["return"]["preferred"]))
    flex_o = a["outbound"]["flex_days"] if mode == "full" else 0
    flex_r = a["return"]["flex_days"] if mode == "full" else 0
    # Date pairs: vary outbound with preferred return, then vary return with preferred outbound.
    pairs = [(d, ret_pref) for d in date_window(out_pref, flex_o)]
    pairs += [(out_pref, d) for d in date_window(ret_pref, flex_r) if d != ret_pref]
    results: list[TicketItin] = []
    for origin in a["origins"]:
        for d_out, d_ret in pairs:
            base = dict(type=1, departure_id=origin, arrival_id=a["destination"],
                        outbound_date=d_out.isoformat(), return_date=d_ret.isoformat(),
                        travel_class=cabin, stops=0, **_pax(a["passengers"]))
            try:
                outs = [o for o in parse_options(client.query(**base)) if not banned(o, banned_ap)]
            except BudgetExceeded:
                return results
            outs.sort(key=lambda o: (o.price or 1e9))
            for o in outs[:k]:
                if not o.departure_token:
                    continue
                try:
                    backs = parse_options(client.query(**base, departure_token=o.departure_token))
                except BudgetExceeded:
                    return results
                for b in backs:
                    if banned(b, banned_ap) or b.price is None:
                        continue
                    results.append(TicketItin("A", origin, o, b, b.price,
                                              d_out.isoformat(), d_ret.isoformat()))
    return results


def search_ticket_b(cfg: dict, client: SerpApiClient, mode: str,
                    a_returns: list[tuple[str, str, list[str]]]) -> list[TicketItin]:
    """a_returns: [(return_date, destination_airport, flights)] of the best A plans.

    Wife's ticket is an open jaw: origin -> BOM, then PNQ -> (A's return airport)
    on A's return date. We keep only second legs whose flight numbers equal A's.
    """
    b = cfg["ticket_b"]
    banned_ap = set(cfg["airspace"]["banned_connection_airports"])
    cabin = CABIN[cfg.get("cabin", "economy")]
    k = cfg["search"]["outbound_candidates_for_returns"]
    out_pref = dt.date.fromisoformat(str(b["outbound"]["preferred"]))
    dates = date_window(out_pref, b["outbound"]["flex_days"] if mode == "full" else 0)
    results: list[TicketItin] = []
    seen_targets = []
    for t in a_returns:
        if t[:2] not in [s[:2] for s in seen_targets]:
            seen_targets.append(t)
    for ret_date, ret_airport, _ in seen_targets[:2]:
        wanted_sets = {tuple(f) for (rd, ra, f) in a_returns if rd == ret_date and ra == ret_airport}
        for d in dates:
            legs = [{"departure_id": b["origin"], "arrival_id": b["destination"], "date": d.isoformat()},
                    {"departure_id": cfg["ticket_a"]["destination"], "arrival_id": ret_airport,
                     "date": ret_date}]
            base = dict(type=3, multi_city_json=json.dumps(legs), travel_class=cabin, stops=0,
                        **_pax(b["passengers"]))
            try:
                outs = [o for o in parse_options(client.query(**base)) if not banned(o, banned_ap)]
            except BudgetExceeded:
                return results
            outs.sort(key=lambda o: (o.price or 1e9))
            for o in outs[:k]:
                if not o.departure_token:
                    continue
                try:
                    backs = parse_options(client.query(**base, departure_token=o.departure_token))
                except BudgetExceeded:
                    return results
                for bk in backs:
                    if bk.price is None or banned(bk, banned_ap):
                        continue
                    match = tuple(bk.flights) in wanted_sets
                    results.append(TicketItin("B", b["origin"], o, bk, bk.price, d.isoformat(), ret_date,
                                              note="" if match else "return differs from Ticket A"))
    return results
