// Google Flights page driver (free, no API key).
// Usage: node gflights_page.cjs job.json out.json
// job = {url, timeout_s, stage1_file, picks_file}
// Handoff: writes stage-1 body to stage1_file, then waits for the caller to
// write picks_file = [{price: "$6,888", airline: "Air France"}, ...] (or []).
// One page load per search keeps Google request volume minimal.
// Loads the search, captures the GetShoppingResults responses (stage 1 =
// departing options), then for each pick clicks the matching card and captures
// stage 2 (returning / next-leg options with final ticket prices).
//
// The page's lazily-loaded JS modules fail through the sandbox proxy in
// Chromium (ERR_BLOCKED_BY_ORB), so they are fetched with curl (which honors
// HTTPS_PROXY) and fulfilled into the page.
const { chromium } = require('playwright');
const fs = require('fs');
const { execFileSync } = require('child_process');

const sleep = ms => new Promise(r => setTimeout(r, ms));
const jitter = (a, b) => a + Math.random() * (b - a);

(async () => {
  const [jobPath, outPath] = process.argv.slice(2);
  const job = JSON.parse(fs.readFileSync(jobPath, 'utf8'));
  const out = { url: job.url, stage1: null, stage2: [], blocked: false, errors: [], requests: 0 };
  const browser = await chromium.launch();
  try {
    const ctx = await browser.newContext({ locale: 'en-US', timezoneId: 'America/New_York',
      viewport: { width: 1366, height: 900 } });
    await ctx.route(/play\.google\.com|ogads-pa\.clients6|googletagmanager|doubleclick|google-analytics/, r => r.abort());
    await ctx.route(/www\.gstatic\.com\/_\/mss\/boq-travel/, async route => {
      try {
        const body = execFileSync('curl', ['-sS', '--compressed', '--max-time', '30', route.request().url()],
          { maxBuffer: 64e6 });
        await route.fulfill({ status: 200, contentType: 'text/javascript; charset=UTF-8', body });
      } catch (e) { await route.abort(); }
    });
    const page = await ctx.newPage();
    let bodies = [];
    page.on('response', async r => {
      if (!r.url().includes('GetShoppingResults')) return;
      out.requests++;
      const st = r.status();
      if (st === 429 || (st === 302 && /sorry/.test(r.headers()['location'] || ''))) out.blocked = true;
      try { const t = await r.text(); if (st === 200 && t.length > 500) bodies.push(t); } catch (e) { /* navigated */ }
    });
    page.on('framenavigated', f => { if (f === page.mainFrame() && /\/sorry\//.test(f.url())) out.blocked = true; });

    const waitResults = async () => {
      const t0 = Date.now();
      while (Date.now() - t0 < (job.timeout_s || 35) * 1000) {
        if (out.blocked) return false;
        const txt = await page.evaluate(() => document.body ? document.body.innerText : '');
        if (/something went wrong|No results returned/.test(txt) && bodies.length === 0) return false;
        if (bodies.length && /\$\d/.test(txt) && !/Fetching results/.test(txt)) { await sleep(1200); return true; }
        await sleep(500);
      }
      return bodies.length > 0;
    };

    await page.goto(job.url, { waitUntil: 'domcontentloaded', timeout: 60000 });
    if (!(await waitResults())) {
      out.errors.push('stage1: no results' + (out.blocked ? ' (rate limited)' : ''));
    } else {
      out.stage1 = bodies[bodies.length - 1];
      fs.writeFileSync(job.stage1_file, out.stage1);
      let picks = [];
      for (let i = 0; i < 120; i++) {           // wait up to 60 s for the caller
        if (fs.existsSync(job.picks_file)) { picks = JSON.parse(fs.readFileSync(job.picks_file, 'utf8')); break; }
        await sleep(500);
      }
      job.picks = picks;
      for (const pick of picks) {
        if (out.blocked) break;
        await sleep(jitter(2500, 5000));
        const cards = page.locator('li').filter({ hasText: pick.price }).filter({ hasText: pick.airline });
        if (await cards.count() === 0) { out.errors.push(`card not found ${pick.price} ${pick.airline}`); continue; }
        bodies = [];
        await cards.first().click();
        const ok = await waitResults();
        if (job.debug_dir) {
          await page.screenshot({ path: job.debug_dir + '/after_click.png' });
          fs.writeFileSync(job.debug_dir + '/after_click.txt', await page.evaluate(() => document.body.innerText));
        }
        out.stage2.push({ pick, body: ok ? bodies[bodies.length - 1] : null });
        // back to departing list for the next pick
        if ((job.picks || []).indexOf(pick) < job.picks.length - 1) {
          bodies = [];
          await page.goBack({ waitUntil: 'domcontentloaded' }).catch(() => {});
          if (!(await waitResults())) {
            await page.goto(job.url, { waitUntil: 'domcontentloaded', timeout: 60000 });
            if (!(await waitResults())) { out.errors.push('could not return to stage1'); break; }
          }
        }
      }
    }
  } catch (e) {
    out.errors.push(String(e.message || e).slice(0, 300));
  } finally {
    if (job.stage1_file && !fs.existsSync(job.stage1_file)) fs.writeFileSync(job.stage1_file, '');
    await browser.close();
    fs.writeFileSync(outPath, JSON.stringify(out));
  }
})();
