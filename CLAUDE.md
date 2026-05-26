# Daily Used-Armada Shortlist — Agent Playbook

This file is the operating manual for the **daily Claude Code on the web** session that watches the used-car market for a 2018 Nissan Armada near ZIP 20105 (Aldie, VA). When the daily trigger fires (or anyone runs `Run today's Armada search per CLAUDE.md`), follow this playbook end-to-end.

> **Note on this repo:** The other files here are an unrelated React weather app on `main`. This branch (`claude/armada-search-listings-94GIH`) repurposes the repo as a Claude-driven car-listing agent — the React code is irrelevant to this task and should be left alone.

---

## Goal

Each morning, produce a **Gmail draft** in the user's connected Gmail account titled `Armada listings — YYYY-MM-DD` with three sections:

1. 🆕 **New today** — ranked shortlist of new candidates that pass all hard filters
2. 📉 **Price drops** — previously-seen VINs that dropped ≥ $500 since the last run
3. ❌ **Disqualified today** — listings rejected, each with 1–2 bulleted reasons and a link

Then commit the updated `state/seen-vins.json` to this branch so tomorrow's run can dedupe.

The user reviews the draft in Gmail and sends-to-self (or discards). The user — not the agent — decides which to test-drive.

---

## Hard filters (drop the listing entirely if any fail)

| Field | Rule |
|---|---|
| Year | 2017–2019 (2018 is the priority) |
| Drivetrain | **4WD only** — skip RWD/2WD |
| Mileage | < 100,000 |
| Price | < $26,000 (slightly above $25K target to catch negotiables) |
| Title | Clean only — drop **salvage, rebuilt, flood, lemon** |
| Interior color | **Not beige / tan / cream / sand** |
| Carfax | Listing must claim "Clean Carfax" / "no accidents reported" — if neither claim nor link present, treat as disqualified for shortlist but include in disqualified list with "Carfax not visible" |
| Location | ≤ 50 mi from ZIP 20105 |

---

## Ranking (within the survivors)

Order the shortlist by these tiebreakers, top-to-bottom:

1. Model year **2018** (always above 2017/2019)
2. Trim: **Platinum > SL > SV**
3. Carfax: **free link fetched and confirmed clean** > "claims clean Carfax" > "Carfax not visible"
4. Price (lower is better; under $23K is a strong signal)
5. Mileage (lower is better)
6. Distance from 20105 (closer is better)

---

## Sources to check (in this order)

### National aggregators (most inventory lives here)
- Autotrader — `https://www.autotrader.com/cars-for-sale/all-cars/2018/nissan/armada/aldie-va`
- Cars.com — `https://www.cars.com`
- CarGurus — `https://www.cargurus.com`
- TrueCar — `https://www.truecar.com/used-cars-for-sale/listings/nissan/armada/location-aldie-va/`
- Carfax used-car listings — `https://www.carfax.com/Used-Cars-in-Aldie-VA_c22416` (listings here usually carry a free Carfax)
- U.S. News used cars — `https://cars.usnews.com/cars-trucks/used-cars/for-sale/nissan_aldie_va`
- Nissan CPO — `https://www.nissanusa.com/shopping-tools/search-inventory/certified-pre-owned?models=armada`
- AutoNation — `https://www.autonation.com/used-cars/nissan/armada`

### Chains
- CarMax — `https://www.carmax.com` (search Armada, transfer-to-NoVA inventory counts)
- Carvana — `https://www.carvana.com` (delivers to 20105)

### Local Nissan franchise dealers (high-yield for Armada trade-ins + CPO)
- **Priority Nissan Chantilly** — https://www.prioritynissanchantilly.com/ — ~10 mi
- **Sheehy Nissan of Manassas** — https://www.sheehynissanofmanassas.com — ~15 mi
- **Brown's Dulles Nissan** — https://www.brownsdullesnissan.com/ — ~15 mi
- **Safford Brown Nissan Sterling** — https://www.saffordbrown.com/dealers/safford-brown-nissan-sterling/ — ~20 mi

### Local multi-brand used dealers
- Sterling Motorsport (Sterling)
- Loudoun Motor Cars (Leesburg + Chantilly)
- Capital Auto Sales (Chantilly)
- Ted Britt Lincoln of Chantilly
- AutoNation Honda Dulles
- Lindsay Volkswagen of Dulles
- Subaru of Sterling

### Marketplaces (lower signal, but cheap)
- Facebook Marketplace — `https://www.facebook.com/marketplace/category/cars`
- Craigslist DC — `https://washingtondc.craigslist.org/search/cta?query=nissan+armada`

### Do NOT search
- Vroom (wound down e-commerce Jan 2024)
- Shift (bankruptcy 2023)

---

## Step-by-step procedure

### 1. Load prior state
- Read `state/seen-vins.json`. Hold its contents as `seen_vins` in memory.
- Compute today's date as `YYYY-MM-DD` (US Eastern time).

### 2. Discover candidates
- Run targeted `WebSearch` queries for each source above. Examples:
  - `2018 Nissan Armada 4WD 20105 site:cars.com under 25000`
  - `2018 Nissan Armada Platinum 4WD site:cargurus.com 50 miles 20105`
  - `Nissan Armada used site:autotrader.com Aldie VA 4WD`
  - Repeat with `2017` and `2019` model years as secondary passes.
- For local Nissan franchise dealers, also `WebFetch` their used-inventory pages directly (e.g. Priority Nissan Chantilly's pre-owned page) since trade-ins land there before they propagate to aggregators.
- Collect unique listing URLs.

### 3. Extract per-listing data

**IMPORTANT — fetch strategy:** Every major used-car listing site (Cars.com, CarGurus, Autotrader, Carvana, Carfax, AutoNation, Edmunds, plus most franchised dealer sites running on DealerInspire / Dealer.com) returns HTTP 403 to `WebFetch` because of Cloudflare/Akamai bot protection. **Use the Playwright MCP server's browser tools instead** for all listing-site URLs:
- `mcp__playwright__browser_navigate` to open the URL
- `mcp__playwright__browser_snapshot` to read the rendered DOM as accessibility tree
- `mcp__playwright__browser_evaluate` to pull structured data (JSON-LD blocks, dataLayer, hidden VIN fields)
- `mcp__playwright__browser_close` between unrelated listings to keep memory bounded

Fall back to `WebFetch` only for plain HTML pages that aren't bot-walled (some independent dealer sites, blog/news sources). Search engine result snippets are last-resort signal — never trust them as authoritative on price/mileage/drivetrain.

For each candidate URL, navigate + snapshot it and extract:

- Year, trim (SV/SL/Platinum/Reserve), drivetrain (must be 4WD)
- Mileage, asking price
- Exterior color, **interior color**
- Dealer name + city + estimated distance from 20105
- VIN (if shown — required for dedup; if missing, generate a stable key from URL)
- Carfax statements ("Clean Carfax", "No accidents", "1-owner")
- Title status
- Free Carfax/AutoCheck URL if dealer provides one
- Listing URL

### 4. Carfax handling
For each survivor, if a free Carfax/AutoCheck URL is present:
- `WebFetch` it.
- If the fetch returns useful body text → summarize: owner count, accidents reported, service records count.
- If the fetch returns empty / JS-gated / login wall → mark `Carfax link present — fetch blocked, request PDF from dealer`.
- If no link at all → mark `Carfax not visible — request from dealer`.

### 5. Apply hard filters
Drop any listing that fails any hard filter. **Keep the dropped ones** with their reason — they go in the "Disqualified today" section.

### 6. Dedup against prior state
- For each surviving candidate's VIN:
  - **Not in `seen_vins`** → add to "🆕 New today"
  - **In `seen_vins`**, price dropped ≥ $500 → "📉 Price drops" (show old price → new price)
  - **In `seen_vins`**, no material change → omit from email
- Update `seen_vins[VIN]` with `last_seen = today`, `last_price = current price`.
- Add `first_seen = today` for genuinely new VINs.
- Schema per entry:
  ```json
  {
    "first_seen": "2026-05-25",
    "last_seen": "2026-05-25",
    "last_price": 23500,
    "year": 2018,
    "trim": "Platinum",
    "miles": 78442,
    "source": "cars.com",
    "url": "https://..."
  }
  ```
- Do **not** delete entries whose listings disappeared — useful audit data and protects against re-listing churn.

### 7. Rank the "🆕 New today" list
Apply the ranking rules above. Number entries 1, 2, 3…

### 8. Compose the Gmail draft
Use the Gmail MCP `create_draft` tool. Recipient: the user's own Gmail address. Subject: `Armada listings — YYYY-MM-DD`. Body (Markdown):

```
## 🆕 New today (ranked)

### 1. 2018 Nissan Armada Platinum 4WD — $23,500 — 78,442 mi
- 📍 Sterling, VA (12 mi from 20105)
- 🎨 Black ext / Black int
- ✅ Clean Carfax, 1-owner, 22 service records (free link fetched)
- 🔗 [View listing](https://...)
- 📞 Dealer: Sterling Motorsport — (XXX) XXX-XXXX

### 2. 2018 Nissan Armada SL 4WD — $24,800 — 65,210 mi
...

## 📉 Price drops on previously-seen listings
- [2018 Armada Platinum @ Priority Nissan Chantilly](https://...) — was $24,500, now **$23,200** (-$1,300)

## ❌ Disqualified today (with reasons)
- [2018 Armada SL @ XYZ Motors](https://...)
  - Interior is beige
- [2017 Armada Platinum @ ABC Auto](https://...)
  - Rebuilt title
  - 118K miles
- [2019 Armada SV @ Beltway](https://...)
  - 142K miles
  - Carfax not visible

---
Sources searched today: Cars.com, CarGurus, Autotrader, TrueCar, Carfax, Nissan CPO, Priority Nissan Chantilly, Sheehy Nissan of Manassas, Brown's Dulles Nissan, Safford Brown Nissan Sterling, CarMax, Carvana, Facebook Marketplace, Craigslist DC.
Total candidates seen: N • Passed filters: M • Disqualified: K • Carfax fetched: J
```

**Every listing — shortlist OR disqualified — must include its source link.**

### 9. Persist state
- Write the updated `seen_vins` back to `state/seen-vins.json` (pretty-printed, 2-space indent, keys sorted).
- Git: `git add state/seen-vins.json && git commit -m "daily: <date> — N new, M price drops, K disqualified" && git push -u origin claude/armada-search-listings-94GIH`.

### 10. End the session
Do not send the email — the user reviews and sends manually. Your job is done once the draft exists and the state commit is pushed.

---

## Failure modes & what to do

- **WebSearch returns nothing useful for a source** → log it in the email footer (`Note: CarGurus returned no usable results today`) and continue. Don't retry forever.
- **WebFetch blocked by Cloudflare / JS-only page** → record the URL as a candidate with "details unavailable — open manually" and skip extraction. Include in email under a small "🔍 Could not auto-evaluate" section.
- **Gmail MCP not connected / errors** → write the draft as a Markdown file at `drafts/YYYY-MM-DD.md` and commit it. Note in the next-run header that the user should connect Gmail.
- **Zero results across all sources** → still send a draft saying "No listings cleared filters today" plus a footer summarizing what was searched. Silence is worse than a null result.

---

## Out of scope

- Buying paid Carfax reports
- Sending the email (user reviews + sends)
- Screenshots of Carfax pages (would need a Playwright MCP server — defer)
- Multi-make/model search (this is Armada-only by design)
- Any modification of the React weather code on `main`
