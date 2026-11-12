"""Free fare source: Google Flights driven through headless Chromium.

Builds `tfs` search URLs (via the open-source fast-flights package), runs
js/gflights_page.cjs, and parses Google's own GetShoppingResults JSON into the
same Option/Segment objects the rest of the agent uses.

Google rate-limits shared cloud IPs (~20 searches / 30 min observed), so a run
does only a handful of page searches: preferred dates every run, plus one
rotating alternate date pair in "full" mode.
"""
from __future__ import annotations

import datetime as dt
import json
import subprocess
import tempfile
import time
from pathlib import Path

from fast_flights import Passengers, create_query
from fast_flights.querying import FlightQuery

from search import Option, Segment, TicketItin, banned, date_window  # noqa: F401

JS = Path(__file__).parent / "js" / "gflights_page.cjs"


# ---------------------------------------------------------------- parsing
def extract_inner(body: str) -> list:
    """GetShoppingResults bodies are batchexecute chunks; return inner payloads."""
    if not body:
        return []
    dec = json.JSONDecoder()
    out, i = [], 0
    while i < len(body):
        j = body.find("[", i)
        if j < 0:
            break
        try:
            obj, i = dec.raw_decode(body, j)
        except ValueError:
            i = j + 1
            continue
        for entry in obj if isinstance(obj, list) else []:
            if isinstance(entry, list) and len(entry) > 2 and isinstance(entry[2], str) \
                    and entry[2].startswith("["):
                try:
                    out.append(json.loads(entry[2]))
                except ValueError:
                    pass
    return out


def _hm(v) -> tuple[int, int]:
    v = list(v or []) + [None, None]
    return (v[0] or 0, v[1] or 0)


def _stamp(date, hm) -> str:
    y, m, d = date
    return f"{y:04d}-{m:02d}-{d:02d} {hm[0]:02d}:{hm[1]:02d}"


def parse_payload(inner: list) -> list[Option]:
    opts: list[Option] = []
    for blk in (2, 3):
        if len(inner) <= blk or not inner[blk] or not inner[blk][0]:
            continue
        for it in inner[blk][0]:
            try:
                f = it[0]
                price = it[1][0][1] if it[1] and it[1][0] else None
                segs = []
                for s in f[2]:
                    code, num = (s[22] or [None, None])[:2]
                    segs.append(Segment(
                        carrier=(s[22][3] if s[22] and len(s[22]) > 3 else "") or "",
                        flight=f"{code}{num}", origin=s[3], dest=s[6],
                        dep=_stamp(s[20], _hm(s[8])), arr=_stamp(s[21], _hm(s[10])),
                        minutes=int(s[11] or 0), aircraft=s[17] or "",
                        extensions=[f"also sold as {c[0]}{c[1]}" for c in (s[15] or []) if c]))
                lays = []
                for k, l in enumerate(f[13] or []):
                    a, b = segs[k], segs[k + 1] if k + 1 < len(segs) else segs[k]
                    lays.append({"airport": l[1], "minutes": int(l[0] or 0),
                                 "overnight": a.arr[:10] != b.dep[:10] and int(b.dep[11:13]) < 10})
                opts.append(Option(segments=segs, layovers=lays,
                                   total_minutes=int(f[9] or sum(x.minutes for x in segs)),
                                   price=float(price) if price is not None else None))
            except (IndexError, TypeError, KeyError):
                continue
    return opts


def parse_body(body: str) -> list[Option]:
    seen, out = set(), []
    for inner in extract_inner(body):
        for o in parse_payload(inner):
            k = (o.key, o.price)
            if k not in seen:
                seen.add(k)
                out.append(o)
    return out


def money(p: float) -> str:
    return f"${p:,.0f}"


# ---------------------------------------------------------------- client
class GFlightsClient:
    def __init__(self, budget: int, timeout_s: int = 240):
        self.budget = budget          # page searches (each = 1 + picks shopping calls)
        self.used = 0
        self.blocked = False
        self.errors: list[str] = []
        self.timeout_s = timeout_s
        self.log: list[dict] = []     # per-search diagnostics (written to latest.json)

    def url(self, legs: list[tuple[str, str, str]], trip: str, pax: dict, max_stops: int | None) -> str:
        q = create_query(
            flights=[FlightQuery(date=d, from_airport=a, to_airport=b, max_stops=max_stops) for a, b, d in legs],
            trip=trip, seat="economy",
            passengers=Passengers(adults=pax.get("adults", 1), children=pax.get("children") or 0),
            language="en", currency="USD")
        return q.url() + "&gl=us"

    def search(self, url: str, choose, label: str = "") -> tuple[list[Option], list[tuple[Option, list[Option]]]]:
        """choose(stage1_options) -> options to expand. Returns (stage1, [(picked, stage2)]).

        One browser session per search: the page driver writes stage 1, we pick,
        it clicks the picks in the same session.
        """
        if self.blocked or self.used >= self.budget:
            return [], []
        self.used += 1
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            job, out = td / "job.json", td / "out.json"
            s1f, pf = td / "stage1.txt", td / "picks.json"
            job.write_text(json.dumps({"url": url, "timeout_s": 35,
                                       "stage1_file": str(s1f), "picks_file": str(pf)}))
            proc = subprocess.Popen(["node", str(JS), str(job), str(out)],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            t0 = time.time()
            while not s1f.exists() and proc.poll() is None and time.time() - t0 < 90:
                time.sleep(0.3)
            stage1 = parse_body(s1f.read_text()) if s1f.exists() else []
            picks = choose(stage1) if stage1 else []
            pf.write_text(json.dumps([{"price": money(p.price), "airline": p.segments[0].carrier}
                                      for p in picks]))
            try:
                proc.wait(timeout=self.timeout_s)
            except subprocess.TimeoutExpired:
                proc.kill()
                self.errors.append("page driver timed out")
            try:
                res = json.loads(out.read_text())
            except (FileNotFoundError, ValueError):
                self.errors.append("page driver produced no output")
                return stage1, []
        if res.get("blocked"):
            self.blocked = True
            self.errors.append("Google Flights rate-limited this run (will retry next run)")
        self.errors += [f"{label}: {e}" for e in res.get("errors", [])]
        pairs = []
        for p in picks:
            body = next((s["body"] for s in res.get("stage2", [])
                         if s["pick"]["price"] == money(p.price)
                         and s["pick"]["airline"] == p.segments[0].carrier), None)
            if body:
                pairs.append((p, parse_body(body)))
        self.log.append({"search": label, "stage1": len(stage1), "picked": [x.flights for x in picks],
                         "expanded": [len(b) for _, b in pairs], "errors": res.get("errors", []),
                         "blocked": bool(res.get("blocked"))})
        return stage1, pairs


# ---------------------------------------------------------------- strategy
def _choose(cfg, verdicts, k):
    banned_ap = set(cfg["airspace"]["banned_connection_airports"])

    def status(o: Option) -> int:
        st = [verdicts.get(f"{s.flight}:{s.origin}-{s.dest}", {}).get("status") for s in o.segments]
        return 2 if "violates" in st else (0 if any(x == "compliant" for x in st) else 1)

    def choose(opts: list[Option]) -> list[Option]:
        ok = [o for o in opts if o.price and not banned(o, banned_ap) and status(o) < 2]
        ok.sort(key=lambda o: o.price)
        picks = ok[: max(1, k - 1)]
        known_good = [o for o in ok if status(o) == 0 and o not in picks]
        if known_good:                      # always expand the best already-verified routing
            picks.append(known_good[0])
        elif len(ok) > len(picks):
            picks.append(ok[len(picks)])
        return picks[:k]
    return choose


def date_pairs(cfg, mode: str, rotation: int) -> list[tuple[dt.date, dt.date]]:
    a = cfg["ticket_a"]
    o = dt.date.fromisoformat(str(a["outbound"]["preferred"]))
    r = dt.date.fromisoformat(str(a["return"]["preferred"]))
    pairs = [(o, r)]
    if mode == "full":
        alts = [(d, r) for d in date_window(o, a["outbound"]["flex_days"])[1:]] + \
               [(o, d) for d in date_window(r, a["return"]["flex_days"])[1:]]
        if alts:
            pairs.append(alts[rotation % len(alts)])
    return pairs


# ---------------------------------------------------------------- domestic legs
_DOM_MEMO: dict = {}


def domestic_options(a: str, b: str, date: str, pax: dict, errors: list) -> list[Option]:
    """Nonstop one-ways from Google's server-rendered page (no browser, 1 request)."""
    import os

    from fast_flights import fetch_flights_html
    from selectolax.lexbor import LexborHTMLParser
    key = (a, b, date, pax.get("adults", 1), pax.get("children") or 0)
    if key in _DOM_MEMO:
        return _DOM_MEMO[key]
    opts: list[Option] = []
    try:
        q = create_query(flights=[FlightQuery(date=date, from_airport=a, to_airport=b, max_stops=0)],
                         trip="one-way", passengers=Passengers(adults=pax.get("adults", 1),
                                                               children=pax.get("children") or 0),
                         language="en", currency="USD")
        h = fetch_flights_html(q, proxy=os.environ.get("HTTPS_PROXY"))
        js = LexborHTMLParser(h).css_first(r"script.ds\:1").text()
        opts = [o for o in parse_payload(json.loads(js.split("data:", 1)[1].rsplit(",", 1)[0])) if o.price]
        time.sleep(3)
    except Exception as e:  # noqa: BLE001 - any scrape failure just means "no domestic data"
        errors.append(f"domestic {a}-{b} {date}: {type(e).__name__}")
    _DOM_MEMO[key] = opts
    return opts


def _t(stamp: str) -> dt.datetime:
    return dt.datetime.fromisoformat(stamp)


def attach_domestic(intl: Option, a: str, b: str, pax: dict, buffer_min: int, outbound: bool,
                    errors: list) -> Option | None:
    """Join an international leg with the best separate-ticket domestic flight.

    outbound: intl arrives at hub `a`, domestic a->b departs >= buffer after arrival.
    return:   domestic a->b arrives >= buffer before intl departs from hub `b`.
    """
    if outbound:
        arr = _t(intl.segments[-1].arr)
        cands = []
        for day in (arr.date(), arr.date() + dt.timedelta(days=1)):
            cands += domestic_options(a, b, day.isoformat(), pax, errors)
        ok = [o for o in cands if _t(o.segments[0].dep) >= arr + dt.timedelta(minutes=buffer_min)]
        if not ok:
            return None
        dom = min(ok, key=lambda o: (_t(o.segments[0].dep), o.price))
        lay = int((_t(dom.segments[0].dep) - arr).total_seconds() // 60)
        segs, lays = intl.segments + dom.segments, intl.layovers + [
            {"airport": a, "minutes": lay, "overnight": arr.date() != _t(dom.segments[0].dep).date()
             or _t(dom.segments[0].dep).hour < 6}]
    else:
        dep = _t(intl.segments[0].dep)
        cands = []
        for day in (dep.date() - dt.timedelta(days=1), dep.date()):
            cands += domestic_options(a, b, day.isoformat(), pax, errors)
        ok = [o for o in cands if _t(o.segments[-1].arr) <= dep - dt.timedelta(minutes=buffer_min)]
        if not ok:
            return None
        dom = max(ok, key=lambda o: (_t(o.segments[-1].arr), -o.price))
        lay = int((dep - _t(dom.segments[-1].arr)).total_seconds() // 60)
        segs, lays = dom.segments + intl.segments, [
            {"airport": b, "minutes": lay, "overnight": _t(dom.segments[-1].arr).date() != dep.date()}] + intl.layovers
    total = int((_t(segs[-1].arr) - _t(segs[0].dep)).total_seconds() // 60) if segs else 0
    # Local times differ by zone; fall back to summed durations when the clock math looks off.
    total = total if 0 < total < 4000 else sum(x.minutes for x in segs) + sum(l["minutes"] for l in lays)
    return Option(segments=segs, layovers=lays, total_minutes=intl.total_minutes + dom.total_minutes + lay,
                  price=dom.price, separate_tickets=True)


def search_ticket_a(cfg, client: GFlightsClient, mode: str, verdicts: dict, rotation: int) -> list[TicketItin]:
    a = cfg["ticket_a"]
    k = cfg["search"]["outbound_candidates_for_returns"]
    split = cfg["search"].get("split_hubs", ["DEL"])
    buf = cfg["scoring"]["min_connection_minutes"].get("self_transfer", 270)
    out: list[TicketItin] = []
    for d_out, d_ret in date_pairs(cfg, mode, rotation):
        for origin in a["origins"]:
            # (a) through tickets origin <-> destination (one ticket, protected connections)
            url = client.url([(origin, a["destination"], d_out.isoformat()),
                              (a["destination"], origin, d_ret.isoformat())],
                             "round-trip", a["passengers"], None)
            _, pairs = client.search(url, _choose(cfg, verdicts, k), f"A {origin}-{a['destination']} {d_out}/{d_ret}")
            for o, backs in pairs:
                for b in backs:
                    if b.price is not None and len(o.segments) <= 3 and len(b.segments) <= 3:
                        out.append(TicketItin("A", origin, o, b, b.price, d_out.isoformat(), d_ret.isoformat()))
            # (b) international round trip to a hub + separate domestic tickets (e.g. United EWR-DEL nonstop)
            for hub in split:
                url = client.url([(origin, hub, d_out.isoformat()), (hub, origin, d_ret.isoformat())],
                                 "round-trip", a["passengers"], 0 if origin == "EWR" else 1)
                _, pairs = client.search(url, _choose(cfg, verdicts, k), f"A-split {origin}-{hub} {d_out}/{d_ret}")
                for o, backs in pairs:
                    o2 = attach_domestic(o, hub, a["destination"], a["passengers"], buf, True, client.errors)
                    if not o2:
                        continue
                    for b in backs:
                        if b.price is None:
                            continue
                        b2 = attach_domestic(b, a["destination"], hub, a["passengers"], buf, False, client.errors)
                        if not b2:
                            continue
                        out.append(TicketItin("A", origin, o2, b2, b.price + o2.price + b2.price,
                                              d_out.isoformat(), d_ret.isoformat(),
                                              note=f"split: {origin}-{hub} round trip + separate {hub}-"
                                                   f"{a['destination']} domestic tickets"))
    return out


def search_ticket_b(cfg, client: GFlightsClient, mode: str, verdicts: dict, a_returns: list[dict]) -> list[TicketItin]:
    """a_returns: [{date, flights, intl_from, intl_to, intl_date, intl_flights, dom: Option|None}]."""
    b = cfg["ticket_b"]
    k = max(1, cfg["search"]["outbound_candidates_for_returns"] - 1)
    out: list[TicketItin] = []
    targets = []
    for t in a_returns:
        sig = (t["intl_from"], t["intl_to"], t["intl_date"])
        if sig not in [(x["intl_from"], x["intl_to"], x["intl_date"]) for x in targets]:
            targets.append(t)
    d = dt.date.fromisoformat(str(b["outbound"]["preferred"]))
    for t in targets[:1 if mode == "light" else 2]:
        wanted = {tuple(x["intl_flights"]) for x in a_returns
                  if (x["intl_from"], x["intl_to"], x["intl_date"]) == (t["intl_from"], t["intl_to"], t["intl_date"])}
        url = client.url([(b["origin"], b["destination"], d.isoformat()), (t["intl_from"], t["intl_to"], t["intl_date"])],
                         "multi-city", b["passengers"], None)
        _, pairs = client.search(url, _choose(cfg, verdicts, k), f"B {b['origin']}-{b['destination']} / {t['intl_from']}-{t['intl_to']}")
        for o, backs in pairs:
            for bk in backs:
                if bk.price is None:
                    continue
                price, back, note = bk.price, bk, ""
                if tuple(bk.flights) not in wanted:
                    note = "return differs from Ticket A"
                elif t.get("dom") is not None:      # same separate domestic flight as the family, 1 seat
                    dom_flight = t["dom"].segments[0].flight
                    opts = domestic_options(t["dom"].segments[0].origin, t["dom"].segments[0].dest,
                                            t["dom"].segments[0].dep[:10], b["passengers"], client.errors)
                    same = [x for x in opts if x.flights == [dom_flight]]
                    dom_price = same[0].price if same else (t["dom"].price or 0) / max(
                        1, cfg["ticket_a"]["passengers"]["adults"] + (cfg["ticket_a"]["passengers"].get("children") or 0))
                    back = Option(segments=t["dom"].segments + bk.segments,
                                  layovers=[{"airport": t["intl_from"], "minutes": t["dom_layover"], "overnight": False}]
                                  + bk.layovers, total_minutes=bk.total_minutes + t["dom"].total_minutes + t["dom_layover"],
                                  price=bk.price, separate_tickets=True)
                    price += dom_price
                    note = f"+ separate {t['dom'].segments[0].origin}-{t['intl_from']} domestic seat"
                out.append(TicketItin("B", b["origin"], o, back, price, d.isoformat(), t["date"], note=note))
    return out
