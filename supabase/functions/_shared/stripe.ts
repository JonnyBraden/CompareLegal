// Stripe helpers written against web-standard APIs only (fetch, crypto.subtle), so the same code
// runs in Supabase Edge Functions (Deno) and in the Node unit tests. No Stripe SDK on purpose:
// it keeps the functions small and independent of SDK/API-version churn.

export type StripeSubscription = {
  id: string;
  customer: string | { id: string };
  status: string;
  cancel_at_period_end?: boolean;
  cancel_at?: number | null;
  current_period_end?: number;
  metadata?: Record<string, string>;
  items?: { data?: Array<{ current_period_end?: number; price?: { id?: string } }> };
};

export type BillingRow = {
  firm_id: string;
  stripe_customer_id: string;
  stripe_subscription_id: string;
  status: string;
  price_id: string | null;
  current_period_end: string | null;
  cancel_at_period_end: boolean;
  updated_at: string;
};

/** Encodes nested objects/arrays the way Stripe's API expects: a[b]=1, items[0][price]=x. */
export function encodeForm(params: Record<string, unknown>): string {
  const pairs: string[] = [];
  const walk = (key: string, value: unknown) => {
    if (value === undefined || value === null) return;
    if (Array.isArray(value)) value.forEach((v, i) => walk(`${key}[${i}]`, v));
    else if (typeof value === 'object') {
      for (const [k, v] of Object.entries(value as Record<string, unknown>)) walk(`${key}[${k}]`, v);
    } else pairs.push(`${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`);
  };
  for (const [k, v] of Object.entries(params)) walk(k, v);
  return pairs.join('&');
}

export async function stripeFetch<T = any>(
  secretKey: string,
  path: string,
  params: Record<string, unknown> = {},
  opts: { method?: 'GET' | 'POST'; idempotencyKey?: string } = {},
): Promise<T> {
  const method = opts.method ?? 'POST';
  const body = encodeForm(params);
  const url = `https://api.stripe.com/v1${path}${method === 'GET' && body ? `?${body}` : ''}`;
  const headers: Record<string, string> = { Authorization: `Bearer ${secretKey}` };
  if (method === 'POST') headers['Content-Type'] = 'application/x-www-form-urlencoded';
  if (opts.idempotencyKey) headers['Idempotency-Key'] = opts.idempotencyKey;
  const res = await fetch(url, { method, headers, body: method === 'POST' ? body : undefined });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const msg = data?.error?.message ?? `Stripe request failed (${res.status})`;
    throw new Error(`${path}: ${msg}`);
  }
  return data as T;
}

const enc = new TextEncoder();

function timingSafeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

async function hmacSha256Hex(secret: string, message: string): Promise<string> {
  const key = await crypto.subtle.importKey('raw', enc.encode(secret), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  const sig = await crypto.subtle.sign('HMAC', key, enc.encode(message));
  return [...new Uint8Array(sig)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

/**
 * Verifies a Stripe-Signature header against the raw request body.
 * Header format: "t=1700000000,v1=<hex>,v1=<hex>". Rejects stale timestamps (replay protection).
 */
export async function verifyStripeSignature(
  payload: string,
  header: string | null,
  secret: string,
  opts: { toleranceSeconds?: number; nowMs?: number } = {},
): Promise<boolean> {
  if (!header || !secret) return false;
  const tolerance = opts.toleranceSeconds ?? 300;
  const now = opts.nowMs ?? Date.now();
  let timestamp = '';
  const signatures: string[] = [];
  for (const part of header.split(',')) {
    const [k, v] = part.split('=');
    if (k === 't') timestamp = v;
    else if (k === 'v1' && v) signatures.push(v);
  }
  const t = Number(timestamp);
  if (!timestamp || !Number.isFinite(t) || signatures.length === 0) return false;
  if (Math.abs(now / 1000 - t) > tolerance) return false;
  const expected = await hmacSha256Hex(secret, `${timestamp}.${payload}`);
  return signatures.some((s) => timingSafeEqual(s, expected));
}

export const customerIdOf = (sub: StripeSubscription): string =>
  typeof sub.customer === 'string' ? sub.customer : sub.customer.id;

/** Newer Stripe API versions moved current_period_end onto the subscription item; read both. */
export function periodEndOf(sub: StripeSubscription): string | null {
  const ts = sub.current_period_end ?? sub.items?.data?.[0]?.current_period_end;
  return typeof ts === 'number' ? new Date(ts * 1000).toISOString() : null;
}

export function subscriptionHasPrice(sub: StripeSubscription, priceId: string | undefined): boolean {
  if (!priceId) return true;
  return (sub.items?.data ?? []).some((i) => i.price?.id === priceId);
}

export function subscriptionToBilling(sub: StripeSubscription, firmId: string, nowIso = new Date().toISOString()): BillingRow {
  return {
    firm_id: firmId,
    stripe_customer_id: customerIdOf(sub),
    stripe_subscription_id: sub.id,
    status: sub.status,
    price_id: sub.items?.data?.[0]?.price?.id ?? null,
    current_period_end: periodEndOf(sub),
    cancel_at_period_end: !!sub.cancel_at_period_end || sub.cancel_at != null,
    updated_at: nowIso,
  };
}

export type StripeEvent = { id: string; type: string; data: { object: any } };

export type WebhookDeps = {
  priceId?: string;
  /** Always re-read the subscription from Stripe so out-of-order events cannot overwrite newer state. */
  retrieveSubscription: (id: string) => Promise<StripeSubscription>;
  findFirmIdByCustomer: (customerId: string) => Promise<string | null>;
  upsertBilling: (row: BillingRow) => Promise<void>;
};

export type WebhookResult = { handled: boolean; reason?: string; firmId?: string; status?: string };

const SUBSCRIPTION_EVENTS = new Set([
  'customer.subscription.created',
  'customer.subscription.updated',
  'customer.subscription.deleted',
  'customer.subscription.paused',
  'customer.subscription.resumed',
]);

export async function handleStripeEvent(event: StripeEvent, deps: WebhookDeps): Promise<WebhookResult> {
  let subscriptionId: string | null = null;
  let firmHint: string | null = null;

  if (event.type === 'checkout.session.completed') {
    const s = event.data.object;
    if (s.mode !== 'subscription' || !s.subscription) return { handled: false, reason: 'not a subscription checkout' };
    subscriptionId = typeof s.subscription === 'string' ? s.subscription : s.subscription.id;
    firmHint = s.client_reference_id ?? s.metadata?.firm_id ?? null;
  } else if (SUBSCRIPTION_EVENTS.has(event.type)) {
    subscriptionId = event.data.object.id;
    firmHint = event.data.object.metadata?.firm_id ?? null;
  } else {
    return { handled: false, reason: `ignored ${event.type}` };
  }

  const sub = await deps.retrieveSubscription(subscriptionId!);
  if (!subscriptionHasPrice(sub, deps.priceId)) return { handled: false, reason: 'subscription is for a different price' };

  const firmId = sub.metadata?.firm_id || firmHint || (await deps.findFirmIdByCustomer(customerIdOf(sub)));
  if (!firmId) return { handled: false, reason: 'no firm found for this subscription' };

  await deps.upsertBilling(subscriptionToBilling(sub, firmId));
  return { handled: true, firmId, status: sub.status };
}
