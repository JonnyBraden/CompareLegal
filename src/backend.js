/* Compare Legal data layer.
 *
 * When config.js supplies Supabase keys, firm accounts, listings, enquiries and billing live in
 * Supabase (auth, Postgres, storage, edge functions) and Stripe, and window.CL.live is true.
 * Without keys, window.CL.live is false and the app runs its browser-only prototype mode.
 *
 * Security model: the anon key below is public by design. Every rule that matters (who can read
 * or write what, what counts as Premium) is enforced by Postgres row-level security and the Edge
 * Functions, never by this file.
 */
(function () {
  'use strict';
  const CFG = window.CL_CONFIG || {};
  const live = !!(CFG.supabaseUrl && CFG.supabaseAnonKey && window.supabase && window.supabase.createClient);
  const CL = (window.CL = {
    live,
    cfg: CFG,
    firms: [],          // public listings, in the app's firm shape
    user: null,         // { id, email } while signed in
    me: null,           // { firm, billing, enquiries } for the signed-in firm
    recovery: false,    // true while the user arrived from a password-reset email
    onChange: function () {},
    // Filled in by app.html so this file stays free of the service catalogue.
    serviceIds: [], bandedIds: [], bandKeys: [],
  });
  if (!live) return;

  const sb = window.supabase.createClient(CFG.supabaseUrl, CFG.supabaseAnonKey, {
    auth: { flowType: 'pkce', persistSession: true, autoRefreshToken: true, detectSessionInUrl: true },
  });
  const PREMIUM_STATUSES = ['active', 'trialing', 'past_due'];

  // Subscribe straight away, not inside init(): when someone arrives from a password-reset email,
  // supabase-js fires PASSWORD_RECOVERY while it initialises, before init() would have subscribed.
  let ready = false;
  sb.auth.onAuthStateChange((event, session) => {
    if (event === 'PASSWORD_RECOVERY') CL.recovery = true;
    if (!ready) return; // init() loads everything itself once initialisation has finished
    const uid = session && session.user ? session.user.id : null;
    // A reset-link arrival must always reach the app, even for an already-known user. Token
    // refreshes and tab-focus events change nothing, so those are ignored.
    if (event !== 'PASSWORD_RECOVERY' && uid === (CL.user ? CL.user.id : null)) return;
    // Never await Supabase calls inside this callback (it can deadlock supabase-js); defer them.
    setTimeout(async () => {
      try { await loadMe(); } catch (e) { /* surfaced on the next action */ }
      CL.onChange(event);
    }, 0);
  });

  /* ---------- mapping rows <-> the app's firm shape ---------- */
  const num = (v) => { v = Number(v); return Number.isFinite(v) && v >= 0 && v < 1e8 ? v : 0; };

  // Services are owner-written JSON that every visitor renders, so rebuild them from a whitelist.
  function cleanServices(obj) {
    const out = {};
    if (!obj || typeof obj !== 'object' || Array.isArray(obj)) return out;
    for (const id of CL.serviceIds) {
      const o = obj[id];
      if (!o || typeof o !== 'object') continue;
      const type = ['fixed', 'from', 'hourly'].includes(o.type) ? o.type : 'fixed';
      const s = {
        type,
        time: String(o.time == null ? '' : o.time).slice(0, 80),
        inc: String(o.inc == null ? '' : o.inc).slice(0, 500),
        disb: (Array.isArray(o.disb) ? o.disb : []).slice(0, 12)
          .map((d) => ({ label: String((d && d.label) == null ? '' : d.label).slice(0, 100), amount: num(d && d.amount) }))
          .filter((d) => d.label),
      };
      if (type === 'hourly') s.rate = num(o.rate);
      else if (CL.bandedIds.includes(id)) { s.bands = {}; CL.bandKeys.forEach((k) => { s.bands[k] = num(o.bands && o.bands[k]); }); }
      else s.fee = num(o.fee);
      out[id] = s;
    }
    return out;
  }

  const logoUrl = (path) => path
    ? `${CFG.supabaseUrl}/storage/v1/object/public/logos/${path.split('/').map(encodeURIComponent).join('/')}`
    : null;
  CL.logoUrl = logoUrl;

  function rowToFirm(r, premium) {
    return {
      id: r.id, name: r.name, nation: r.nation, town: r.town || '', address: r.address || '', phone: r.phone || '',
      email: r.email || '', website: r.website || '', regNo: r.reg_no || '', about: r.about || '',
      remote: !!r.remote, vat: r.vat !== false, color: /^#[0-9a-f]{6}$/i.test(r.color || '') ? r.color : '#24507A',
      services: cleanServices(r.services), listed: r.listed === undefined ? true : !!r.listed,
      updated: r.updated_at, premium: !!premium, logoPath: r.logo_path || null,
      // A lapsed firm keeps its stored logo but it is not shown until it re-subscribes.
      logo: premium ? logoUrl(r.logo_path) : null,
    };
  }

  const billingFrom = (row) => {
    const status = (row && row.status) || 'none';
    return {
      status, premium: PREMIUM_STATUSES.includes(status),
      periodEnd: (row && row.current_period_end) || null, cancelAtPeriodEnd: !!(row && row.cancel_at_period_end),
    };
  };

  /* ---------- errors people can act on ---------- */
  function friendly(err, fallback) {
    const msg = (err && err.message) || '';
    const code = err && err.code;
    if (code === '42501' || /row-level security|permission denied/i.test(msg)) return new Error('That is part of the Premium plan.');
    if (code === '23514') return new Error('One of the fields is too long or not valid. Check your entries and try again.');
    if (/invalid login credentials/i.test(msg)) return new Error('That email and password do not match. Check them, or register your firm.');
    if (/email not confirmed/i.test(msg)) return new Error('Please confirm your email first. We sent you a link when you registered.');
    if (/already registered|already been registered/i.test(msg)) return new Error('An account with this email already exists. Sign in instead.');
    if (/password.*(at least|short|weak)/i.test(msg)) return new Error(msg);
    if (/rate limit|too many/i.test(msg)) return new Error('Too many attempts. Please wait a few minutes and try again.');
    if (/network|failed to fetch/i.test(msg)) return new Error('We could not reach the server. Check your connection and try again.');
    return new Error(fallback || 'Something went wrong. Please try again.');
  }

  /* ---------- loading ---------- */
  let publicLoadedAt = 0;
  async function refreshPublic() {
    const { data, error } = await sb.from('public_firms').select('*').order('name').limit(1000);
    if (error) throw error;
    CL.firms = (data || []).map((r) => rowToFirm(r, r.premium));
    publicLoadedAt = Date.now();
  }
  // Re-reads listings at most once a minute while someone browses results.
  CL.maybeRefresh = async function () {
    if (Date.now() - publicLoadedAt < 60000) return false;
    try { await refreshPublic(); return true; } catch (e) { return false; }
  };

  let meLoading = null;
  async function loadMe() {
    if (meLoading) return meLoading;
    meLoading = (async () => {
      const { data: { session } } = await sb.auth.getSession();
      CL.user = session && session.user ? { id: session.user.id, email: session.user.email } : null;
      CL.me = null;
      if (!CL.user) return;
      const [f, b, e] = await Promise.all([
        sb.from('firms').select('*').eq('owner_id', CL.user.id).maybeSingle(),
        // Column list matters: clients are only granted these columns of firm_billing.
        sb.from('firm_billing').select('status,current_period_end,cancel_at_period_end').maybeSingle(),
        sb.from('enquiries').select('*').order('created_at', { ascending: false }).limit(500),
      ]);
      if (f.error) throw f.error;
      const billing = billingFrom(b.data);
      CL.me = { row: f.data, firm: f.data ? rowToFirm(f.data, billing.premium) : null, billing, enquiries: e.data || [] };
    })().finally(() => { meLoading = null; });
    return meLoading;
  }

  CL.init = async function () {
    await Promise.all([refreshPublic(), loadMe()]);
    ready = true;
  };

  /* ---------- accounts ---------- */
  CL.signUp = async function ({ email, password, firmName, nation }) {
    const { data, error } = await sb.auth.signUp({
      email, password,
      options: { data: { firm_name: firmName, nation }, emailRedirectTo: (CFG.siteUrl || location.origin) + '/' },
    });
    if (error) throw friendly(error, 'We could not create your account.');
    // With email-enumeration protection on, an existing address returns a user with no identities.
    if (data.user && Array.isArray(data.user.identities) && data.user.identities.length === 0) {
      throw new Error('An account with this email already exists. Sign in instead.');
    }
    if (data.session) await loadMe();
    return { needsConfirm: !data.session };
  };
  CL.signIn = async function (email, password) {
    const { error } = await sb.auth.signInWithPassword({ email, password });
    if (error) throw friendly(error, 'We could not sign you in.');
    await loadMe();
  };
  CL.signOut = async function () {
    await sb.auth.signOut();
    CL.user = null; CL.me = null; CL.recovery = false;
  };
  CL.resetPassword = async function (email) {
    const { error } = await sb.auth.resetPasswordForEmail(email, { redirectTo: (CFG.siteUrl || location.origin) + '/' });
    if (error && /rate limit|too many/i.test(error.message || '')) throw friendly(error);
    // Otherwise report success whether or not the address exists.
  };
  CL.updatePassword = async function (password) {
    const { error } = await sb.auth.updateUser({ password });
    if (error) throw friendly(error, 'We could not update your password.');
    CL.recovery = false;
  };

  /* ---------- firm profile ---------- */
  CL.saveFirm = async function (f) {
    const patch = {
      name: f.name, nation: f.nation, town: f.town || '', address: f.address || '', phone: f.phone || '',
      email: f.email || '', website: f.website || '', reg_no: f.regNo || '', about: f.about || '',
      remote: !!f.remote, vat: f.vat !== false, services: f.services || {}, listed: !!f.listed,
      logo_path: f.logoPath || null,
    };
    const { data, error } = await sb.from('firms').update(patch).eq('id', f.id).select('*').single();
    if (error) throw friendly(error, 'We could not save your changes.');
    CL.me.row = data;
    CL.me.firm = rowToFirm(data, CL.me.billing.premium);
    try { await refreshPublic(); } catch (e) { /* listings refresh on the next visit */ }
    return CL.me.firm;
  };

  CL.uploadLogo = async function (blob, firmId) {
    if (!CL.me || !CL.me.billing.premium) throw new Error('Logos are part of the Premium plan.');
    const name = 'logo-' + Date.now().toString(36) + '.png';
    const path = firmId + '/' + name;
    const { error } = await sb.storage.from('logos').upload(path, blob, { contentType: 'image/png', cacheControl: '31536000', upsert: false });
    if (error) throw friendly(error, 'We could not upload that logo.');
    return path;
  };
  // Best effort: remove earlier logo files once the new one is saved.
  CL.pruneLogos = async function (firmId, keepPath) {
    try {
      const { data } = await sb.storage.from('logos').list(firmId);
      const old = (data || []).map((o) => firmId + '/' + o.name).filter((p) => p !== keepPath);
      if (old.length) await sb.storage.from('logos').remove(old);
    } catch (e) { /* harmless leftovers */ }
  };

  /* ---------- enquiries ---------- */
  CL.submitEnquiry = async function (payload) {
    let res;
    try {
      res = await fetch(CFG.supabaseUrl + '/functions/v1/submit-enquiry', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', apikey: CFG.supabaseAnonKey },
        body: JSON.stringify(payload),
      });
    } catch (e) { throw friendly(e); }
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.error || 'We could not send your request. Please try again.');
  };
  CL.loadEnquiries = async function () {
    if (!CL.me) return;
    const { data, error } = await sb.from('enquiries').select('*').order('created_at', { ascending: false }).limit(500);
    if (error) throw error;
    CL.me.enquiries = data || [];
  };
  CL.updateEnquiry = async function (id, patch) {
    const { error } = await sb.from('enquiries').update(patch).eq('id', id);
    if (error) throw friendly(error, 'We could not update that enquiry.');
    const row = CL.me.enquiries.find((e) => e.id === id);
    if (row) Object.assign(row, patch);
  };

  /* ---------- billing ---------- */
  async function callFunction(name) {
    const { data: { session } } = await sb.auth.getSession();
    if (!session) throw new Error('Please sign in again.');
    let res;
    try {
      res = await fetch(CFG.supabaseUrl + '/functions/v1/' + name, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', apikey: CFG.supabaseAnonKey, Authorization: 'Bearer ' + session.access_token },
        body: '{}',
      });
    } catch (e) { throw friendly(e); }
    const body = await res.json().catch(() => ({}));
    if (!res.ok) { const err = new Error(body.error || 'Request failed.'); err.code = body.error; throw err; }
    return body;
  }
  function goToStripe(url) {
    let host = '';
    try { host = new URL(url).hostname; } catch (e) { /* handled below */ }
    if (host !== 'stripe.com' && !host.endsWith('.stripe.com')) throw new Error('Unexpected billing address.');
    location.assign(url);
  }
  CL.startCheckout = async function () { goToStripe((await callFunction('create-checkout-session')).url); };
  CL.openBillingPortal = async function () { goToStripe((await callFunction('create-portal-session')).url); };

  CL.refreshBilling = async function () {
    if (!CL.me) return false;
    const [b, f] = await Promise.all([
      sb.from('firm_billing').select('status,current_period_end,cancel_at_period_end').maybeSingle(),
      sb.from('firms').select('*').eq('owner_id', CL.user.id).maybeSingle(),
    ]);
    CL.me.billing = billingFrom(b.data);
    if (f.data) { CL.me.row = f.data; CL.me.firm = rowToFirm(f.data, CL.me.billing.premium); }
    return CL.me.billing.premium;
  };
  // After Stripe sends the firm back, the webhook may land a few seconds later.
  CL.waitForPremium = async function (tries, everyMs) {
    tries = tries || 10; everyMs = everyMs || 2000;
    for (let i = 0; i < tries; i++) {
      try { if (await CL.refreshBilling()) { try { await refreshPublic(); } catch (e) {} return true; } } catch (e) {}
      await new Promise((r) => setTimeout(r, everyMs));
    }
    return false;
  };
})();
