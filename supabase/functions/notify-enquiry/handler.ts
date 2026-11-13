// Emails a firm about a new enquiry. Called by a database trigger on public.enquiries with {"id": ...}.
//
// Safe to expose without a secret: it only acts on a real, recent enquiry that hasn't been emailed,
// and it claims the enquiry (sets emailed_at) before sending, so each one is emailed at most once.
import { composeFirmEmail, isEmail, type Enquiry } from './email.ts';

export type Deps = {
  db: any; // supabase-js client with the service role key
  fetch: typeof fetch;
  resendKey?: string;
  mailFrom?: string;
  now?: () => number;
};

const MAX_AGE_MS = 15 * 60_000;
const PER_FIRM_PER_HOUR = 40;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

export async function handle(req: Request, d: Deps): Promise<Response> {
  if (req.method !== 'POST') return json({ error: 'Method not allowed' }, 405);
  let id = '';
  try { const b = await req.json(); id = String(b?.id ?? b?.record?.id ?? ''); } catch { /* fall through */ }
  if (!UUID.test(id)) return json({ error: 'Bad request' }, 400);
  if (!d.resendKey || !d.mailFrom) return json({ skipped: 'email not configured' });
  const now = (d.now ?? Date.now)();

  // Claim the enquiry: only one caller can move emailed_at from null.
  const since = new Date(now - MAX_AGE_MS).toISOString();
  const { data: e, error } = await d.db.from('enquiries')
    .update({ emailed_at: new Date(now).toISOString() })
    .eq('id', id).is('emailed_at', null).gte('created_at', since)
    .select('id, firm_id, svc, nation, price, total, name, email, phone, msg, created_at')
    .maybeSingle();
  if (error) { console.error('notify-enquiry: claim failed', error); return json({ error: 'claim failed' }, 500); }
  if (!e) return json({ skipped: 'not found, too old or already emailed' });
  const enq = e as Enquiry;

  const { data: firm } = await d.db.from('firms').select('id, name, email, owner, listed, sample').eq('id', enq.firm_id).maybeSingle();
  if (!firm || firm.sample || !firm.listed) return json({ skipped: 'firm not live' });

  // Stop an enquiry flood turning into an email flood.
  const hourAgo = new Date(now - 3600_000).toISOString();
  const { count } = await d.db.from('enquiries').select('id', { count: 'exact', head: true })
    .eq('firm_id', firm.id).not('emailed_at', 'is', null).gte('created_at', hourAgo);
  if ((count ?? 0) > PER_FIRM_PER_HOUR) return json({ skipped: 'rate limited' });

  let to = isEmail(firm.email) ? String(firm.email).trim() : '';
  if (!to && firm.owner) {
    const { data: u } = await d.db.auth.admin.getUserById(firm.owner);
    if (isEmail(u?.user?.email)) to = u.user.email;
  }
  if (!to) return json({ skipped: 'firm has no email' });

  const mail = composeFirmEmail(firm.name, enq);
  const res = await d.fetch('https://api.resend.com/emails', {
    method: 'POST',
    headers: { Authorization: `Bearer ${d.resendKey}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ from: d.mailFrom, to: [to], reply_to: isEmail(enq.email) ? enq.email.trim() : undefined, subject: mail.subject, text: mail.text }),
  });
  if (!res.ok) {
    console.error('notify-enquiry: Resend error', res.status, await res.text());
    await d.db.from('enquiries').update({ emailed_at: null }).eq('id', id); // let a retry try again
    return json({ error: 'send failed' }, 502);
  }
  return json({ ok: true });
}
