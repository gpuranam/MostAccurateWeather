#!/usr/bin/env python3
"""Run the India trip fare agent end to end.

  python run.py --mode light     # preferred dates only (~3 page searches)
  python run.py --mode full      # + one rotating alternate date pair (~5 page searches)
  python run.py --no-routes      # skip route verification (all unverified)

Default sources are free: Google Flights pages (gflights.py) and FlightAware
public track pages (fa_public.py). The SerpApi/AeroAPI paths remain available
via trip.yaml (search.provider: serpapi, airspace.track_source: aeroapi).

Writes report.md (email-ready), latest.json, and appends history.json.
The first line of report.md is the suggested email subject.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

import yaml

import fa_public
import gflights
import rank
import routes
import search as serp
from search import TicketItin

HERE = Path(__file__).parent
HISTORY = HERE / "history.json"


def fmt_leg(opt) -> str:
    segs = " → ".join(f"{s.flight} {s.origin}-{s.dest} ({s.dep[5:16]})" for s in opt.segments)
    lays = ", ".join(f"{l['airport']} {l['minutes'] // 60}h{l['minutes'] % 60:02d}"
                     + (" overnight" if l.get("overnight") else "") for l in opt.layovers)
    return f"{segs}  | {opt.total_minutes // 60}h{opt.total_minutes % 60:02d}" + (f" | layovers: {lays}" if lays else "")


def verify_options(options, cfg, cache, limit: int, enabled: bool) -> dict:
    """Verify long-haul flights used by the given options (priority order). Returns status info."""
    if not enabled:
        return {"checked": 0}
    wanted = []
    for opt in options:
        for sg in opt.segments:
            if routes.needs_check(sg, cfg) and (sg.flight, sg.origin, sg.dest) not in wanted:
                wanted.append((sg.flight, sg.origin, sg.dest))
    if cfg["airspace"].get("track_source", "flightaware_public") == "aeroapi":
        n = 0
        for f, o, d in wanted:
            if n >= limit:
                break
            hit = cache.get(f"{f}:{o}-{d}")
            if hit and hit.get("status") != "unverified" and (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(
                    hit["checked_at"])).total_seconds() < cfg["airspace"]["cache_ttl_hours"] * 3600:
                continue
            before = cache.get(f"{f}:{o}-{d}", {}).get("checked_at")
            routes.verify_flight(f, o, d, cfg, cache)
            n += cache[f"{f}:{o}-{d}"]["checked_at"] != before
        return {"checked": n}
    return fa_public.verify_batch(wanted, cfg, cache, max_flights=limit)


def load_history() -> dict:
    if HISTORY.exists():
        return json.loads(HISTORY.read_text())
    return {"runs": [], "plans": {}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["full", "light"], default="full")
    ap.add_argument("--config", default=str(HERE / "trip.yaml"))
    ap.add_argument("--fixtures", default=None)
    ap.add_argument("--no-routes", action="store_true")
    ap.add_argument("--max-route-checks", type=int, default=4)
    ap.add_argument("--out", default=str(HERE / "report.md"))
    args = ap.parse_args(argv)

    cfg = yaml.safe_load(Path(args.config).read_text())
    now = dt.datetime.now(dt.timezone.utc)
    cache = routes.load_cache()
    hist = load_history()
    provider = cfg["search"].get("provider", "gflights")
    budget = cfg["search"]["max_queries_per_run"][args.mode]
    route_notes = []

    if provider == "gflights":
        client = gflights.GFlightsClient(budget=budget)
        rotation = sum(1 for r in hist["runs"] if r.get("mode") == "full")
        a_list = gflights.search_ticket_a(cfg, client, args.mode, cache, rotation)
    else:
        client = serp.SerpApiClient(budget=budget, hl=cfg["search"]["hl"], gl=cfg["search"]["gl"],
                                    currency=cfg["currency"],
                                    fixtures_dir=Path(args.fixtures) if args.fixtures else None)
        full_budget, client.budget = client.budget, int(budget * 0.6)   # reserve ~40% for Ticket B
        a_list = serp.search_ticket_a(cfg, client, args.mode)
        client.budget = full_budget

    # 2) Pre-rank A with current knowledge, verify flights of the best candidates.
    pre = sorted(a_list, key=lambda t: rank.score_ticket(t, cfg, cache)["score"])
    r = verify_options([o for t in pre[:12] for o in (t.out, t.back)], cfg, cache,
                       args.max_route_checks, not args.no_routes)
    if r.get("blocked"):
        route_notes.append("FlightAware rate-limited route checks this run; remaining flights stay unverified")
    scored_a = sorted(((t, rank.score_ticket(t, cfg, cache)) for t in a_list),
                      key=lambda x: (rank.ORDER[x[1]["status"]], x[1]["score"]))
    a_returns = []
    for t, sc in scored_a:
        if sc["status"] == "violates":
            continue
        back = t.back
        dom, lay = None, 0
        if back.separate_tickets and len(back.segments) > 1:      # split plan: domestic first
            dom = gflights.Option(segments=[back.segments[0]], layovers=[], total_minutes=back.segments[0].minutes,
                                  price=None)
            lay = back.layovers[0]["minutes"] if back.layovers else 0
            intl = back.segments[1:]
        else:
            intl = back.segments
        tgt = {"date": t.back_date, "flights": back.flights, "intl_from": intl[0].origin,
               "intl_to": intl[-1].dest, "intl_date": intl[0].dep[:10], "intl_flights": [x.flight for x in intl],
               "dom": dom, "dom_layover": lay}
        if dom is not None:
            dom.price = None
        if tgt["flights"] not in [x["flights"] for x in a_returns]:
            a_returns.append(tgt)
        if len(a_returns) >= 4:
            break

    # 3) Ticket B (wife), open jaw matched to A's best returns.
    if not a_returns:
        b_list = []
    elif provider == "gflights":
        b_list = gflights.search_ticket_b(cfg, client, args.mode, cache, a_returns)
    else:
        b_list = serp.search_ticket_b(cfg, client, args.mode,
                                      [(x["date"], x["intl_to"], x["flights"]) for x in a_returns])
    pre_b = sorted(b_list, key=lambda t: rank.score_ticket(t, cfg, cache)["score"])
    if not r.get("blocked"):
        r2 = verify_options([t.out for t in pre_b[:6]], cfg, cache, max(2, args.max_route_checks // 2),
                            not args.no_routes)
        if r2.get("blocked"):
            route_notes.append("FlightAware rate-limited route checks this run")
    routes.save_cache(cache)

    # 4) Combine and rank.
    res = rank.build_plans(a_list, b_list, cfg, cache)
    plans = res["plans"][: cfg["scoring"]["top_n"]]

    # 5) History + alerts.
    last = hist["runs"][-1] if hist["runs"] else None
    alerts = []
    best = plans[0] if plans else None
    if best:
        sig = rank.plan_signature(best)
        prev = hist["plans"].get(sig, [])
        if last and last.get("best_sig") and cfg["alerts"]["new_best_plan"] and last.get("best_sig") != sig:
            alerts.append(f"New top plan (was ${last.get('best_fare')}): now ${best['total_fare']}")
        if prev and prev[-1]["fare"] - best["total_fare"] >= cfg["alerts"]["price_drop_usd_total"]:
            alerts.append(f"Price drop on top plan: ${prev[-1]['fare']} → ${best['total_fare']}")
        if best["status"] != "compliant":
            alerts.append("Top plan is NOT route-verified yet")
    for p in res["plans"]:
        hist["plans"].setdefault(rank.plan_signature(p), []).append(
            {"t": now.isoformat(timespec="minutes"), "fare": p["total_fare"]})
    hist["runs"].append({"t": now.isoformat(timespec="minutes"), "mode": args.mode,
                         "queries": client.used, "best_sig": rank.plan_signature(best) if best else None,
                         "best_fare": best["total_fare"] if best else None,
                         "n_plans": len(res["plans"]), "errors": client.errors[:5]})
    hist["runs"] = hist["runs"][-300:]
    for k in list(hist["plans"]):
        hist["plans"][k] = hist["plans"][k][-120:]
    if not args.fixtures:
        HISTORY.write_text(json.dumps(hist, indent=1))

    # 6) Booked-plan watch (phase 2).
    booked_lines = booked_watch(cfg, a_list, b_list)
    if any(l.startswith("DROP") for l in booked_lines):
        alerts.append("Booked fare dropped — consider rebooking for credit")

    client.errors = list(dict.fromkeys(client.errors + route_notes))
    report = render(cfg, plans, res["rejected"], cache, alerts, client, args.mode, now, booked_lines)
    Path(args.out).write_text(report)
    (HERE / "latest.json").write_text(json.dumps({
        "generated": now.isoformat(), "alerts": alerts, "queries": client.used,
        "errors": client.errors, "search_log": getattr(client, "log", []),
        "plans": [{"rank": i + 1, "status": p["status"], "total_fare": p["total_fare"], "score": p["score"],
                   "a": {"origin": p["a"].origin, "out": p["a"].out.flights, "out_date": p["a"].out_date,
                         "back": p["a"].back.flights, "back_date": p["a"].back_date, "fare": p["a"].price},
                   "b": None if not p["b"] else {"out": p["b"].out.flights, "out_date": p["b"].out_date,
                                                  "back": p["b"].back.flights, "fare": p["b"].price}}
                  for i, p in enumerate(plans)]}, indent=1))
    print(report.splitlines()[0])
    print(f"queries used: {client.used}; plans: {len(res['plans'])}; rejected: {len(res['rejected'])}")
    return 0


def booked_watch(cfg, a_list: list[TicketItin], b_list: list[TicketItin]) -> list[str]:
    bk = cfg.get("booked")
    if not bk:
        return []
    lines = []
    for label, lst, key in (("Ticket A", a_list, "ticket_a"), ("Ticket B", b_list, "ticket_b")):
        b = bk.get(key)
        if not b:
            continue
        same = [t for t in lst if t.out.flights == b.get("flights_out") and t.back.flights == b.get("flights_back")]
        if not same:
            lines.append(f"{label} ({b.get('pnr')}): booked itinerary not seen in this run's results")
            continue
        cur = min(t.price for t in same)
        diff = b["paid_usd"] - cur
        tag = "DROP" if diff >= cfg["alerts"]["price_drop_usd_total"] else "OK"
        lines.append(f"{tag} {label} ({b.get('pnr')}): paid ${b['paid_usd']:.0f}, now ${cur:.0f} "
                     f"({'-' if diff > 0 else '+'}${abs(diff):.0f})")
    return lines


def render(cfg, plans, rejected, cache, alerts, client, mode, now, booked_lines) -> str:
    subj = ("ALERT: " + alerts[0] if alerts else
            (f"India trip: best compliant plan ${plans[0]['total_fare']:,.0f}" if plans else "India trip: no plans found"))
    if not plans and getattr(client, "blocked", False):
        subj = "India trip: fare source rate-limited this run (no new prices)"
    L = [subj, "", f"_Run {now:%Y-%m-%d %H:%M} UTC · mode {mode} · {client.used} fare searches_", "",
         "_Prices are Google Flights quotes for the whole ticket (all travelers on it). "
         "Confirm on the airline site before paying._", ""]
    if alerts:
        L += ["## Alerts", *[f"- {a}" for a in alerts], ""]
    if booked_lines:
        L += ["## Booked plan watch", *[f"- {l}" for l in booked_lines], ""]
    L += ["## Ranked plans (all 3 travelers, economy)",
          "Status: ✅ route-verified clean of excluded airspace · ⚠️ not yet verified · plans that violate are excluded.", ""]
    for i, p in enumerate(plans, 1):
        icon = "✅" if p["status"] == "compliant" else "⚠️"
        a, b = p["a"], p["b"]
        L.append(f"### {i}. {icon} ${p['total_fare']:,.0f} total fare · all-in score {p['score']:,.0f} (fare + time + risk, lower is better)")
        L.append(f"**{cfg['ticket_a']['label']}** (${a.price:,.0f}, from {a.origin})")
        L.append(f"- Out {a.out_date}: {fmt_leg(a.out)}")
        L.append(f"- Back {a.back_date}: {fmt_leg(a.back)}")
        if b:
            L.append(f"**{cfg['ticket_b']['label']}** (${b.price:,.0f}, open jaw)")
            L.append(f"- Out {b.out_date}: {fmt_leg(b.out)}")
            L.append(f"- Back {b.back_date}: same flights as above"
                     + (f" (lands {b.back.segments[-1].dest} with the family)" if b.back.segments[-1].dest != b.origin else ""))
        else:
            L.append(f"**{cfg['ticket_b']['label']}**: {p.get('incomplete')}")
        notes = p["sa"]["notes"] + (p["sb"]["notes"] if p["sb"] else [])
        if notes:
            L.append("- Notes: " + "; ".join(dict.fromkeys(notes)))
        L.append("")
    if plans:
        top = plans[0]
        L += ["## Recommendation"]
        if top["status"] == "compliant":
            L.append(f"Book plan #1 now (${top['total_fare']:,.0f}). It is route-verified and is the best "
                     "trade-off of price, time and connection risk under your rules. Keep the agent running: "
                     "if the same flights drop by more than the alert threshold, change the ticket for credit.")
        else:
            L.append("No plan is fully route-verified yet. Do not book until the long-haul flights in plan #1 "
                     "are verified (next run), unless you accept the risk.")
        L.append("")
    flights = sorted(cache.values(), key=lambda v: (v["status"], v["flight"]))
    if flights:
        L += ["## Route evidence (actual ADS-B tracks, last 10 days)"]
        for v in flights:
            icon = {"compliant": "✅", "violates": "❌", "unverified": "⚠️"}[v["status"]]
            countries = ""
            good = [t for t in v.get("tracks", []) if "countries" in t]
            if good:
                countries = " · via " + " → ".join(good[0]["countries"])
            L.append(f"- {icon} {v['flight']} {v['origin']}-{v['dest']}: {v.get('reason', '')}{countries}")
        L.append("")
    if rejected:
        L += [f"## Rejected for airspace ({len(rejected)} ticket options)"]
        reasons = {}
        for r in rejected:
            for w in r["why"]:
                if "violates" in w:
                    reasons[w] = reasons.get(w, 0) + 1
        L += [f"- {w} — {n} options" for w, n in sorted(reasons.items(), key=lambda x: -x[1])[:10]]
        L.append("")
    if client.errors:
        L += ["## Search errors", *[f"- {e}" for e in client.errors[:8]], ""]
    L += ["## How to book (checklist)",
          "1. Book Ticket A first (you + son) as ONE ticket, so the Delhi–Pune flight is protected on the same ticket. Avoid basic economy.",
          "2. Book Ticket B (wife) as an open-jaw IAD→BOM / PNQ→home ticket whose return is exactly Ticket A's return flights.",
          "3. Call the airline and ask them to link the two reservations (so you're seated together and changes get handled for both).",
          "4. Carry a notarized consent letter from your wife for your son. Check passports and OCI cards or Indian e-visas.",
          "5. Put the PNRs and fares paid in trip.yaml → `booked:` so the agent watches for price drops and schedule changes.",
          ""]
    return "\n".join(L)


if __name__ == "__main__":
    raise SystemExit(main())
