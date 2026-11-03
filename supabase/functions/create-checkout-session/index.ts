// Starts a Stripe Checkout session for the signed-in firm's Premium subscription.
// Called from the portal with the firm's Supabase access token. Returns { url } to redirect to.
import { createClient } from 'npm:@supabase/supabase-js@2';
import { json, preflight, requireEnv } from '../_shared/http.ts';
import { stripeFetch } from '../_shared/stripe.ts';

const ACTIVE = ['active', 'trialing', 'past_due'];

Deno.serve(async (req) => {
  const pre = preflight(req);
  if (pre) return pre;
  if (req.method !== 'POST') return json({ error: 'Method not allowed' }, 405);

  try {
    const supabaseUrl = requireEnv('SUPABASE_URL');
    const userClient = createClient(supabaseUrl, requireEnv('SUPABASE_ANON_KEY'), {
      global: { headers: { Authorization: req.headers.get('Authorization') ?? '' } },
    });
    const { data: { user } } = await userClient.auth.getUser();
    if (!user) return json({ error: 'Please sign in again.' }, 401);

    const admin = createClient(supabaseUrl, requireEnv('SUPABASE_SERVICE_ROLE_KEY'));
    const { data: firm } = await admin.from('firms').select('id, name, email').eq('owner_id', user.id).maybeSingle();
    if (!firm) return json({ error: 'No firm account found.' }, 404);

    const { data: billing } = await admin.from('firm_billing').select('*').eq('firm_id', firm.id).maybeSingle();
    if (billing && ACTIVE.includes(billing.status)) return json({ error: 'already_subscribed' }, 409);

    const stripeKey = requireEnv('STRIPE_SECRET_KEY');
    const siteUrl = requireEnv('SITE_URL').replace(/\/+$/, '');

    let customerId: string | null = billing?.stripe_customer_id ?? null;
    if (!customerId) {
      // The idempotency key makes a double-click return the same customer instead of creating two.
      const customer = await stripeFetch<{ id: string }>(
        stripeKey, '/customers',
        { email: user.email, name: firm.name, metadata: { firm_id: firm.id } },
        { idempotencyKey: `customer-${firm.id}` },
      );
      customerId = customer.id;
      const { error } = await admin.from('firm_billing')
        .upsert({ firm_id: firm.id, stripe_customer_id: customerId }, { onConflict: 'firm_id', ignoreDuplicates: true });
      if (error) throw error;
    }

    const session = await stripeFetch<{ url: string }>(stripeKey, '/checkout/sessions', {
      mode: 'subscription',
      customer: customerId,
      client_reference_id: firm.id,
      line_items: [{ price: requireEnv('STRIPE_PRICE_ID'), quantity: 1 }],
      allow_promotion_codes: true,
      // The firm id rides on the subscription so every later webhook can find the firm.
      subscription_data: { metadata: { firm_id: firm.id } },
      success_url: `${siteUrl}/?billing=success#portal`,
      cancel_url: `${siteUrl}/?billing=cancelled#portal`,
    });
    return json({ url: session.url });
  } catch (err) {
    console.error('create-checkout-session', err);
    return json({ error: 'We could not start checkout. Please try again.' }, 500);
  }
});
