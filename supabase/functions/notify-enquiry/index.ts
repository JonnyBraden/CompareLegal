// Deploy with JWT verification off (the database trigger calls it without a user token):
//   supabase functions deploy notify-enquiry --no-verify-jwt
// Secrets: RESEND_API_KEY, MAIL_FROM (e.g. "Compare Legal <enquiries@comparelegal.com>").
import { createClient } from 'npm:@supabase/supabase-js@2';
import { handle } from './handler.ts';

const db = createClient(Deno.env.get('SUPABASE_URL')!, Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!, { auth: { persistSession: false } });

Deno.serve((req) => handle(req, {
  db, fetch, resendKey: Deno.env.get('RESEND_API_KEY'), mailFrom: Deno.env.get('MAIL_FROM'),
}));
