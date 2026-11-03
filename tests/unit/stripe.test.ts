import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHmac } from 'node:crypto';
import {
  encodeForm, verifyStripeSignature, subscriptionToBilling, periodEndOf, handleStripeEvent,
  type StripeSubscription, type BillingRow, type WebhookDeps,
} from '../../supabase/functions/_shared/stripe.ts';

const SECRET = 'whsec_test_secret';
const sign = (payload: string, t: number, secret = SECRET) =>
  `t=${t},v1=${createHmac('sha256', secret).update(`${t}.${payload}`).digest('hex')}`;

test('encodeForm handles nesting, arrays and skips empty values', () => {
  const out = encodeForm({
    mode: 'subscription',
    line_items: [{ price: 'price_1', quantity: 1 }],
    subscription_data: { metadata: { firm_id: 'abc' } },
    skipped: undefined,
    alsoSkipped: null,
    allow_promotion_codes: true,
    name: 'Ashcombe & Reid',
  });
  const params = new URLSearchParams(out);
  assert.equal(params.get('mode'), 'subscription');
  assert.equal(params.get('line_items[0][price]'), 'price_1');
  assert.equal(params.get('line_items[0][quantity]'), '1');
  assert.equal(params.get('subscription_data[metadata][firm_id]'), 'abc');
  assert.equal(params.get('allow_promotion_codes'), 'true');
  assert.equal(params.get('name'), 'Ashcombe & Reid');
  assert.equal(params.has('skipped'), false);
  assert.equal(params.has('alsoSkipped'), false);
});

test('signature: accepts a correctly signed payload', async () => {
  const payload = '{"id":"evt_1"}';
  const now = 1_800_000_000_000;
  assert.equal(await verifyStripeSignature(payload, sign(payload, now / 1000), SECRET, { nowMs: now }), true);
});

test('signature: rejects a tampered payload, wrong secret, missing or malformed header', async () => {
  const payload = '{"id":"evt_1"}';
  const now = 1_800_000_000_000;
  const good = sign(payload, now / 1000);
  assert.equal(await verifyStripeSignature(payload + ' ', good, SECRET, { nowMs: now }), false);
  assert.equal(await verifyStripeSignature(payload, sign(payload, now / 1000, 'whsec_other'), SECRET, { nowMs: now }), false);
  assert.equal(await verifyStripeSignature(payload, null, SECRET, { nowMs: now }), false);
  assert.equal(await verifyStripeSignature(payload, 'garbage', SECRET, { nowMs: now }), false);
  assert.equal(await verifyStripeSignature(payload, `t=${now / 1000}`, SECRET, { nowMs: now }), false);
  assert.equal(await verifyStripeSignature(payload, good, '', { nowMs: now }), false);
});

test('signature: rejects stale timestamps (replay) but allows small clock skew', async () => {
  const payload = '{"id":"evt_1"}';
  const now = 1_800_000_000_000;
  assert.equal(await verifyStripeSignature(payload, sign(payload, now / 1000 - 600), SECRET, { nowMs: now }), false);
  assert.equal(await verifyStripeSignature(payload, sign(payload, now / 1000 - 120), SECRET, { nowMs: now }), true);
});

test('signature: accepts any matching v1 when Stripe sends several (secret rotation)', async () => {
  const payload = '{"id":"evt_1"}';
  const now = 1_800_000_000_000;
  const t = now / 1000;
  const real = createHmac('sha256', SECRET).update(`${t}.${payload}`).digest('hex');
  assert.equal(await verifyStripeSignature(payload, `t=${t},v1=${'0'.repeat(64)},v1=${real}`, SECRET, { nowMs: now }), true);
});

const sub = (over: Partial<StripeSubscription> = {}): StripeSubscription => ({
  id: 'sub_1', customer: 'cus_1', status: 'active', current_period_end: 1_800_000_000,
  metadata: { firm_id: 'firm-1' }, items: { data: [{ price: { id: 'price_premium' } }] }, ...over,
});

test('subscriptionToBilling maps the fields we store', () => {
  const row = subscriptionToBilling(sub({ cancel_at_period_end: true }), 'firm-1', '2026-11-03T00:00:00.000Z');
  assert.deepEqual(row, {
    firm_id: 'firm-1', stripe_customer_id: 'cus_1', stripe_subscription_id: 'sub_1', status: 'active',
    price_id: 'price_premium', current_period_end: new Date(1_800_000_000 * 1000).toISOString(),
    cancel_at_period_end: true, updated_at: '2026-11-03T00:00:00.000Z',
  });
});

test('period end is read from either API shape; expanded customer objects work', () => {
  assert.equal(periodEndOf(sub({ current_period_end: undefined, items: { data: [{ current_period_end: 1_900_000_000 }] } })), new Date(1_900_000_000 * 1000).toISOString());
  assert.equal(periodEndOf(sub({ current_period_end: undefined, items: { data: [{}] } })), null);
  assert.equal(subscriptionToBilling(sub({ customer: { id: 'cus_9' } }), 'f').stripe_customer_id, 'cus_9');
});

test('a scheduled cancellation (cancel_at) counts as cancel-at-period-end', () => {
  assert.equal(subscriptionToBilling(sub({ cancel_at: 1_850_000_000 }), 'f').cancel_at_period_end, true);
  assert.equal(subscriptionToBilling(sub({ cancel_at: null }), 'f').cancel_at_period_end, false);
});

// ---- webhook event handling, with Stripe and the database faked ----
function harness(retrieved: StripeSubscription, opts: { priceId?: string; customers?: Record<string, string> } = {}) {
  const upserts: BillingRow[] = [];
  const retrievals: string[] = [];
  const deps: WebhookDeps = {
    priceId: opts.priceId ?? 'price_premium',
    retrieveSubscription: async (id) => { retrievals.push(id); return retrieved; },
    findFirmIdByCustomer: async (c) => opts.customers?.[c] ?? null,
    upsertBilling: async (row) => { upserts.push(row); },
  };
  return { deps, upserts, retrievals };
}

test('checkout.session.completed activates the firm named in client_reference_id', async () => {
  const h = harness(sub({ metadata: {} }));
  const res = await handleStripeEvent({ id: 'e1', type: 'checkout.session.completed',
    data: { object: { mode: 'subscription', subscription: 'sub_1', client_reference_id: 'firm-77' } } }, h.deps);
  assert.deepEqual(res, { handled: true, firmId: 'firm-77', status: 'active' });
  assert.equal(h.upserts[0].firm_id, 'firm-77');
  assert.deepEqual(h.retrievals, ['sub_1']);
});

test('non-subscription checkouts and unrelated events are acknowledged but ignored', async () => {
  const h = harness(sub());
  assert.equal((await handleStripeEvent({ id: 'e', type: 'checkout.session.completed', data: { object: { mode: 'payment' } } }, h.deps)).handled, false);
  assert.equal((await handleStripeEvent({ id: 'e', type: 'invoice.paid', data: { object: {} } }, h.deps)).handled, false);
  assert.equal(h.upserts.length, 0);
  assert.equal(h.retrievals.length, 0);
});

test('subscription events re-read Stripe instead of trusting a possibly stale payload', async () => {
  // The delivered event says "active", but Stripe's current state is "canceled" (events arrived out of order).
  const h = harness(sub({ status: 'canceled' }));
  const res = await handleStripeEvent({ id: 'e', type: 'customer.subscription.updated',
    data: { object: sub({ status: 'active' }) } }, h.deps);
  assert.equal(res.status, 'canceled');
  assert.equal(h.upserts[0].status, 'canceled');
});

test('deleted subscriptions record the cancelled status', async () => {
  const h = harness(sub({ status: 'canceled' }));
  await handleStripeEvent({ id: 'e', type: 'customer.subscription.deleted', data: { object: sub({ status: 'canceled' }) } }, h.deps);
  assert.equal(h.upserts[0].status, 'canceled');
});

test('falls back to the stored Stripe customer when the subscription has no firm metadata', async () => {
  const h = harness(sub({ metadata: {} }), { customers: { cus_1: 'firm-from-customer' } });
  const res = await handleStripeEvent({ id: 'e', type: 'customer.subscription.created', data: { object: sub({ metadata: {} }) } }, h.deps);
  assert.equal(res.firmId, 'firm-from-customer');
});

test('a subscription we cannot attribute to a firm is acknowledged without writing', async () => {
  const h = harness(sub({ metadata: {} }));
  const res = await handleStripeEvent({ id: 'e', type: 'customer.subscription.created', data: { object: sub({ metadata: {} }) } }, h.deps);
  assert.equal(res.handled, false);
  assert.equal(h.upserts.length, 0);
});

test('subscriptions for other Stripe products are ignored', async () => {
  const h = harness(sub({ items: { data: [{ price: { id: 'price_something_else' } }] } }));
  const res = await handleStripeEvent({ id: 'e', type: 'customer.subscription.updated', data: { object: sub() } }, h.deps);
  assert.equal(res.handled, false);
  assert.equal(h.upserts.length, 0);
});

test('Stripe lookup failures propagate so the webhook returns 500 and Stripe retries', async () => {
  const deps: WebhookDeps = { priceId: 'p', retrieveSubscription: async () => { throw new Error('stripe down'); },
    findFirmIdByCustomer: async () => null, upsertBilling: async () => {} };
  await assert.rejects(handleStripeEvent({ id: 'e', type: 'customer.subscription.updated', data: { object: sub() } }, deps), /stripe down/);
});
