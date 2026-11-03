# Compare Legal

[comparelegal.com](https://comparelegal.com): compare UK high-street legal fees (conveyancing, wills and powers of attorney, probate and estates, family). Law firms sign themselves up, publish their prices for free, and can upgrade to a monthly **Premium** plan.

## How it works

| | Free | Premium (monthly) |
|---|---|---|
| Listing with prices and contact details | ✓ | ✓ |
| Every client enquiry emailed to the firm straight away | ✓ | ✓ |
| Enquiries kept in the firm's portal | ✓ | ✓ |
| Logo on the listing and in comparisons | | ✓ |
| "Premium listing" label | | ✓ |
| Status and private notes on each enquiry | | ✓ |
| CSV export of enquiries | | ✓ |

Results are always ranked by price. Paying never changes where a firm appears, and the results page says so. Every enquiry is delivered to every firm, so no client is left waiting because a firm is on the free plan.

**Architecture**

- **Site:** static HTML/JS on Render (`src/app.html` + `src/backend.js`, built by `build.py` into `public/`).
- **Accounts, data, logos:** Supabase (Auth, Postgres with row-level security, Storage).
- **Billing:** Stripe Checkout and the Stripe customer portal, driven by Supabase Edge Functions.
- **Enquiry email:** Resend, sent from the `submit-enquiry` Edge Function.

The browser only ever holds the public Supabase key. Everything that matters is enforced on the server: a firm can only read and edit its own rows, **Premium status can only be written by the signature-verified Stripe webhook**, and Premium-only actions (logo upload, enquiry tracking) are blocked by the database itself, not just hidden in the UI.

Without Supabase keys the site builds as the original browser-only prototype (data in `localStorage`, fictional sample firms), so it always runs.

```
src/app.html            the whole app UI (vanilla JS)
src/backend.js          data layer: Supabase auth/data/storage, calls the Edge Functions
build.py                builds public/index.html and public/config.js from environment variables
supabase/migrations/    database schema, row-level security, storage rules
supabase/functions/     create-checkout-session, create-portal-session, stripe-webhook, submit-enquiry
tests/                  SQL (RLS), unit (Edge Functions) and browser (UI) tests
```

## Setting it up

Do this in Stripe **test mode** first, and switch to live keys at the end.

### 1. Supabase

1. Create a project at [supabase.com](https://supabase.com). Note the project URL, the **anon (public)** key and the project ref.
2. Install the [Supabase CLI](https://supabase.com/docs/guides/local-development), then from this folder:
   ```
   supabase login
   supabase link --project-ref <your-project-ref>
   supabase db push
   ```
   This creates the tables, security rules, the `public_firms` listing view and the `logos` storage bucket.
3. In the dashboard under **Authentication**:
   - **URL Configuration:** set *Site URL* to `https://comparelegal.com`, and add `https://www.comparelegal.com` to the redirect URLs.
   - **Sign In / Providers → Email:** keep *Confirm email* on and set the minimum password length to 8.
   - **SMTP Settings:** add a custom SMTP provider (Resend works well). Supabase's built-in email sender is limited to a handful of messages per hour, which will block real sign-ups.

### 2. Stripe

1. **Products → Add product:** "Compare Legal Premium", recurring, monthly, GBP. Copy the **price ID** (`price_...`).
2. **Settings → Billing → Customer portal:** turn on *update payment method*, *view invoices* and *cancel subscriptions*, then save. The "Manage billing" button needs this to be saved.
3. **Developers → Webhooks → Add endpoint:**
   - URL: `https://<your-project-ref>.supabase.co/functions/v1/stripe-webhook`
   - Events: `checkout.session.completed`, `customer.subscription.created`, `customer.subscription.updated`, `customer.subscription.deleted`, `customer.subscription.paused`, `customer.subscription.resumed`
   - Copy the **signing secret** (`whsec_...`).

### 3. Edge Function secrets and deploy

```
cp supabase/functions/.env.example supabase/functions/.env     # then fill it in
supabase secrets set --env-file supabase/functions/.env
supabase functions deploy
```

`config.toml` already sets `verify_jwt = false` for `stripe-webhook` and `submit-enquiry` (they do their own verification and rate-limiting). Enquiry emails need `RESEND_API_KEY` and `MAIL_FROM` on a domain you have verified in Resend. Without them enquiries are still saved and appear in the firm's portal, but firms are **not** emailed, so set this up before launch.

### 4. Render

In the `comparelegal` service's **Environment** tab, set:

| Variable | Value |
|---|---|
| `SUPABASE_URL` | `https://<your-project-ref>.supabase.co` |
| `SUPABASE_ANON_KEY` | the **anon / publishable** key (public). The build refuses a service-role or secret key. |
| `PREMIUM_PRICE_LABEL` | the price text shown to firms, e.g. `£49 per month`. **It must match the Stripe price.** |
| `SITE_URL` | `https://comparelegal.com` |
| `SHOW_SAMPLE_FIRMS` | `false` (set `true` only if you want the fictional sample firms alongside real ones) |

Redeploy. `render.yaml` also sets security headers, including a Content-Security-Policy that allows `https://*.supabase.co`. If you give Supabase a custom domain, add it to `connect-src` and `img-src` there.

### 5. Check it end to end (test mode)

1. Register a firm, confirm the email, add details and a price, publish.
2. Search for that service and request a quote. Check the firm's inbox.
3. In the portal, **Plan → Upgrade**, pay with Stripe's test card `4242 4242 4242 4242`. You should come back to the portal with Premium switched on.
4. Check the webhook shows successful deliveries in Stripe, then switch the Stripe keys, price ID and webhook secret to live mode.

## Running the tests

```
npm install
npm test            # SQL + unit + browser tests
```

| Suite | What it covers | Needs |
|---|---|---|
| `npm run test:sql` | The migration and every row-level-security rule on a throwaway Postgres: ownership, public view, Premium gating, Stripe-only billing writes, storage rules | PostgreSQL server binaries |
| `npm run test:unit` | The Edge Functions end to end with Stripe, Resend and the database faked: webhook signature checks, out-of-order events, checkout, enquiry validation, rate limits | Node 22.18+ |
| `npm run test:ui` | The whole site in Chrome against a fake backend, under the production CSP: sign-up, publishing, enquiries, upgrade, logo, enquiry tracking, lapse, password reset | Chrome or Chromium |

These tests run against fakes, so they prove the logic, not your live accounts. Do the manual check above once everything is connected.

## Running things

- **Suspend a firm** (it disappears from results at once):
  `update public.firms set suspended = true where id = '<firm id>';`
- **Give a firm Premium without Stripe** (e.g. a founding partner):
  `insert into public.firm_billing (firm_id, status) values ('<firm id>', 'active') on conflict (firm_id) do update set status = 'active';`
- **Premium rules:** `active`, `trialing` and `past_due` count as Premium. `past_due` is a grace period while Stripe retries the card.
- **If a firm lapses,** its logo is hidden but kept, and returns if they resubscribe.

## Before you launch

Things this repo does not do for you:

- **Terms and a privacy policy.** Quote requests pass clients' names and contact details to firms, and firm accounts hold personal data. The site has no policy pages yet. Add them, link them from the footer, and check your ICO registration.
- **VAT on the Premium fee.** The price label is plain text and Stripe Tax is not switched on. Decide whether the fee is VAT-inclusive or exclusive and configure Stripe to match.
- **Checking firms are who they say they are.** Sign-up is instant and firms enter their own regulator number, with a tick-box confirming they are regulated and authorised. Consider verifying numbers against the SRA, Law Society of Scotland and Law Society of Northern Ireland registers, and decide how you will handle complaints about listings.
- **Rules on comparison sites and referral fees.** Keep the price-first ranking, and keep any future paid placement clearly labelled, in line with regulator and CMA guidance.

## Known limits

- Results load every listed firm (up to 1,000) in one request. Beyond that, move filtering and sorting into the database.
- One login per firm. Multi-user firm accounts would need a members table.
- Enquiries are emailed once; there is no reminder or auto-reply to the client.
