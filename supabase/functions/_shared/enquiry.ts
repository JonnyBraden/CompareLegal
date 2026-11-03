// Validation and email composition for client quote requests. Pure functions (no Deno APIs) so they
// can be unit-tested in Node.

export const SERVICE_IDS = [
  'purchase', 'sale', 'remortgage', 'will1', 'will2', 'poa',
  'grant', 'estate', 'divorce', 'finances', 'children',
] as const;

const SERVICE_LABELS: Record<string, string> = {
  purchase: 'Buying a home', sale: 'Selling a home', remortgage: 'Remortgage',
  will1: 'Single will', will2: 'Mirror wills for a couple', poa: 'Power of attorney',
  grant: 'Grant of probate / confirmation only', estate: 'Full estate administration',
  divorce: 'Uncontested divorce', finances: 'Financial consent order / minute of agreement',
  children: 'Child arrangements: first consultation',
};
export const serviceLabel = (id: string) => SERVICE_LABELS[id] ?? id;

export type EnquiryInput = {
  firmId: string;
  service: string;
  nation: string | null;
  price: number | null;
  total: number | null;
  name: string;
  email: string;
  phone: string;
  message: string;
};

export type Validation = { ok: true; value: EnquiryInput } | { ok: false; error: string };

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const EMAIL = /^[^\s@<>()",;:]+@[^\s@<>()",;:]+\.[^\s@<>()",;:]{2,}$/;
const NATIONS = ['England', 'Wales', 'Scotland', 'Northern Ireland'];

const str = (v: unknown, max: number) => (typeof v === 'string' ? v.trim().slice(0, max) : '');
const num = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) && v >= 0 && v < 1e10 ? v : null);

export function validateEnquiry(raw: any): Validation {
  if (!raw || typeof raw !== 'object') return { ok: false, error: 'Invalid request.' };
  const firmId = str(raw.firmId, 36);
  if (!UUID.test(firmId)) return { ok: false, error: 'Unknown firm.' };
  const service = str(raw.service, 40);
  if (!(SERVICE_IDS as readonly string[]).includes(service)) return { ok: false, error: 'Unknown service.' };
  const name = str(raw.name, 120);
  if (!name) return { ok: false, error: 'Please enter your name.' };
  const email = str(raw.email, 200);
  if (!EMAIL.test(email)) return { ok: false, error: 'Please enter a valid email address.' };
  const nation = str(raw.nation, 40);
  return {
    ok: true,
    value: {
      firmId, service, name, email,
      nation: NATIONS.includes(nation) ? nation : null,
      price: num(raw.price),
      total: num(raw.total),
      phone: str(raw.phone, 40),
      message: str(raw.message, 2000),
    },
  };
}

const gbp = (n: number) => '£' + n.toLocaleString('en-GB', { maximumFractionDigits: 2 });

/** Plain-text email only: client-supplied text is never interpreted as HTML. */
export function composeFirmEmail(firmName: string, e: EnquiryInput): { subject: string; text: string } {
  const lines = [
    `${firmName}, you have a new quote request from Compare Legal.`,
    ``,
    `Service: ${serviceLabel(e.service)}${e.nation ? ` (${e.nation})` : ''}`,
  ];
  if (e.price != null) lines.push(`Property value: ${gbp(e.price)}`);
  if (e.total != null) lines.push(`Total the client saw for your firm: ${gbp(e.total)} including VAT and disbursements`);
  lines.push(``, `Name: ${e.name}`, `Email: ${e.email}`);
  if (e.phone) lines.push(`Phone: ${e.phone}`);
  if (e.message) lines.push(``, `Message:`, e.message);
  lines.push(
    ``,
    `Reply to this email to reach the client directly. They asked for a written quote, so a reply within one working day is ideal.`,
    ``,
    `You can also see and manage this enquiry in your Compare Legal portal: https://comparelegal.com/#portal`,
  );
  return { subject: `New quote request: ${serviceLabel(e.service)}`, text: lines.join('\n') };
}
