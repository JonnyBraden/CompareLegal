// Public endpoint: a client asks a listed firm for a quote. Validates, rate-limits, stores the
// enquiry and emails the firm (every firm, free or Premium, gets the email so no client is left
// waiting). Deploy with verify_jwt = false (see supabase/config.toml).
//
// Email is sent through Resend when RESEND_API_KEY and MAIL_FROM are set. Without them the
// enquiry is still saved and shows in the firm's portal.
import { createClient } from 'npm:@supabase/supabase-js@2';
import { json, preflight, requireEnv } from '../_shared/http.ts';
import { composeFirmEmail, validateEnquiry } from '../_shared/enquiry.ts';

const PER_CLIENT_PER_FIRM_PER_HOUR = 3;
const PER_FIRM_PER_HOUR = 40;

Deno.serve(async (req) => {
  const pre = preflight(req);
  if (pre) return pre;
  if (req.method !== 'POST') return json({ error: 'Method not allowed' }, 405);

  let raw: any;
  try {
    raw = await req.json();
  } catch {
    return json({ error: 'Invalid request.' }, 400);
  }

  // Honeypot: real users never see or fill this field. Pretend success so bots learn nothing.
  if (typeof raw?.hp === 'string' && raw.hp.trim() !== '') return json({ ok: true });

  const v = validateEnquiry(raw);
  if (!v.ok) return json({ error: v.error }, 400);
  const e = v.value;

  try {
    const admin = createClient(requireEnv('SUPABASE_URL'), requireEnv('SUPABASE_SERVICE_ROLE_KEY'));

    const { data: firm } = await admin.from('firms')
      .select('id, name, email').eq('id', e.firmId).eq('listed', true).eq('suspended', false).maybeSingle();
    if (!firm) return json({ error: 'That firm is not accepting requests right now.' }, 404);

    const since = new Date(Date.now() - 3600_000).toISOString();
    const [mine, all] = await Promise.all([
      admin.from('enquiries').select('id', { count: 'exact', head: true })
        .eq('firm_id', firm.id).eq('email', e.email).gte('created_at', since),
      admin.from('enquiries').select('id', { count: 'exact', head: true })
        .eq('firm_id', firm.id).gte('created_at', since),
    ]);
    if ((mine.count ?? 0) >= PER_CLIENT_PER_FIRM_PER_HOUR || (all.count ?? 0) >= PER_FIRM_PER_HOUR) {
      return json({ error: 'Too many requests. Please try again later.' }, 429);
    }

    const { data: row, error } = await admin.from('enquiries').insert({
      firm_id: firm.id, service: e.service, nation: e.nation,
      property_price: e.price, quoted_total: e.total,
      name: e.name, email: e.email, phone: e.phone, message: e.message,
    }).select('id').single();
    if (error) throw error;

    const resendKey = Deno.env.get('RESEND_API_KEY');
    const from = Deno.env.get('MAIL_FROM');
    if (resendKey && from && firm.email) {
      const mail = composeFirmEmail(firm.name, e);
      const res = await fetch('https://api.resend.com/emails', {
        method: 'POST',
        headers: { Authorization: `Bearer ${resendKey}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ from, to: [firm.email], reply_to: e.email, subject: mail.subject, text: mail.text }),
      });
      if (res.ok) await admin.from('enquiries').update({ emailed_at: new Date().toISOString() }).eq('id', row.id);
      else console.error('submit-enquiry: email failed', res.status, await res.text());
    }

    return json({ ok: true });
  } catch (err) {
    console.error('submit-enquiry', err);
    return json({ error: 'We could not send your request. Please try again.' }, 500);
  }
});
