/* In-browser stand-in for supabase-js, used only by the UI tests. It keeps its "database" in
   localStorage so state survives page reloads, and mimics the server-side rules the real database
   enforces (own rows only, Premium-gated logo and enquiry tracking, public view hides owner ids). */
(function () {
  const KEY = 'cl-fake-db';
  const subscribers = [];
  let initialised;

  function seed() {
    const svc = (fee) => ({
      purchase: { type: 'fixed', bands: { b1: fee, b2: fee + 100, b3: fee + 200, b4: fee + 300 }, disb: [{ label: 'Searches', amount: 300 }], time: '8-12 weeks', inc: 'Full service' },
      will1: { type: 'fixed', fee: fee / 4, disb: [], time: '1-2 weeks', inc: 'Drafting and signing' },
    });
    const firm = (id, name, over) => Object.assign({
      id, owner_id: 'seed-' + id, name, nation: 'England', town: 'Bristol', address: '1 High Street', phone: '0117 496 0000',
      email: 'hello@' + id + '.example', website: '', reg_no: 'SRA-' + id, about: 'A high-street practice.', remote: true, vat: true,
      color: '#2F5D50', logo_path: null, services: svc(900), listed: true, suspended: false, updated_at: '2026-10-01T10:00:00Z',
    }, over);
    return {
      users: [], session: null, flags: { requireConfirm: true, recovery: false }, resets: [], files: [], enquiries: [],
      firms: [
        firm('harbour-law', 'Harbour Law', { logo_path: '11111111-1111-1111-1111-111111111111/logo-seed.png', services: svc(1200) }),
        firm('quayside', 'Quayside Solicitors', { services: svc(800) }),
        // Hostile content: must render as inert text and never run.
        firm('evil', 'Evil <img src=x onerror="window.__xss=1"> Ltd', {
          about: '<script>window.__xss=2</script>', color: 'red;background:url(javascript:window.__xss=3)',
          services: Object.assign({ '<b>bogus</b>': { type: 'fixed', fee: 1 } }, svc(1500), { purchase: { type: 'fixed', bands: { b1: '<i>5</i>', b2: 1, b3: 1, b4: 1 }, disb: [{ label: '<u>x</u>', amount: 'NaN' }] } }),
        }),
      ],
      billing: [{ firm_id: 'harbour-law', status: 'active', current_period_end: '2027-01-01T00:00:00Z', cancel_at_period_end: false }],
    };
  }
  const load = () => { try { const v = JSON.parse(localStorage.getItem(KEY)); if (v) return v; } catch (e) {} const d = seed(); save(d); return d; };
  const save = (d) => localStorage.setItem(KEY, JSON.stringify(d));
  const mutate = (fn) => { const d = load(); const r = fn(d); save(d); return r; };

  const PREMIUM = ['active', 'trialing', 'past_due'];
  const billingOf = (d, firmId) => d.billing.find((b) => b.firm_id === firmId);
  const isPremium = (d, firmId) => { const b = billingOf(d, firmId); return !!b && PREMIUM.includes(b.status); };
  const sessionOf = (d) => {
    if (!d.session) return null;
    const u = d.users.find((x) => x.id === d.session);
    return u ? { user: { id: u.id, email: u.email }, access_token: 'fake-token-' + u.id } : null;
  };
  const myFirm = (d) => { const s = d.session; return s ? d.firms.find((f) => f.owner_id === s) : null; };
  const emit = (event) => { const s = sessionOf(load()); subscribers.forEach((cb) => setTimeout(() => cb(event, s), 0)); };
  const err = (message, code) => ({ message, code });

  class Query {
    constructor(table) { this.table = table; this.op = 'select'; this.filters = []; this.orderBy = null; this.max = Infinity; this.patch = null; }
    select() { return this; }
    update(p) { this.op = 'update'; this.patch = p; return this; }
    eq(c, v) { this.filters.push((r) => r[c] === v); return this; }
    order(col, o) { this.orderBy = [col, !(o && o.ascending === false)]; return this; }
    limit(n) { this.max = n; return this; }
    run() {
      const d = load();
      const me = myFirm(d);
      let rows;
      if (this.table === 'public_firms') {
        rows = d.firms.filter((f) => f.listed && !f.suspended).map((f) => {
          const prem = isPremium(d, f.id); const { owner_id, suspended, listed, ...rest } = f;
          return Object.assign({}, rest, { premium: prem, logo_path: prem ? f.logo_path : null });
        });
      } else if (this.table === 'firms') rows = me ? [me] : [];
      else if (this.table === 'firm_billing') rows = me && billingOf(d, me.id) ? [billingOf(d, me.id)] : [];
      else if (this.table === 'enquiries') rows = me ? d.enquiries.filter((e) => e.firm_id === me.id) : [];
      else return { data: null, error: err('unknown table ' + this.table) };
      rows = rows.filter((r) => this.filters.every((f) => f(r)));

      if (this.op === 'update') {
        if (!rows.length) return { data: null, error: null };
        const target = d[this.table === 'firms' ? 'firms' : 'enquiries'].find((r) => r.id === rows[0].id);
        if (this.table === 'firms') {
          if (this.patch.logo_path && this.patch.logo_path !== target.logo_path && !isPremium(d, target.id)) return { data: null, error: err('Logo upload is a Premium feature', '42501') };
          if ((this.patch.about || '').length > 300) return { data: null, error: err('check constraint', '23514') };
          Object.assign(target, this.patch, { updated_at: new Date().toISOString() });
        } else {
          if (!isPremium(d, target.firm_id)) return { data: null, error: err('Enquiry tracking is a Premium feature', '42501') };
          Object.assign(target, this.patch);
        }
        save(d);
        return { data: [target], error: null };
      }
      if (this.orderBy) { const [c, asc] = this.orderBy; rows = rows.slice().sort((a, b) => (a[c] > b[c] ? 1 : a[c] < b[c] ? -1 : 0) * (asc ? 1 : -1)); }
      return { data: rows.slice(0, this.max).map((r) => JSON.parse(JSON.stringify(r))), error: null };
    }
    async maybeSingle() { await initialised; const r = this.run(); return { data: r.data && r.data[0] || null, error: r.error }; }
    async single() { await initialised; const r = this.run(); const row = r.data && r.data[0]; return { data: row || null, error: r.error || (row ? null : err('no rows')) }; }
    then(res, rej) { return initialised.then(() => this.run()).then(res, rej); }
  }

  function createClient() {
    // Like supabase-js, finish initialising asynchronously. A password-recovery arrival is announced
    // to subscribers *before* initialisation resolves, which is why the app must subscribe at load.
    initialised = new Promise((resolve) => setTimeout(() => {
      const s = sessionOf(load());
      if (load().flags.recovery && s) subscribers.forEach((cb) => cb('PASSWORD_RECOVERY', s));
      resolve();
    }, 20));
    return {
      auth: {
        onAuthStateChange(cb) { subscribers.push(cb); return { data: { subscription: { unsubscribe() {} } } }; },
        async getSession() { await initialised; return { data: { session: sessionOf(load()) }, error: null }; },
        async signUp({ email, password, options }) {
          const r = mutate((d) => {
            if (d.users.some((u) => u.email === email)) return { dup: true };
            const meta = (options && options.data) || {};
            const u = { id: 'user-' + (d.users.length + 1) + '-' + Math.random().toString(16).slice(2, 10), email, password, confirmed: !d.flags.requireConfirm };
            d.users.push(u);
            // What the handle_new_user trigger does:
            d.firms.push({ id: 'firm-' + u.id, owner_id: u.id, name: meta.firm_name || email.split('@')[0], nation: meta.nation || 'England', town: '', address: '', phone: '', email,
              website: '', reg_no: '', about: '', remote: false, vat: true, color: '#24507A', logo_path: null, services: {}, listed: false, suspended: false, updated_at: new Date().toISOString() });
            if (u.confirmed) d.session = u.id;
            return { u };
          });
          if (r.dup) return { data: { user: { identities: [] }, session: null }, error: null };
          if (r.u.confirmed) emit('SIGNED_IN');
          return { data: { user: { id: r.u.id, identities: [{}] }, session: r.u.confirmed ? sessionOf(load()) : null }, error: null };
        },
        async signInWithPassword({ email, password }) {
          const r = mutate((d) => {
            const u = d.users.find((x) => x.email === email && x.password === password);
            if (!u) return { error: err('Invalid login credentials') };
            if (!u.confirmed) return { error: err('Email not confirmed') };
            d.session = u.id; return {};
          });
          if (r.error) return { data: {}, error: r.error };
          emit('SIGNED_IN');
          return { data: { session: sessionOf(load()) }, error: null };
        },
        async signOut() { mutate((d) => { d.session = null; }); emit('SIGNED_OUT'); return { error: null }; },
        async resetPasswordForEmail(email) { mutate((d) => { d.resets.push(email); }); return { data: {}, error: null }; },
        async updateUser({ password }) {
          const r = mutate((d) => { const u = d.users.find((x) => x.id === d.session); if (!u) return { error: err('Auth session missing') }; u.password = password; d.flags.recovery = false; return {}; });
          return { data: {}, error: r.error || null };
        },
      },
      from(table) { return new Query(table); },
      storage: {
        from() {
          return {
            async upload(path) {
              const d = load(); const me = myFirm(d);
              if (!me || !path.startsWith(me.id + '/') || !isPremium(d, me.id)) return { data: null, error: err('new row violates row-level security policy') };
              mutate((x) => { x.files.push(path); }); return { data: { path }, error: null };
            },
            async list(folder) { return { data: load().files.filter((p) => p.startsWith(folder + '/')).map((p) => ({ name: p.split('/')[1] })), error: null }; },
            async remove(paths) { mutate((d) => { d.files = d.files.filter((p) => !paths.includes(p)); }); return { data: [], error: null }; },
          };
        },
      },
    };
  }

  window.supabase = { createClient };
  // Hooks for the test runner (simulating what the database, Stripe webhook and Edge Functions do).
  window.__fake = {
    db: () => load(),
    setBilling: (firmId, status, extra) => mutate((d) => {
      const row = Object.assign({ firm_id: firmId, status, current_period_end: '2027-01-01T00:00:00Z', cancel_at_period_end: false }, extra || {});
      const i = d.billing.findIndex((b) => b.firm_id === firmId); if (i >= 0) d.billing[i] = row; else d.billing.push(row);
    }),
    confirm: (email) => mutate((d) => { d.users.find((u) => u.email === email).confirmed = true; }),
    addEnquiry: (row) => mutate((d) => { d.enquiries.unshift(Object.assign({ id: 'enq-' + (d.enquiries.length + 1), status: 'new', note: '', phone: '', message: '', nation: 'England', property_price: null, quoted_total: 195, created_at: new Date().toISOString() }, row)); }),
    setFlag: (k, v) => mutate((d) => { d.flags[k] = v; }),
    reset: () => localStorage.removeItem(KEY),
  };
})();
