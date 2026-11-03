// End-to-end browser tests. Builds the site twice (live mode against a fake Supabase, and
// prototype mode), serves it with the production Content-Security-Policy, and drives it in Chrome.
//   node tests/ui/run.mjs          (CHROME_PATH overrides the browser location)
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';
import puppeteer from 'puppeteer-core';
import { startServer } from './server.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const CHROME = process.env.CHROME_PATH || ['/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser'].find((p) => fs.existsSync(p));
if (!CHROME) { console.error('No Chrome found. Set CHROME_PATH.'); process.exit(2); }

const b64url = (o) => Buffer.from(JSON.stringify(o)).toString('base64url');
const ANON_KEY = `${b64url({ alg: 'HS256', typ: 'JWT' })}.${b64url({ role: 'anon', iss: 'test' })}.signature`;

let failures = 0;
async function step(name, fn) {
  try { await fn(); console.log('ok   -', name); }
  catch (e) { failures++; console.log('FAIL -', name, '\n       ', String(e.stack || e.message).split('\n').slice(0, 3).join('\n        ')); }
}

function build(outDir, env) {
  fs.mkdirSync(outDir, { recursive: true });
  execFileSync('python3', ['build.py'], { cwd: root, env: { ...process.env, BUILD_OUT_DIR: outDir, ...env }, stdio: 'pipe' });
}

async function newPage(browser, origin, issues, visits) {
  const expected = [];
  const ctx = await browser.createBrowserContext();
  const page = await ctx.newPage();
  await page.setViewport({ width: 1280, height: 900 });
  await page.setRequestInterception(true);
  page.on('request', (req) => {
    const u = new URL(req.url());
    if (u.origin === origin || u.protocol === 'data:' || u.protocol === 'blob:') return req.continue();
    if (u.hostname === 'fonts.googleapis.com') return req.respond({ status: 200, contentType: 'text/css', body: '' });
    if (u.hostname === 'fonts.gstatic.com') return req.respond({ status: 200, body: '' });
    if (u.hostname === 'checkout.stripe.com' || u.hostname === 'billing.stripe.com') {
      visits.push(req.url());
      return req.respond({ status: 200, contentType: 'text/html', body: '<h1>stripe stub</h1>' });
    }
    issues.push('unexpected external request: ' + req.url());
    return req.abort();
  });
  page.on('pageerror', (e) => issues.push('pageerror: ' + e.message));
  page.on('console', (m) => {
    if (m.type() !== 'error') return;
    const i = expected.findIndex((re) => re.test(m.text()));
    if (i >= 0) { expected.splice(i, 1); return; } // an error this test provokes on purpose
    issues.push('console error: ' + m.text());
  });
  page.expect = (re) => expected.push(re);
  page.unmetExpectations = () => expected.map(String);
  return page;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const wait = (page, fn, arg, timeout = 8000) => page.waitForFunction(fn, { timeout }, arg);
const text = (page, sel) => page.$eval(sel, (e) => e.textContent);
const clear = (page, sel) => page.$eval(sel, (e) => { e.value = ''; });
const fill = async (page, sel, v) => { await clear(page, sel); await page.type(sel, v); };
const toast = (page, re) => wait(page, (s) => new RegExp(s).test(document.querySelector('.toast')?.textContent || ''), re.source);
const db = (page) => page.evaluate(() => window.__fake.db());
const count = (page, sel) => page.$$eval(sel, (els) => els.length);
async function clickText(page, sel, label) {
  const ok = await page.evaluate((s, l) => { const el = [...document.querySelectorAll(s)].find((e) => e.textContent.includes(l)); if (el) el.click(); return !!el; }, sel, label);
  assert.ok(ok, `no ${sel} containing "${label}"`);
}

const FIRM = 'Test & Co Solicitors', EMAIL = 'owner@testco.example', PW = 'correct horse battery';
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'cl-ui-'));
const LOGO = path.join(tmp, 'logo.png');
fs.writeFileSync(LOGO, Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==', 'base64'));

const browser = await puppeteer.launch({ executablePath: CHROME, headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] });

try {
  /* ================================================================ live mode ================================================================ */
  console.log('\n# Live mode (fake Supabase, production CSP)');
  const live = await startServer({ outDir: path.join(tmp, 'live'), root, useFake: true });
  build(path.join(tmp, 'live'), { SUPABASE_URL: live.origin, SUPABASE_ANON_KEY: ANON_KEY });
  const issues = [], visits = [];
  const page = await newPage(browser, live.origin, issues, visits);
  let firmId, userId;

  await step('public listings load from the database, with no sample firms', async () => {
    await page.goto(live.origin + '/#results');
    await page.waitForSelector('.card');
    assert.equal(await count(page, '.card'), 3);
    assert.equal(await count(page, '.chip.prem'), 1, 'one Premium listing chip');
    assert.equal(await page.$$eval('.chip', (e) => e.filter((x) => x.textContent === 'Sample firm').length), 0, 'no sample firms in live mode');
    assert.match(await text(page, '.filters'), /How firms are ranked/);
    assert.equal(await count(page, '.card .logo img'), 1, 'only the Premium firm shows a logo');
  });

  await step('hostile listing content renders as inert text', async () => {
    assert.equal(await page.evaluate(() => window.__xss), undefined, 'no script ran');
    assert.equal(await count(page, 'img[src="x"]'), 0);
    assert.match(await page.$eval('.cards', (e) => e.textContent), /Evil <img src=x/);
  });

  await step('registration asks the firm to confirm its email', async () => {
    await page.click('#nav-firm');
    await page.click('[data-mode="register"]');
    await fill(page, '#a-firm', FIRM);
    await fill(page, '#a-email', EMAIL);
    await fill(page, '#a-pw', PW);
    await page.click('#a-ok');
    await page.click('#authf button[type=submit]');
    await wait(page, () => /Check your inbox/.test(document.querySelector('.ok')?.textContent || ''));
    const d = await db(page);
    assert.equal(d.users.length, 1);
    userId = d.users[0].id; firmId = d.firms.find((f) => f.owner_id === userId).id;
    assert.equal(d.firms.find((f) => f.id === firmId).name, FIRM);
    assert.equal(d.firms.find((f) => f.id === firmId).listed, false);
  });

  await step('a duplicate email and bad sign-ins give clear errors', async () => {
    await page.goto(live.origin + '/#firms'); await page.reload();
    await page.click('[data-mode="register"]');
    await fill(page, '#a-firm', 'Another'); await fill(page, '#a-email', EMAIL); await fill(page, '#a-pw', PW);
    await page.click('#a-ok'); await page.click('#authf button[type=submit]');
    await wait(page, () => /already exists/.test(document.querySelector('#a-err')?.textContent || ''));
    await page.click('[data-mode="signin"]');
    await fill(page, '#a-email', EMAIL); await fill(page, '#a-pw', PW);
    await page.click('#authf button[type=submit]');
    await wait(page, () => /confirm your email/.test(document.querySelector('#a-err')?.textContent || ''));
    await fill(page, '#a-pw', 'wrong password!');
    await page.click('#authf button[type=submit]');
    await wait(page, () => /do not match/.test(document.querySelector('#a-err')?.textContent || ''));
  });

  await step('after confirming, the firm signs in to a draft portal with the logo locked', async () => {
    await page.evaluate((e) => window.__fake.confirm(e), EMAIL);
    await fill(page, '#a-pw', PW);
    await page.click('#authf button[type=submit]');
    await page.waitForSelector('.dash-head');
    assert.equal(await text(page, '.dash-head h1'), FIRM);
    assert.match(await text(page, '.dash-head .status'), /Draft/);
    assert.equal(await count(page, '.status.premium'), 0);
    assert.equal(await count(page, '#logo-in'), 0, 'no uploader for a free firm');
    assert.match(await text(page, '.lock'), /See Premium/);
    assert.equal(await text(page, '#nav-firm'), 'Firm portal');
  });

  await step('details and prices save to the database, then the firm publishes', async () => {
    await fill(page, '#p-town', 'Bristol'); await fill(page, '#p-address', '9 Park Street'); await fill(page, '#p-phone', '0117 496 0123'); await fill(page, '#p-regno', '654321');
    await page.click('#detf button[type=submit]');
    await toast(page, /Details saved/);
    await page.click('[data-tab="prices"]');
    await page.click('.svc[data-svc="will1"] [data-on]');
    await page.type('.svc[data-svc="will1"] [data-fee]', '195');
    await page.click('#pricef button[type=submit]');
    await toast(page, /Prices saved/);
    const row = (await db(page)).firms.find((f) => f.id === firmId);
    assert.equal(row.town, 'Bristol'); assert.equal(row.reg_no, '654321'); assert.equal(row.services.will1.fee, 195);
    await page.click('#publish');
    await toast(page, /listing is live/);
    assert.match(await text(page, '.dash-head .status'), /Live on Compare Legal/);
    assert.equal((await db(page)).firms.find((f) => f.id === firmId).listed, true);
  });

  await step('the new firm appears in results, ranked by price, with no Premium label', async () => {
    await page.click('[data-go="results"]');
    await page.waitForSelector('#r-svc');
    await page.select('#r-svc', 'will1');
    await wait(page, () => document.querySelectorAll('.card').length === 4);
    const names = await page.$$eval('.card h3', (e) => e.map((x) => x.textContent));
    assert.ok(names.includes(FIRM));
    assert.equal(names[0], FIRM, 'cheapest first (195 + VAT)');
    assert.equal(await page.$$eval('.card.mine .chip.prem', (e) => e.length), 0);
  });

  await step('a quote request goes to the enquiry function with the right details', async () => {
    await page.evaluate((n) => { [...document.querySelectorAll('.card')].find((c) => c.textContent.includes(n)).querySelector('[data-quote]').click(); }, FIRM);
    await page.waitForSelector('#qform');
    await page.type('#q-name', 'Sam Client'); await page.type('#q-email', 'sam@client.example'); await page.type('#q-msg', 'Single will please');
    await page.click('#qform button[type=submit]');
    await wait(page, () => /Request sent to/.test(document.querySelector('#qform')?.textContent || ''));
    const call = live.state.functionCalls.filter((c) => c.name === 'submit-enquiry').pop();
    assert.equal(call.body.firmId, firmId); assert.equal(call.body.service, 'will1'); assert.equal(call.body.total, 234);
    assert.equal(call.body.hp, ''); assert.equal(call.apikey, ANON_KEY);
  });

  await step('a rejected quote request shows the server\'s reason and can be retried', async () => {
    await page.keyboard.press('Escape');
    await page.evaluate((n) => { [...document.querySelectorAll('.card')].find((c) => c.textContent.includes(n)).querySelector('[data-quote]').click(); }, FIRM);
    await page.waitForSelector('#qform');
    page.expect(/status of 400/); // the stand-in function rejects this address on purpose
    await page.type('#q-name', 'Sam'); await page.type('#q-email', 'bad@x');
    await page.click('#qform button[type=submit]');
    await wait(page, () => /valid email/.test(document.querySelector('#q-err')?.textContent || ''));
    assert.equal(await page.$eval('#qform button[type=submit]', (b) => b.disabled), false);
    await page.keyboard.press('Escape');
  });

  await step('the Plan tab offers Premium and sends the firm to Stripe Checkout', async () => {
    await page.click('#nav-firm'); await page.waitForSelector('.dash-head');
    await page.click('[data-tab="plan"]');
    assert.match(await text(page, '#pane'), /Upgrade to Premium/);
    assert.match(await text(page, '#pane'), /£49 per month/);
    // "already subscribed" is handled without leaving the page
    live.state.alreadySubscribed = true;
    page.expect(/status of 409/);
    await page.click('#upgrade');
    await toast(page, /already on Premium/);
    live.state.alreadySubscribed = false;
    await page.click('[data-tab="plan"]');
    await page.click('#upgrade');
    await wait(page, () => location.hostname === 'checkout.stripe.com');
    const call = live.state.functionCalls.filter((c) => c.name === 'create-checkout-session').pop();
    assert.equal(call.auth, 'Bearer fake-token-' + userId, 'the firm\'s own access token is sent');
    assert.ok(visits.some((v) => v.startsWith('https://checkout.stripe.com/')));
  });

  await step('returning from Stripe waits for the webhook, then unlocks Premium', async () => {
    setTimeout(() => page.evaluate((id) => window.__fake.setBilling(id, 'active'), firmId).catch(() => {}), 3000); // the webhook lands late
    await page.goto(live.origin + '/?billing=success#portal');
    await toast(page, /Activating your Premium plan/);
    await toast(page, /Premium is active/);
    assert.equal(await count(page, '.dash-head .status.premium'), 1);
    assert.match(await text(page, '.plan.on'), /Premium/);
    assert.match(await text(page, '#pane'), /Manage billing/);
    assert.ok(!page.url().includes('billing='), 'the return parameter is removed from the address bar');
  });

  await step('Premium firms can upload a logo that is saved and shown', async () => {
    await page.click('[data-tab="details"]');
    const input = await page.$('#logo-in');
    assert.ok(input, 'uploader is available');
    await input.uploadFile(LOGO);
    await toast(page, /Logo added/);
    assert.ok((await db(page)).files.some((f) => f.startsWith(firmId + '/logo-')));
    await page.click('#detf button[type=submit]');
    await toast(page, /Details saved/);
    const row = (await db(page)).firms.find((f) => f.id === firmId);
    assert.match(row.logo_path, new RegExp('^' + firmId + '/logo-[a-z0-9]+\\.png$'));
    assert.equal(await count(page, '.dash-id .logo img'), 1);
  });

  await step('Premium enquiry tracking works and the CSV export is safe to open in a spreadsheet', async () => {
    await page.evaluate((id) => window.__fake.addEnquiry({ firm_id: id, service: 'will1', name: '=HYPERLINK("http://evil.example")', email: 'sam@client.example', message: 'Hi, "quoted"\nline two' }), firmId);
    await page.click('[data-tab="enquiries"]');
    await page.waitForSelector('[data-enq] select[data-estatus]');
    await page.select('[data-enq] select[data-estatus]', 'contacted');
    await toast(page, /Status updated/);
    await page.type('[data-enq] textarea[data-enote]', 'Called back');
    await page.$eval('[data-enq] textarea[data-enote]', (e) => e.blur());
    await toast(page, /Note saved/);
    const enq = (await db(page)).enquiries[0];
    assert.equal(enq.status, 'contacted'); assert.equal(enq.note, 'Called back');

    await page.evaluate(() => { window.__blobs = []; const o = URL.createObjectURL; URL.createObjectURL = (b) => { window.__blobs.push(b); return o.call(URL, b); }; });
    await page.click('#enq-csv');
    const bytes = await page.evaluate(async () => [...new Uint8Array(await window.__blobs[0].arrayBuffer())]);
    assert.deepEqual(bytes.slice(0, 3), [0xEF, 0xBB, 0xBF], 'UTF-8 byte order mark so Excel reads accents correctly');
    const csv = Buffer.from(bytes).toString('utf8').slice(1);
    assert.ok(csv.startsWith('Received,Name,Email,Phone,Service,Nation,Property value,Total quoted,Status,Note,Message'));
    assert.ok(csv.includes(`'=HYPERLINK("http://evil.example")`.replace(/"/g, '""')), 'formula prefixed so it stays text');
    assert.ok(csv.includes('"Hi, ""quoted""\nline two"'), 'quotes and newlines escaped');
  });

  await step('Premium listings show a logo and label in results', async () => {
    await page.click('[data-go="results"]'); await page.waitForSelector('.card');
    assert.equal(await page.$$eval('.card.mine .chip.prem', (e) => e.length), 1);
    assert.equal(await page.$$eval('.card.mine .logo img', (e) => e.length), 1);
  });

  await step('when Premium lapses, extras switch off but nothing is lost', async () => {
    await page.evaluate((id) => window.__fake.setBilling(id, 'canceled'), firmId);
    await page.goto(live.origin + '/#portal'); await page.reload();
    await page.waitForSelector('.dash-head');
    assert.equal(await count(page, '.status.premium'), 0);
    assert.equal(await count(page, '.dash-id .logo img'), 0, 'logo hidden');
    await page.click('[data-tab="plan"]');
    assert.match(await text(page, '#pane'), /Your Premium plan has ended/);
    assert.match(await text(page, '#pane'), /Upgrade to Premium/);
    await page.click('[data-tab="enquiries"]');
    await page.waitForSelector('[data-enq]');
    assert.equal(await count(page, 'select[data-estatus]'), 0);
    assert.match(await text(page, '#pane'), /Track every enquiry/);
    assert.equal(await count(page, '#enq-csv'), 0);
    await page.click('[data-tab="details"]');
    assert.match(await text(page, '.lock'), /See Premium/);
    await page.click('#detf button[type=submit]');
    await toast(page, /Details saved/); // the kept logo path is unchanged, so saving still works
    assert.ok((await db(page)).firms.find((f) => f.id === firmId).logo_path, 'stored logo kept');
  });

  await step('a firm can reset a forgotten password', async () => {
    await page.click('#signout'); await toast(page, /Signed out/);
    await page.click('#forgot');
    await wait(page, () => /Enter your work email/.test(document.querySelector('#a-err')?.textContent || ''));
    await fill(page, '#a-email', EMAIL);
    await page.click('#forgot'); await toast(page, /reset link is on its way/);
    assert.deepEqual((await db(page)).resets, [EMAIL]);
  });

  await step('arriving from a reset email shows the new-password form, even though the event fires during start-up', async () => {
    await fill(page, '#a-pw', PW); await page.click('#authf button[type=submit]');
    await page.waitForSelector('.dash-head');
    await page.evaluate(() => window.__fake.setFlag('recovery', true));
    await page.reload();
    await page.waitForSelector('#resetf');
    await fill(page, '#r-pw', 'a brand new password'); await fill(page, '#r-pw2', 'something else entirely');
    await page.click('#resetf button[type=submit]');
    await wait(page, () => /do not match/.test(document.querySelector('#r-err')?.textContent || ''));
    await fill(page, '#r-pw2', 'a brand new password');
    await page.click('#resetf button[type=submit]');
    await toast(page, /Password updated/);
    await page.waitForSelector('.dash-head');
    assert.equal((await db(page)).users[0].password, 'a brand new password');
  });

  await step('signing out returns to the sign-in page and protects the portal', async () => {
    await page.click('#signout'); await page.waitForSelector('#authf');
    assert.equal(await text(page, '#nav-firm'), 'For law firms');
    await page.goto(live.origin + '/#portal'); await page.reload();
    await page.waitForSelector('#authf');
  });

  await step('no script errors, CSP violations or third-party requests in live mode', async () => {
    assert.deepEqual(issues, [], 'issues: ' + JSON.stringify(issues, null, 1));
    assert.deepEqual(page.unmetExpectations(), [], 'the deliberate error responses really happened');
  });
  await live.close();

  /* ================================================================ prototype mode ================================================================ */
  console.log('\n# Prototype mode (no backend configured)');
  const proto = await startServer({ outDir: path.join(tmp, 'proto'), root, useFake: false });
  build(path.join(tmp, 'proto'), { SUPABASE_URL: '', SUPABASE_ANON_KEY: '' });
  const pIssues = [];
  const pp = await newPage(browser, proto.origin, pIssues, []);

  await step('the browser-only demo still works: samples, demo portal and plan notice', async () => {
    assert.ok(!fs.existsSync(path.join(tmp, 'proto', 'backend.js')), 'backend is not shipped in prototype mode');
    await pp.goto(proto.origin + '/#results');
    await pp.waitForSelector('.card');
    assert.ok((await count(pp, '.card')) >= 3);
    assert.ok(await pp.$$eval('.chip', (e) => e.some((x) => x.textContent === 'Sample firm')), 'sample firms are shown');
    assert.equal(await count(pp, '.chip.prem'), 0);
    await pp.click('#nav-firm');
    assert.equal(await count(pp, '.filters, .notice'), 0);
    await pp.click('#demo');
    await pp.waitForSelector('.dash-head');
    assert.equal(await text(pp, '.dash-head h1'), 'Strangford Solicitors');
    assert.equal(await count(pp, '#logo-in'), 1, 'demo keeps the free logo uploader');
    await pp.click('[data-tab="plan"]');
    assert.match(await text(pp, '#pane'), /not connected in this demo/);
  });

  await step('prototype registration, saving and sign-out still work', async () => {
    await pp.click('#signout'); await toast(pp, /Signed out/);
    await pp.click('[data-mode="register"]');
    await fill(pp, '#a-firm', 'Demo Firm LLP'); await fill(pp, '#a-email', 'demo@firm.example'); await fill(pp, '#a-pw', PW);
    await pp.click('#a-ok'); await pp.click('#authf button[type=submit]');
    await pp.waitForSelector('.dash-head');
    assert.equal(await text(pp, '.dash-head h1'), 'Demo Firm LLP');
    await fill(pp, '#p-town', 'Leeds'); await pp.click('#detf button[type=submit]');
    await toast(pp, /Details saved/);
    await pp.click('#signout'); await toast(pp, /Signed out/);
  });

  await step('no script errors or third-party requests in prototype mode', async () => {
    assert.deepEqual(pIssues, [], 'issues: ' + JSON.stringify(pIssues, null, 1));
  });
  await proto.close();
} finally {
  await browser.close();
  fs.rmSync(tmp, { recursive: true, force: true });
}

console.log(failures ? `\n${failures} UI TEST(S) FAILED` : '\nALL UI TESTS PASSED');
process.exit(failures ? 1 : 0);
