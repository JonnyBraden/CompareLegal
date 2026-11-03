// A tiny in-memory stand-in for supabase-js, covering only the calls the Edge Functions make.
// It is shared state: tests reset it with resetDb() and inspect `db` afterwards.

type Row = Record<string, any>;

export const db: {
  firms: Row[];
  firm_billing: Row[];
  enquiries: Row[];
  tokens: Record<string, { id: string; email: string }>;
  failInsert: boolean;
} = { firms: [], firm_billing: [], enquiries: [], tokens: {}, failInsert: false };

export function resetDb() {
  db.firms = []; db.firm_billing = []; db.enquiries = []; db.tokens = {}; db.failInsert = false;
}

let seq = 0;

class Query {
  private op: 'select' | 'insert' | 'upsert' | 'update' = 'select';
  private payload: any = null;
  private opts: any = {};
  private filters: Array<(r: Row) => boolean> = [];
  private head = false;
  private wantCount = false;
  private returning = false;
  private table: Row[];
  // (No constructor parameter properties: Node's type stripping only supports erasable syntax.)
  constructor(table: Row[]) { this.table = table; }

  select(_cols?: string, o: { head?: boolean; count?: string } = {}) {
    if (this.op === 'select') { this.head = !!o.head; this.wantCount = !!o.count; }
    else this.returning = true;
    return this;
  }
  insert(p: Row) { this.op = 'insert'; this.payload = p; return this; }
  upsert(p: Row, o: any = {}) { this.op = 'upsert'; this.payload = p; this.opts = o; return this; }
  update(p: Row) { this.op = 'update'; this.payload = p; return this; }
  eq(c: string, v: any) { this.filters.push((r) => r[c] === v); return this; }
  gte(c: string, v: any) { this.filters.push((r) => r[c] >= v); return this; }

  private run(): { data: any; error: any; count?: number } {
    const match = () => this.table.filter((r) => this.filters.every((f) => f(r)));
    if (this.op === 'select') {
      const rows = match();
      return { data: this.head ? null : rows, error: null, count: this.wantCount ? rows.length : undefined };
    }
    if (this.op === 'insert') {
      if (db.failInsert) return { data: null, error: { message: 'insert failed' } };
      const row = { id: `row-${++seq}`, created_at: new Date().toISOString(), ...this.payload };
      this.table.push(row);
      return { data: this.returning ? [row] : null, error: null };
    }
    if (this.op === 'upsert') {
      const key = this.opts.onConflict as string;
      const existing = this.table.find((r) => r[key] === this.payload[key]);
      if (existing) { if (!this.opts.ignoreDuplicates) Object.assign(existing, this.payload); }
      else this.table.push({ ...this.payload });
      return { data: null, error: null };
    }
    const rows = match();
    rows.forEach((r) => Object.assign(r, this.payload));
    return { data: null, error: null };
  }

  maybeSingle() { const r = this.run(); return Promise.resolve({ data: Array.isArray(r.data) ? r.data[0] ?? null : r.data, error: r.error }); }
  single() {
    const r = this.run();
    const data = Array.isArray(r.data) ? r.data[0] ?? null : r.data;
    return Promise.resolve({ data, error: r.error ?? (data ? null : { message: 'no rows' }) });
  }
  then(resolve: (v: any) => any, reject?: (e: any) => any) { return Promise.resolve(this.run()).then(resolve, reject); }
}

export function createClient(_url: string, _key: string, options: any = {}) {
  const authHeader: string = options?.global?.headers?.Authorization ?? '';
  return {
    from(name: 'firms' | 'firm_billing' | 'enquiries') { return new Query(db[name]); },
    auth: {
      async getUser() {
        const token = authHeader.replace(/^Bearer\s+/i, '');
        const user = db.tokens[token];
        return { data: { user: user ?? null }, error: user ? null : { message: 'invalid token' } };
      },
    },
  };
}
