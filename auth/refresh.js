// Refresh a Playtomic member session from a stored refresh token, using a real
// headless browser (the refresh runs client-side JS on app.playtomic.com/refresh,
// which plain HTTP clients and the relay can't execute).
//
// Playtomic rotates the refresh token on every use, so this ALSO captures the new
// refresh token: the caller must persist it for the next run (see the workflow).
//
// Input  (env): PLAYTOMIC_REFRESH_TOKEN
// Output (files, kept out of public logs): $OUT_DIR/pt_cookie  -> "pt_auth_access_token=<jwt>"
//                                          $OUT_DIR/pt_refresh -> "<new refresh jwt>"
// Exits non-zero on failure so the caller can fall back to the anonymous view.
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const REFRESH = process.env.PLAYTOMIC_REFRESH_TOKEN;
const OUT_DIR = process.env.RUNNER_TEMP || require('os').tmpdir();
const UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36';

function jwtExp(j) { try { return JSON.parse(Buffer.from(j.split('.')[1], 'base64').toString()).exp; } catch { return null; } }
function mask(v) { console.log('::add-mask::' + v); } // hide from GitHub Actions logs

(async () => {
  if (!REFRESH) { console.error('PLAYTOMIC_REFRESH_TOKEN not set'); process.exit(2); }
  mask(REFRESH);

  const browser = await chromium.launch({ headless: true });
  try {
    const ctx = await browser.newContext({ userAgent: UA, locale: 'it-IT' });
    await ctx.addCookies([{ name: 'pt_auth_refresh_token', value: REFRESH, domain: '.playtomic.com', path: '/', secure: true, sameSite: 'Lax' }]);
    const page = await ctx.newPage();

    // Navigating this route lands on app.playtomic.com/refresh, whose JS mints a
    // fresh access token (and rotates the refresh token) from the refresh cookie.
    await page.goto('https://playtomic.com/api/web-app/refresh?return_url=' + encodeURIComponent('https://playtomic.com/'),
      { waitUntil: 'networkidle', timeout: 60000 });

    // Poll until the access cookie is present and unexpired (JS refresh is async).
    let acc = null, ref = null;
    for (let i = 0; i < 15; i++) {
      const cookies = await ctx.cookies();
      acc = cookies.find(c => c.name === 'pt_auth_access_token');
      ref = cookies.find(c => c.name === 'pt_auth_refresh_token');
      const exp = acc && jwtExp(acc.value);
      if (exp && exp > Math.floor(Date.now() / 1000) + 60) break;
      await page.waitForTimeout(1000);
    }
    if (acc) mask(acc.value);
    if (ref) mask(ref.value);

    const exp = acc && jwtExp(acc.value);
    if (!exp || exp <= Math.floor(Date.now() / 1000) + 60) {
      console.error('refresh failed: no fresh access token (token invalid/rotated, or flow changed)');
      process.exit(1);
    }

    fs.writeFileSync(path.join(OUT_DIR, 'pt_cookie'), 'pt_auth_access_token=' + acc.value, { mode: 0o600 });
    if (ref && ref.value !== REFRESH) {
      fs.writeFileSync(path.join(OUT_DIR, 'pt_refresh'), ref.value, { mode: 0o600 });
      console.log('ok: access refreshed (exp ' + exp + '), NEW refresh token captured -> persist it');
    } else {
      // No new refresh token surfaced: the chain can't continue past this run.
      console.error('warn: access refreshed but NO new refresh token cookie found — chain will break next run');
      console.log('ok: access refreshed (exp ' + exp + ') but refresh token NOT rotated in cookies');
    }
  } finally {
    await browser.close();
  }
})().catch(e => { console.error('refresh error:', e.message); process.exit(1); });
