# India trip fare agent

A standing fare-and-routing scout for one trip: **DC → Pune, Dec 11 2026 – Jan 5 2027.**
- You and your son fly out together.
- Your wife flies to Mumbai about a week later.
- All three fly home together from Pune.

The **hard rule** is no Middle East airspace. It is enforced against **actual flown tracks**, not schedules. **Cost: $0.** There are no paid APIs and no keys.

## How it works
1. **Fares (`gflights.py` + `js/gflights_page.cjs`).**
   - Headless Chromium opens Google Flights (the URL is built with the open-source `fast-flights` package).
   - It reads Google's own result JSON, then clicks the best outbound options to get their returns with final ticket prices.
   - Per run, it searches:
     - **Through tickets** origin⇄PNQ, from IAD and from EWR.
     - **Split tickets**: origin⇄DEL round trip plus separate Delhi⇄Pune domestic tickets. This is the only way United's EWR–DEL nonstop shows up, because Google doesn't combine it with Delhi–Pune on one ticket. A 4.5 h self-transfer buffer is enforced.
     - **Your wife's open jaw**: IAD→BOM, and home on **the same international flights** as the family, plus the same domestic seat on split plans.
   - The page's lazily loaded JavaScript modules are fetched with `curl`, because Chromium's own requests for them fail through the sandbox proxy.
2. **Routes (`fa_public.py` + `js/fa_track.cjs`).**
   - Reads recent flown tracks from FlightAware's **public** flight-history pages, looked up by the **operating** flight (codeshares are resolved).
   - **`geo.py`** tests every point against Natural Earth borders plus the Persian Gulf, Gulf of Oman, Red Sea and Gulf of Aden corridors.
   - Only international legs that touch India get checked. Transatlantic and domestic legs are skipped.
3. **Ranking (`rank.py`).** Scores each **family plan** (both tickets) as a dollar-equivalent total of:
   - fare
   - time × travelers
   - dates off your preference
   - connections, plus self-transfer and overnight penalties
   - unverified-route penalty

   Flights that violate the rule are excluded outright.
4. **`run.py`** writes `report.md` (the email digest; line 1 is the subject), `latest.json` (including a per-search diagnostic log) and `history.json` (the price history that drives alerts).

## Run
```bash
pip install -r requirements.txt
python run.py --mode light   # 5 page searches: preferred dates
python run.py --mode full    # 8 page searches: + one rotating alternate date pair per run
python -m pytest -q tests    # offline; uses real captured payloads and a real UA82 track
```

## Rate limits (the price of free)
**Observed from this cloud IP:**
- Google Flights starts returning 429 / "unusual traffic" after about 20 searches in 30 minutes.
- FlightAware shows a Cloudflare challenge after about 10 rapid page loads.

**What the agent does about it:**
- Keeps each run small: 5–8 searches, 2 runs a day.
- Caches route verdicts for 3 days (6 days when compliant).
- Remembers each flight's FlightAware URL pattern, so a re-check costs 1 page.
- Stops at the first block and reports "rate-limited this run" instead of retrying.

**If blocks become routine:** run the same script on your own Mac (home IP).

## Change the rules
Everything lives in `trip.yaml`: dates and flex, airports, the excluded-country list, banned hubs, connection minimums, scoring weights and alert thresholds. After you book, fill in `booked:` and the agent watches your booked fares for drops.

## Limits
- **Prices are Google Flights quotes.** Confirm the final price on the airline's site.
- **On split plans, the domestic ticket is separate.** If the international flight is late, the airline won't rebook the domestic one. The 4.5 h buffer and the cheap IndiGo fares (about $75 per person) keep that risk small.
- **A ✅ means recent tracks were clean.** It doesn't guarantee your date's routing. Re-checks continue until you fly.
- **Borders are Natural Earth 1:50m land plus hand-drawn sea corridors, not FIR boundaries.** A track that only skirts excluded land is a warning, not a rejection.
