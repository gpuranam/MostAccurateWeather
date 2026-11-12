// FlightAware public flight-history track fetcher (free, no API key).
// Usage: node fa_track.cjs job.json out.json
// job = {flights: [{ident: "UA82", origin_icao: "KEWR", dest_icao: "VIDP"}], per_flight: 1,
//        delay_s: [15, 25]}
// FlightAware sits behind Cloudflare and challenges (HTTP 429) after ~10 rapid
// page loads, so this is deliberately slow and stops at the first challenge.
const { chromium } = require('playwright');
const fs = require('fs');

const sleep = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  const [jobPath, outPath] = process.argv.slice(2);
  const job = JSON.parse(fs.readFileSync(jobPath, 'utf8'));
  const [dmin, dmax] = job.delay_s || [15, 25];
  const out = { results: {}, blocked: false, pages: 0 };
  const browser = await chromium.launch();
  const page = await (await browser.newContext({ locale: 'en-US' })).newPage();
  const load = async url => {
    if (out.pages > 0) await sleep((dmin + Math.random() * (dmax - dmin)) * 1000);
    out.pages++;
    const r = await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
    if (r.status() === 429 || /Just a moment/.test(await page.title())) { out.blocked = true; return null; }
    return await page.content();
  };
  try {
    for (const f of job.flights) {
      const res = { tracks: [], error: null };
      out.results[`${f.key_flight || f.ident}:${f.origin}-${f.dest}`] = res;
      if (out.blocked) { res.error = 'skipped: rate limited'; continue; }
      // Fast path: a known schedule pattern (from a previous run) lets us open
      // yesterday's / the day before's flight page directly (1 page, not 2).
      if (f.pattern) {
        for (const back of [1, 2]) {
          if (res.tracks.length) break;
          const d = new Date(Date.now() - back * 86400e3).toISOString().slice(0, 10).replace(/-/g, '');
          const h = await load(`https://www.flightaware.com/live/flight/${f.ident}/history/${d}/${f.pattern}`);
          if (!h) { res.error = 'rate limited'; break; }
          const mm = h.match(/var trackpollBootstrap = (\{.*?\});\s*<\/script>/s);
          if (!mm) continue;
          const fl = Object.values(JSON.parse(mm[1]).flights)[0] || {};
          const pts = (fl.track || []).map(t => t.coord).filter(Boolean);
          if (pts.length >= 50) res.tracks.push({ date: d, origin: (fl.origin || {}).iata, dest: (fl.destination || {}).iata,
            operator: (fl.airline || {}).shortName || '', points: pts, pattern: f.pattern });
        }
        if (res.tracks.length || out.blocked) continue;
      }
      const html = await load(`https://www.flightaware.com/live/flight/${f.ident}/history`);
      if (!html) { res.error = 'rate limited'; continue; }
      const re = /href="(\/live\/flight\/[A-Z0-9]+\/history\/(\d{8})\/\d{4}Z\/([A-Z0-9]{3,4})\/([A-Z0-9]{3,4}))"/g;
      const links = []; let m;
      while ((m = re.exec(html))) {
        if (links.find(l => l.href === m[1])) continue;
        if (f.origin_icao && m[3] !== f.origin_icao) continue;
        if (f.dest_icao && m[4] !== f.dest_icao) continue;
        links.push({ href: m[1], date: m[2], pattern: m[1].split('/history/')[1].split('/').slice(1).join('/') });
      }
      // newest first; skip today/future (likely not completed)
      const today = new Date().toISOString().slice(0, 10).replace(/-/g, '');
      const done = links.filter(l => l.date < today);
      if (!done.length) { res.error = `no completed ${f.origin}-${f.dest} flights listed`; continue; }
      for (const l of done.slice(0, Math.min(3, (job.per_flight || 1) + 1))) {
        if (res.tracks.length >= (job.per_flight || 1)) break;
        const h = await load('https://www.flightaware.com' + l.href);
        if (!h) { res.error = 'rate limited'; break; }
        const mm = h.match(/var trackpollBootstrap = (\{.*?\});\s*<\/script>/s);
        if (!mm) continue;
        const fl = Object.values(JSON.parse(mm[1]).flights)[0] || {};
        const pts = (fl.track || []).map(t => t.coord).filter(Boolean);
        if (pts.length < 50) continue;   // in-progress or sparse
        res.tracks.push({ date: l.date, origin: (fl.origin || {}).iata, dest: (fl.destination || {}).iata,
          operator: (fl.airline || {}).shortName || '', points: pts, pattern: l.pattern });
      }
      if (!res.tracks.length && !res.error) res.error = 'no usable tracks';
    }
  } catch (e) {
    out.error = String(e.message || e).slice(0, 300);
  } finally {
    await browser.close();
    fs.writeFileSync(outPath, JSON.stringify(out));
  }
})();
