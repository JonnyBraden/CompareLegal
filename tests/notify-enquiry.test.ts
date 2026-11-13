// Run: node --experimental-strip-types --test tests/
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { handle } from '../supabase/functions/notify-enquiry/handler.ts';
import { composeFirmEmail } from '../supabase/functions/notify-enquiry/email.ts';

const NOW = Date.parse('2026-11-13T15:00:00Z');
const ID = '11111111-2222-3333-4444-555555555555';
const FIRM = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee';

// Minimal fake of the supabase-js query builder over in-memory tables.
function fakeDb(tables: any, users: any = {}) {
  const from = (name: string) => {
    let rows = () => tables[name];
    const filters: ((r: any) => boolean)[] = [];
    let patch: any = null, head = false;
    const q: any = {
      update(p: any) { patch = p; return q; },
      select(_c?: string, o?: any) { head = !!o?.head; return q; },
      eq(k: string, v: any) { filters.push(r => r[k] === v); return q; },
      is(k: string, v: any) { filters.push(r => (r[k] ?? null) === v); return q; },
      not(k: string, _op: string, _v: any) { filters.push(r => r[k] != null); return q; },
      gte(k: string, v: any) { filters.push(r => r[k] >= v); return q; },
      _run() { const m = rows().filter((r: any) => filters.every(f => f(r))); if (patch) m.forEach((r: any) => Object.assign(r, patch)); return m; },
      async maybeSingle() { const m = q._run(); return { data: m[0] ?? null, error: null }; },
      then(res: any) { const m = q._run(); return Promise.resolve(head ? { count: m.length, error: null } : { data: m, error: null }).then(res); },
    };
    return q;
  };
  return { from, auth: { admin: { getUserById: async (id: string) => ({ data: { user: users[id] ?? null } }) } } };
}
const mkReq = (body: any) => new Request('http://x/', { method: 'POST', body: JSON.stringify(body) });
function setup(over: any = {}) {
  const enquiry = { id: ID, firm_id: FIRM, svc: 'purchase', nation: 'Northern Ireland', price: 250000, total: 900, name: 'Ann Client', email: 'ann@example.com', phone: '', msg: 'Hello', created_at: '2026-11-13T14:59:00Z', emailed_at: null, ...over.enquiry };
  const firm = { id: FIRM, name: 'Test & Co', email: 'enquiries@testco.example', owner: 'u1', listed: true, sample: false, ...over.firm };
  const tables = { enquiries: [enquiry, ...(over.more ?? [])], firms: [firm] };
  const sent: any[] = [];
  const fetchImpl: any = async (url: string, init: any) => { sent.push({ url, body: JSON.parse(init.body), auth: init.headers.Authorization }); return new Response('{}', { status: over.status ?? 200 }); };
  const deps = { db: fakeDb(tables, { u1: { email: 'owner@testco.example' } }), fetch: fetchImpl, resendKey: 're_test', mailFrom: 'Compare Legal <enquiries@comparelegal.com>', now: () => NOW };
  return { tables, sent, deps, enquiry };
}

test('emails the firm once, with the client as reply-to', async () => {
  const { sent, deps, enquiry } = setup();
  const r = await handle(mkReq({ id: ID }), deps);
  assert.equal(r.status, 200);
  assert.equal(sent.length, 1);
  assert.deepEqual(sent[0].body.to, ['enquiries@testco.example']);
  assert.equal(sent[0].body.reply_to, 'ann@example.com');
  assert.equal(sent[0].auth, 'Bearer re_test');
  assert.match(sent[0].body.subject, /Buying a home/);
  assert.ok(enquiry.emailed_at);
  const again = await handle(mkReq({ id: ID }), deps);
  assert.equal((await again.json()).skipped, 'not found, too old or already emailed');
  assert.equal(sent.length, 1);
});

test('accepts the database webhook shape {record:{id}}', async () => {
  const { sent, deps } = setup();
  await handle(mkReq({ record: { id: ID } }), deps);
  assert.equal(sent.length, 1);
});

test('falls back to the account owner email when the firm has none', async () => {
  const { sent, deps } = setup({ firm: { email: '' } });
  await handle(mkReq({ id: ID }), deps);
  assert.deepEqual(sent[0].body.to, ['owner@testco.example']);
});

test('ignores old enquiries, sample firms, hidden listings and bad ids', async () => {
  for (const over of [{ enquiry: { created_at: '2026-11-13T10:00:00Z' } }, { firm: { sample: true } }, { firm: { listed: false } }]) {
    const { sent, deps } = setup(over);
    await handle(mkReq({ id: ID }), deps);
    assert.equal(sent.length, 0, JSON.stringify(over));
  }
  const { sent, deps } = setup();
  assert.equal((await handle(mkReq({ id: "1' or 1=1" }), deps)).status, 400);
  assert.equal((await handle(new Request('http://x/'), deps)).status, 405);
  assert.equal(sent.length, 0);
});

test('does nothing until Resend is configured', async () => {
  const { sent, deps, enquiry } = setup();
  await handle(mkReq({ id: ID }), { ...deps, resendKey: undefined });
  assert.equal(sent.length, 0);
  assert.equal(enquiry.emailed_at, null);
});

test('releases the claim if Resend fails, so a retry can send', async () => {
  const { deps, enquiry } = setup({ status: 500 });
  const r = await handle(mkReq({ id: ID }), deps);
  assert.equal(r.status, 502);
  assert.equal(enquiry.emailed_at, null);
});

test('rate-limits a flood of enquiries to one firm', async () => {
  const more = Array.from({ length: 41 }, (_, i) => ({ id: `x${i}`, firm_id: FIRM, created_at: '2026-11-13T14:30:00Z', emailed_at: '2026-11-13T14:30:01Z' }));
  const { sent, deps } = setup({ more });
  const r = await handle(mkReq({ id: ID }), deps);
  assert.equal((await r.json()).skipped, 'rate limited');
  assert.equal(sent.length, 0);
});

test('email is plain text and keeps client text out of headers', () => {
  const m = composeFirmEmail('Test & Co', { id: ID, firm_id: FIRM, svc: 'grant', nation: 'Scotland', price: null, total: 1200, name: 'Bob\r\nBcc: x@y.z', email: 'bob@example.com', phone: '07700 900123', msg: '<b>hi</b>' });
  assert.match(m.text, /Name: Bob Bcc: x@y\.z/);
  assert.match(m.text, /Total the client saw for your firm: £1,200/);
  assert.match(m.text, /<b>hi<\/b>/);
  assert.equal(m.subject, 'New quote request: Grant of probate / confirmation only');
});
