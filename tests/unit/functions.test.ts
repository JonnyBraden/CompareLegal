// End-to-end tests of the real Edge Function entry points (index.ts), run under Node with a
// shimmed `Deno`, an in-memory Supabase (see loader.mjs) and a faked fetch for Stripe and Resend.
import { test, before, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { createHmac } from 'node:crypto';
import { db, resetDb } from './fake-supabase.ts';

type Handler = (req: Request) => Promise<Response>;
const handlers: Record<string, Handler> = {};
const env: Record<string, string> = {};
let current = '';

(globalThis as any).Deno = {
  env: { get: (k: string) => env[k] },
  serve: (h: Handler) => { handlers[current] = h; },
};

type Call = { url: string; method: string; headers: Record<string, string>; body: string };
let calls: Call[] = [];
let respond: (c: Call) => { status?: number; body: unknown } = () => ({ body: {} });

globalThis.fetch = (async (url: any, init: any = {}) => {
  const call: Call = { url: String(url), method: init.method ?? 'GET', headers: init.headers ?? {}, body: init.body ?? '' };
  calls.push(call);
  const r = respond(call);
  return new Response(JSON.stringify(r.body), { status: r.status ?? 200, headers: { 'Content-Type': 'application/json' } });
}) as typeof fetch;

before(async () => {
  for (const name of ['submit-enquiry', 'create-checkout-session', 'create-portal-session', 'stripe-webhook']) {
    current = name;
    await import(`../../supabase/functions/${name}/index.ts`);
  }
});

const FIRM_ID = '123e4567-e89b-12d3-a456-426614174000';
const post = (url: string, body: unknown, headers: Record<string, string> = {}) =>
  new Request(`https://x.supabase.co/functions/v1/${url}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...headers }, body: typeof body === 'string' ? body : JSON.stringify(body),
  });

beforeEach(() => {
  resetDb();
  calls = [];
  respond = () => ({ body: {} });
  Object.keys(env).forEach((k) => delete env[k]);
  Object.assign(env, {
    SUPABASE_URL: 'https://x.supabase.co', SUPABASE_ANON_KEY: 'anon', SUPABASE_SERVICE_ROLE_KEY: 'service',
    STRIPE_SECRET_KEY: 'sk_test_123', STRIPE_PRICE_ID: 'price_test', STRIPE_WEBHOOK_SECRET: 'whsec_x',
    SITE_URL: 'https://comparelegal.com/', RESEND_API_KEY: 're_123', MAIL_FROM: 'Compare Legal <enquiries@comparelegal.com>',
  });
  db.firms.push({ id: FIRM_ID, owner_id: 'user-1', name: 'Strangford Solicitors', email: 'hello@strangford.example', listed: true, suspended: false });
  db.tokens['good-token'] = { id: 'user-1', email: 'owner@strangford.example' };
});

const enquiry = (over: Record<string, unknown> = {}) => ({
  firmId: FIRM_ID, service: 'purchase', nation: 'Northern Ireland', price: 250000, total: 1500,
  name: 'Sam Client', email: 'sam@client.example', phone: '', message: 'Leasehold flat', ...over,
});
const form = (c: Call) => new URLSearchParams(c.body);

// ------------------------------------------------------------------ submit-enquiry
test('submit-enquiry: stores the enquiry and emails the firm with the client as reply-to', async () => {
  const res = await handlers['submit-enquiry'](post('submit-enquiry', enquiry()));
  assert.equal(res.status, 200);
  assert.equal(db.enquiries.length, 1);
  assert.equal(db.enquiries[0].firm_id, FIRM_ID);
  assert.equal(db.enquiries[0].property_price, 250000);
  const mail = calls.find((c) => c.url === 'https://api.resend.com/emails')!;
  const sent = JSON.parse(mail.body);
  assert.deepEqual(sent.to, ['hello@strangford.example']);
  assert.equal(sent.reply_to, 'sam@client.example');
  assert.match(sent.text, /Leasehold flat/);
  assert.equal(mail.headers.Authorization, 'Bearer re_123');
  assert.ok(db.enquiries[0].emailed_at, 'emailed_at recorded');
});

test('submit-enquiry: works without email configured (saved for the portal)', async () => {
  delete env.RESEND_API_KEY;
  const res = await handlers['submit-enquiry'](post('submit-enquiry', enquiry()));
  assert.equal(res.status, 200);
  assert.equal(db.enquiries.length, 1);
  assert.equal(calls.length, 0);
});

test('submit-enquiry: an email failure does not lose the enquiry', async () => {
  respond = () => ({ status: 500, body: { message: 'down' } });
  const res = await handlers['submit-enquiry'](post('submit-enquiry', enquiry()));
  assert.equal(res.status, 200);
  assert.equal(db.enquiries.length, 1);
  assert.equal(db.enquiries[0].emailed_at, undefined);
});

test('submit-enquiry: unlisted and suspended firms refuse requests', async () => {
  db.firms[0].listed = false;
  assert.equal((await handlers['submit-enquiry'](post('submit-enquiry', enquiry()))).status, 404);
  db.firms[0].listed = true; db.firms[0].suspended = true;
  assert.equal((await handlers['submit-enquiry'](post('submit-enquiry', enquiry()))).status, 404);
  assert.equal(db.enquiries.length, 0);
});

test('submit-enquiry: the honeypot silently drops bots', async () => {
  const res = await handlers['submit-enquiry'](post('submit-enquiry', enquiry({ hp: 'http://spam.example' })));
  assert.equal(res.status, 200);
  assert.equal(db.enquiries.length, 0);
  assert.equal(calls.length, 0);
});

test('submit-enquiry: validation errors return 400 and store nothing', async () => {
  assert.equal((await handlers['submit-enquiry'](post('submit-enquiry', enquiry({ email: 'nope' })))).status, 400);
  assert.equal((await handlers['submit-enquiry'](post('submit-enquiry', 'not json'))).status, 400);
  assert.equal(db.enquiries.length, 0);
});

test('submit-enquiry: the same client is limited to 3 requests per firm per hour', async () => {
  for (let i = 0; i < 3; i++) assert.equal((await handlers['submit-enquiry'](post('submit-enquiry', enquiry()))).status, 200);
  assert.equal((await handlers['submit-enquiry'](post('submit-enquiry', enquiry()))).status, 429);
  assert.equal((await handlers['submit-enquiry'](post('submit-enquiry', enquiry({ email: 'other@client.example' })))).status, 200);
});

test('submit-enquiry: a firm is protected from floods across many addresses', async () => {
  const now = new Date().toISOString();
  for (let i = 0; i < 40; i++) db.enquiries.push({ id: `e${i}`, firm_id: FIRM_ID, email: `u${i}@x.example`, created_at: now });
  assert.equal((await handlers['submit-enquiry'](post('submit-enquiry', enquiry()))).status, 429);
});

test('submit-enquiry: database errors give a generic 500, not internals', async () => {
  db.failInsert = true;
  const res = await handlers['submit-enquiry'](post('submit-enquiry', enquiry()));
  assert.equal(res.status, 500);
  assert.doesNotMatch(await res.text(), /insert failed/);
});

test('submit-enquiry: answers CORS preflight and rejects GET', async () => {
  const pre = await handlers['submit-enquiry'](new Request('https://x/', { method: 'OPTIONS' }));
  assert.equal(pre.status, 200);
  assert.equal(pre.headers.get('Access-Control-Allow-Origin'), '*');
  assert.equal((await handlers['submit-enquiry'](new Request('https://x/', { method: 'GET' }))).status, 405);
});

// ------------------------------------------------------------------ create-checkout-session
const authed = (url: string, token = 'good-token') => post(url, {}, { Authorization: `Bearer ${token}` });

test('checkout: requires a signed-in firm', async () => {
  assert.equal((await handlers['create-checkout-session'](authed('create-checkout-session', 'bad-token'))).status, 401);
  assert.equal(calls.length, 0);
});

test('checkout: creates a Stripe customer once, then a subscription Checkout session tied to the firm', async () => {
  respond = (c) => c.url.endsWith('/customers') ? { body: { id: 'cus_new' } } : { body: { url: 'https://checkout.stripe.com/c/pay/abc' } };
  const res = await handlers['create-checkout-session'](authed('create-checkout-session'));
  assert.equal(res.status, 200);
  assert.deepEqual(await res.json(), { url: 'https://checkout.stripe.com/c/pay/abc' });

  const customer = calls.find((c) => c.url.endsWith('/v1/customers'))!;
  assert.equal(customer.headers['Idempotency-Key'], `customer-${FIRM_ID}`);
  assert.equal(form(customer).get('metadata[firm_id]'), FIRM_ID);
  assert.equal(db.firm_billing[0].stripe_customer_id, 'cus_new');

  const session = form(calls.find((c) => c.url.endsWith('/v1/checkout/sessions'))!);
  assert.equal(session.get('mode'), 'subscription');
  assert.equal(session.get('customer'), 'cus_new');
  assert.equal(session.get('client_reference_id'), FIRM_ID);
  assert.equal(session.get('line_items[0][price]'), 'price_test');
  assert.equal(session.get('subscription_data[metadata][firm_id]'), FIRM_ID);
  assert.equal(session.get('success_url'), 'https://comparelegal.com/?billing=success#portal');
  assert.equal(session.get('cancel_url'), 'https://comparelegal.com/?billing=cancelled#portal');
  assert.equal(calls[0].headers.Authorization, 'Bearer sk_test_123');
});

test('checkout: reuses an existing Stripe customer', async () => {
  db.firm_billing.push({ firm_id: FIRM_ID, stripe_customer_id: 'cus_existing', status: 'canceled' });
  respond = () => ({ body: { url: 'https://checkout.stripe.com/c/pay/xyz' } });
  const res = await handlers['create-checkout-session'](authed('create-checkout-session'));
  assert.equal(res.status, 200);
  assert.equal(calls.some((c) => c.url.endsWith('/v1/customers')), false);
  assert.equal(form(calls[0]).get('customer'), 'cus_existing');
});

test('checkout: refuses to start a second subscription', async () => {
  db.firm_billing.push({ firm_id: FIRM_ID, stripe_customer_id: 'cus_1', status: 'active' });
  const res = await handlers['create-checkout-session'](authed('create-checkout-session'));
  assert.equal(res.status, 409);
  assert.equal(calls.length, 0);
});

test('checkout: Stripe errors surface as a generic message', async () => {
  respond = () => ({ status: 400, body: { error: { message: 'No such price: price_test' } } });
  const res = await handlers['create-checkout-session'](authed('create-checkout-session'));
  assert.equal(res.status, 500);
  assert.doesNotMatch(await res.text(), /No such price/);
});

// ------------------------------------------------------------------ create-portal-session
test('portal: needs a Stripe customer, then returns the billing portal URL', async () => {
  assert.equal((await handlers['create-portal-session'](authed('create-portal-session', 'bad'))).status, 401);
  assert.equal((await handlers['create-portal-session'](authed('create-portal-session'))).status, 404);

  db.firm_billing.push({ firm_id: FIRM_ID, stripe_customer_id: 'cus_1', status: 'active' });
  respond = () => ({ body: { url: 'https://billing.stripe.com/p/session/abc' } });
  const res = await handlers['create-portal-session'](authed('create-portal-session'));
  assert.deepEqual(await res.json(), { url: 'https://billing.stripe.com/p/session/abc' });
  const p = form(calls[0]);
  assert.equal(p.get('customer'), 'cus_1');
  assert.equal(p.get('return_url'), 'https://comparelegal.com/#portal');
});

// ------------------------------------------------------------------ stripe-webhook
const signed = (payload: string, secret = 'whsec_x') => {
  const t = Math.floor(Date.now() / 1000);
  return { 'stripe-signature': `t=${t},v1=${createHmac('sha256', secret).update(`${t}.${payload}`).digest('hex')}` };
};
const checkoutEvent = JSON.stringify({ id: 'evt_1', type: 'checkout.session.completed',
  data: { object: { mode: 'subscription', subscription: 'sub_1', client_reference_id: FIRM_ID } } });
const stripeSub = { id: 'sub_1', customer: 'cus_1', status: 'active', current_period_end: 1_900_000_000,
  metadata: { firm_id: FIRM_ID }, items: { data: [{ price: { id: 'price_test' } }] } };

test('webhook: rejects unsigned and wrongly signed requests without touching the database', async () => {
  for (const headers of [{}, signed(checkoutEvent, 'whsec_wrong')]) {
    const res = await handlers['stripe-webhook'](new Request('https://x/', { method: 'POST', headers, body: checkoutEvent }));
    assert.equal(res.status, 400);
  }
  assert.equal(db.firm_billing.length, 0);
  assert.equal(calls.length, 0);
});

test('webhook: a signed checkout completion activates Premium from the subscription Stripe reports', async () => {
  respond = () => ({ body: stripeSub });
  const res = await handlers['stripe-webhook'](new Request('https://x/', { method: 'POST', headers: signed(checkoutEvent), body: checkoutEvent }));
  assert.equal(res.status, 200);
  assert.equal(calls[0].method, 'GET');
  assert.equal(calls[0].url, 'https://api.stripe.com/v1/subscriptions/sub_1');
  assert.equal(db.firm_billing.length, 1);
  assert.equal(db.firm_billing[0].firm_id, FIRM_ID);
  assert.equal(db.firm_billing[0].status, 'active');
  assert.equal(db.firm_billing[0].stripe_subscription_id, 'sub_1');
});

test('webhook: a later cancellation updates the same row', async () => {
  respond = () => ({ body: stripeSub });
  await handlers['stripe-webhook'](new Request('https://x/', { method: 'POST', headers: signed(checkoutEvent), body: checkoutEvent }));
  respond = () => ({ body: { ...stripeSub, status: 'canceled' } });
  const ev = JSON.stringify({ id: 'evt_2', type: 'customer.subscription.deleted', data: { object: stripeSub } });
  const res = await handlers['stripe-webhook'](new Request('https://x/', { method: 'POST', headers: signed(ev), body: ev }));
  assert.equal(res.status, 200);
  assert.equal(db.firm_billing.length, 1);
  assert.equal(db.firm_billing[0].status, 'canceled');
});

test('webhook: Stripe lookup failure returns 500 so Stripe retries', async () => {
  respond = () => ({ status: 500, body: {} });
  const res = await handlers['stripe-webhook'](new Request('https://x/', { method: 'POST', headers: signed(checkoutEvent), body: checkoutEvent }));
  assert.equal(res.status, 500);
});

test('webhook: only POST is accepted', async () => {
  assert.equal((await handlers['stripe-webhook'](new Request('https://x/', { method: 'GET' }))).status, 405);
});
