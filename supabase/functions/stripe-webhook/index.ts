// Stripe -> Supabase billing sync. This is the only writer of public.firm_billing, so a firm's
// Premium status can only ever come from a signature-verified Stripe event.
// Deploy with verify_jwt = false (see supabase/config.toml): Stripe does not send a Supabase JWT.
import { createClient } from 'npm:@supabase/supabase-js@2';
import { requireEnv } from '../_shared/http.ts';
import { handleStripeEvent, stripeFetch, verifyStripeSignature, type StripeSubscription } from '../_shared/stripe.ts';

Deno.serve(async (req) => {
  if (req.method !== 'POST') return new Response('Method not allowed', { status: 405 });

  // The signature is computed over the exact bytes Stripe sent, so read the raw text.
  const payload = await req.text();
  const valid = await verifyStripeSignature(payload, req.headers.get('stripe-signature'), requireEnv('STRIPE_WEBHOOK_SECRET'));
  if (!valid) return new Response('Invalid signature', { status: 400 });

  let event;
  try {
    event = JSON.parse(payload);
  } catch {
    return new Response('Bad payload', { status: 400 });
  }

  const stripeKey = requireEnv('STRIPE_SECRET_KEY');
  const admin = createClient(requireEnv('SUPABASE_URL'), requireEnv('SUPABASE_SERVICE_ROLE_KEY'));

  try {
    const result = await handleStripeEvent(event, {
      priceId: Deno.env.get('STRIPE_PRICE_ID') ?? undefined,
      retrieveSubscription: (id) => stripeFetch<StripeSubscription>(stripeKey, `/subscriptions/${id}`, {}, { method: 'GET' }),
      findFirmIdByCustomer: async (customerId) => {
        const { data } = await admin.from('firm_billing').select('firm_id').eq('stripe_customer_id', customerId).maybeSingle();
        return data?.firm_id ?? null;
      },
      upsertBilling: async (row) => {
        const { error } = await admin.from('firm_billing').upsert(row, { onConflict: 'firm_id' });
        if (error) throw error;
      },
    });
    console.log('stripe-webhook', event.type, JSON.stringify(result));
    return new Response(JSON.stringify(result), { status: 200, headers: { 'Content-Type': 'application/json' } });
  } catch (err) {
    // A 500 makes Stripe retry with backoff, which is what we want for transient failures.
    console.error('stripe-webhook failed', event?.type, err);
    return new Response('Webhook handler failed', { status: 500 });
  }
});
