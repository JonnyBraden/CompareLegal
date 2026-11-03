// Opens the Stripe customer portal (update card, view invoices, cancel) for the signed-in firm.
import { createClient } from 'npm:@supabase/supabase-js@2';
import { json, preflight, requireEnv } from '../_shared/http.ts';
import { stripeFetch } from '../_shared/stripe.ts';

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
    const { data: firm } = await admin.from('firms').select('id').eq('owner_id', user.id).maybeSingle();
    if (!firm) return json({ error: 'No firm account found.' }, 404);
    const { data: billing } = await admin.from('firm_billing').select('stripe_customer_id').eq('firm_id', firm.id).maybeSingle();
    if (!billing?.stripe_customer_id) return json({ error: 'No billing account yet. Upgrade to Premium first.' }, 404);

    const siteUrl = requireEnv('SITE_URL').replace(/\/+$/, '');
    const session = await stripeFetch<{ url: string }>(requireEnv('STRIPE_SECRET_KEY'), '/billing_portal/sessions', {
      customer: billing.stripe_customer_id,
      return_url: `${siteUrl}/#portal`,
    });
    return json({ url: session.url });
  } catch (err) {
    console.error('create-portal-session', err);
    return json({ error: 'We could not open billing. Please try again.' }, 500);
  }
});
