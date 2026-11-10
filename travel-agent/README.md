# India trip fare agent

A standing fare-and-routing scout for one trip: **DC → Pune, Dec 11 2026 – Jan 5 2027.**
- You and your son fly out together.
- Your wife flies to Mumbai about a week later.
- All three fly home together from Pune.

The **hard rule** is no Middle East airspace. It is enforced against **actual flown ADS-B tracks**, not schedules.

## How it works
1. **`search.py`** queries Google Flights through the SerpApi engine.
   - **Ticket A** (you + son) is a round trip from IAD or EWR. It searches a date grid around Dec 11 and Jan 5.
   - **Ticket B** (wife) is an open-jaw ticket: IAD→BOM, then PNQ→home on **the same return flights as Ticket A**.
   - Options that connect through a Gulf or Levant hub are dropped immediately.
2. **`routes.py`** checks the long-haul flights from the best candidates:
   - It pulls the last 10 days of actual tracks for each flight number from FlightAware AeroAPI.
   - **`geo.py`** tests every point against Natural Earth borders and the Persian Gulf, Gulf of Oman, Red Sea and Gulf of Aden corridors.
   - One violating track is enough to reject the flight number.
   - Results are cached for 72 hours in `route_cache.json`.
3. **`rank.py`** scores each **family plan** (A + B together) as a dollar-equivalent total of:
   - fare
   - time × travelers
   - dates off your preference
   - extra connections
   - tight, overnight or self-transfer connections
   - basic-economy risk
   - unverified routing
4. **`run.py`** writes `report.md`, an email-ready digest whose first line is the subject. It also writes `latest.json` and appends `history.json`, which drives the price-drop and new-best alerts.

## Run
```bash
pip install -r requirements.txt
python run.py --mode full     # ~45 searches: date grid
python run.py --mode light    # ~12 searches: preferred dates only
python run.py --no-routes     # skip FlightAware (everything shows ⚠️ unverified)
python -m pytest -q tests
```
Credentials:
- **In the cloud session:** the network proxy injects them for `serpapi.com` and `aeroapi.flightaware.com`.
- **Locally:** set `SERPAPI_KEY` and `FLIGHTAWARE_API_KEY`.

## Change the rules
Everything lives in `trip.yaml`: dates and flex, airports, the excluded-country list, banned hubs, connection minimums, scoring weights and alert thresholds. After you book, fill in `booked:` and the agent watches your booked fares for drops.

## Limits (read these)
- **Google Flights is the fare source, so prices are indicative.** Always confirm the final price on the airline's site before you pay.
- **Track verification only covers flights flown in the last 10 days.** A new or re-timed flight shows as ⚠️ until it has flown. Routings change with NOTAMs, so a ✅ is evidence, not a guarantee for your date.
- **Borders are Natural Earth 1:50m land borders plus hand-drawn sea corridors, not FIR boundaries.** A track that only skirts excluded land is a warning, not a rejection.
