// Composes the email a firm receives for a new quote request. Pure functions, no Deno APIs,
// so they can be tested in Node. Plain text only: client-supplied text is never treated as HTML.

const SERVICE_LABELS: Record<string, string> = {
  purchase: 'Buying a home', sale: 'Selling a home', remortgage: 'Remortgage',
  will1: 'Single will', will2: 'Mirror wills for a couple', poa: 'Power of attorney',
  grant: 'Grant of probate / confirmation only', estate: 'Full estate administration',
  divorce: 'Uncontested divorce', finances: 'Financial consent order / minute of agreement',
  children: 'Child arrangements: first consultation',
};
export const serviceLabel = (id: string): string => SERVICE_LABELS[id] ?? id;

export type Enquiry = {
  id: string; firm_id: string; svc: string; nation: string | null; price: number | null; total: number | null;
  name: string; email: string; phone: string | null; msg: string | null; created_at?: string;
};

const gbp = (n: number): string => '£' + Number(n).toLocaleString('en-GB', { maximumFractionDigits: 2 });
const clean = (s: unknown, max: number): string => String(s ?? '').replace(/[\r\n]+/g, ' ').trim().slice(0, max);
const EMAIL = /^[^\s@<>()",;:]+@[^\s@<>()",;:]+\.[^\s@<>()",;:]{2,}$/;
export const isEmail = (s: unknown): boolean => typeof s === 'string' && EMAIL.test(s.trim());

export function composeFirmEmail(firmName: string, e: Enquiry): { subject: string; text: string } {
  const lines = [
    `${firmName}, you have a new quote request from Compare Legal.`,
    ``,
    `Service: ${serviceLabel(e.svc)}${e.nation ? ` (${clean(e.nation, 40)})` : ''}`,
  ];
  if (e.price != null && Number(e.price) > 0) lines.push(`Property value: ${gbp(e.price)}`);
  if (e.total != null && Number(e.total) > 0) lines.push(`Total the client saw for your firm: ${gbp(e.total)} including VAT and disbursements`);
  lines.push(``, `Name: ${clean(e.name, 120)}`, `Email: ${clean(e.email, 200)}`);
  if (e.phone) lines.push(`Phone: ${clean(e.phone, 40)}`);
  if (e.msg) lines.push(``, `Message:`, String(e.msg).slice(0, 2000));
  lines.push(
    ``,
    `Reply to this email to reach the client directly. They asked for a written quote, so a reply within one working day is ideal.`,
    ``,
    `You can also see this enquiry in your Compare Legal portal: https://comparelegal.com/#portal`,
    ``,
    `--`,
    `You're receiving this because your firm is listed on comparelegal.com. To stop receiving requests, hide your listing in the portal.`,
  );
  return { subject: `New quote request: ${serviceLabel(e.svc)}`, text: lines.join('\n') };
}
