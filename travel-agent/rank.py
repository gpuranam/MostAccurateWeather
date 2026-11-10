"""Score combined family plans (Ticket A + Ticket B) in dollar-equivalent cost.

Lower score is better. Hard rule: any segment whose verified track violates the
airspace rule is excluded from ranking entirely (listed under "rejected").
"""
from __future__ import annotations

import datetime as dt

from routes import needs_check
from search import Option, TicketItin

ORDER = {"compliant": 0, "domestic": 0, "unverified": 1, "violates": 2}


def _country(ap: str, cfg) -> str | None:
    for c, aps in cfg["airspace"]["domestic_airports"].items():
        if ap in aps:
            return c
    return None


def route_status(opt: Option, verdicts: dict, cfg) -> tuple[str, list[str]]:
    worst, notes = "compliant", []
    for s in opt.segments:
        if not needs_check(s, cfg):
            continue
        v = verdicts.get(f"{s.flight}:{s.origin}-{s.dest}")
        st = v["status"] if v else "unverified"
        if ORDER[st] > ORDER[worst]:
            worst = st
        if st != "compliant":
            notes.append(f"{s.flight} {s.origin}-{s.dest}: {st}" + (f" ({v['reason']})" if v else ""))
    return worst, notes


def connection_issues(opt: Option, cfg) -> tuple[float, list[str]]:
    sc = cfg["scoring"]
    pen, notes = 0.0, []
    for i, lay in enumerate(opt.layovers):
        if i + 1 >= len(opt.segments):
            break
        inbound, outbound = opt.segments[i], opt.segments[i + 1]
        intl_in = _country(inbound.origin, cfg) != _country(inbound.dest, cfg)
        dom_out = _country(outbound.origin, cfg) is not None and \
            _country(outbound.origin, cfg) == _country(outbound.dest, cfg)
        need = sc["min_connection_minutes"]["international_to_domestic" if intl_in and dom_out else "default"]
        if lay["minutes"] < need:
            pen += sc["tight_connection_penalty_usd"]
            notes.append(f"tight {lay['minutes']}m at {lay['airport']} (want >= {need}m)")
        if lay.get("overnight"):
            pen += sc["overnight_layover_penalty_usd"]
            notes.append(f"overnight at {lay['airport']}")
        if inbound.dest != outbound.origin:
            pen += sc["tight_connection_penalty_usd"]
            notes.append(f"airport change {inbound.dest}->{outbound.origin}")
    return pen, notes


def _days_off(d: str, preferred) -> int:
    return abs((dt.date.fromisoformat(d) - dt.date.fromisoformat(str(preferred))).days)


def score_ticket(t: TicketItin, cfg, verdicts) -> dict:
    sc = cfg["scoring"]
    tk = cfg["ticket_a" if t.ticket == "A" else "ticket_b"]
    pax = tk["passengers"]["adults"] + (tk["passengers"].get("children") or 0)
    parts: dict[str, float] = {"fare": t.price}
    notes: list[str] = []
    hours = (t.out.total_minutes + t.back.total_minutes) / 60
    if t.ticket == "A" and t.origin in cfg.get("positioning", {}):
        pos = cfg["positioning"][t.origin]
        parts["positioning"] = pos["cost_usd"]
        hours += 2 * pos["extra_hours"]
        notes.append(f"positioning to {t.origin} (+${pos['cost_usd']}, +{pos['extra_hours']}h each way)")
    parts["time"] = hours * pax * sc["value_of_time_usd_per_traveler_hour"]
    off = _days_off(t.out_date, tk["outbound"]["preferred"])
    if t.ticket == "A":
        off += _days_off(t.back_date, tk["return"]["preferred"])
    parts["dates"] = off * sc["date_penalty_usd_per_day_off"]
    min_conn = min_connections(cfg, t)
    extra = max(0, len(t.out.segments) - 1 - min_conn) + max(0, len(t.back.segments) - 1 - min_conn)
    parts["connections"] = extra * sc["extra_connection_penalty_usd"]
    cp = 0.0
    for leg in (t.out, t.back):
        p, n = connection_issues(leg, cfg)
        cp += p
        notes += n
        if leg.separate_tickets:
            cp += sc["separate_tickets_penalty_usd"]
            notes.append("separate tickets (unprotected connection)")
        if leg.basic_economy:
            cp += sc["basic_economy_penalty_usd"]
            notes.append("basic economy (no changes)")
    parts["connection_quality"] = cp
    st_o, n_o = route_status(t.out, verdicts, cfg)
    st_b, n_b = route_status(t.back, verdicts, cfg)
    status = max(st_o, st_b, key=lambda s: ORDER[s])
    notes += n_o + n_b
    if status == "unverified":
        parts["unverified_route"] = sc["unverified_route_penalty_usd"]
    if t.note:
        notes.append(t.note)
    return {"score": round(sum(parts.values()), 2), "parts": {k: round(v, 2) for k, v in parts.items()},
            "status": status, "notes": notes, "hours": round(hours, 1)}


def min_connections(cfg, t: TicketItin) -> int:
    # IAD/BOM/PNQ have no compliant nonstop to/from India: 1 connection minimum
    # (EWR-DEL-PNQ); from IAD that's 2 (IAD-EWR-DEL-PNQ). Wife IAD->BOM: 1.
    if t.ticket == "A":
        return 2 if t.origin == "IAD" else 1
    return 1


def build_plans(a_list: list[TicketItin], b_list: list[TicketItin], cfg, verdicts) -> dict:
    a_scored = [(a, score_ticket(a, cfg, verdicts)) for a in a_list]
    b_scored = [(b, score_ticket(b, cfg, verdicts)) for b in b_list]
    plans, rejected = [], []
    for a, sa in a_scored:
        if sa["status"] == "violates":
            rejected.append({"ticket_a": a, "why": sa["notes"]})
            continue
        matches = [(b, sb) for b, sb in b_scored
                   if b.back.flights == a.back.flights and b.back_date == a.back_date
                   and sb["status"] != "violates"]
        if matches:
            b, sb = min(matches, key=lambda x: x[1]["score"])
            status = max(sa["status"], sb["status"], key=lambda s: ORDER[s])
            plans.append({"a": a, "sa": sa, "b": b, "sb": sb, "status": status,
                          "total_fare": round(a.price + b.price, 2),
                          "score": round(sa["score"] + sb["score"], 2)})
        else:
            plans.append({"a": a, "sa": sa, "b": None, "sb": None, "status": sa["status"],
                          "total_fare": round(a.price, 2),
                          "score": round(sa["score"], 2),
                          "incomplete": "no matching wife ticket found yet"})
    for b, sb in b_scored:
        if sb["status"] == "violates":
            rejected.append({"ticket_b": b, "why": sb["notes"]})
    # Compliant before unverified, then score; dedupe identical flight sets.
    plans.sort(key=lambda p: (ORDER[p["status"]], "incomplete" in p, p["score"]))
    seen, uniq = set(), []
    for p in plans:
        k = (p["a"].out.key, p["a"].back.key, p["b"].out.key if p["b"] else None)
        if k not in seen:
            seen.add(k)
            uniq.append(p)
    return {"plans": uniq, "rejected": rejected}


def plan_signature(p: dict) -> str:
    s = f"A[{p['a'].origin}]{p['a'].out.key}|{p['a'].back.key}"
    if p.get("b"):
        s += f"||B{p['b'].out.key}"
    return s
