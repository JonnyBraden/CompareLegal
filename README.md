# Compare Legal

Price comparison for UK high-street legal services (conveyancing, wills & attorneys, probate & estates, family) at comparelegal.com.

## Structure
- `src/app.html` — the whole app (markup, styles, script). Edit this.
- `build.py` — wraps `src/app.html` into `public/index.html` for static hosting.
- `public/vendor/` — bundled supabase-js (no third-party CDN at runtime).
- `render.yaml` — Render static site config (comparelegal.com and www).
- `content/posts/*.md` — blog guides (Markdown with a short front-matter header). Add a file, push, and it's live.
- `site/sitegen.py` — builds the static SEO pages at deploy time: `/blog/`, `/blog/<slug>/`, category pages, an RSS feed,
  `/<service>/`, `/<service>/<nation>/`, `/<service>/<nation>/<city>/` for every city in the app, `/solicitors/`, `sitemap.xml` and `robots.txt`.
- `site/site.css` — styles for those pages.
- `/firms/<name>-<id6>/` — a profile page per live firm (built from Supabase). The portal's badge code links here; the slug rule is in both `site/sitegen.py` (`firm_slug`) and `src/app.html` (`firmSlug`), keep them in step.
- `public/fonts/` — self-hosted fonts (no Google Fonts requests). `public/badge.svg` — the firm badge.

## Blog and local pages
- Services: `conveyancing`, `wills`, `probate`, `family-law`. Nations: `england`, `wales`, `scotland`, `northern-ireland`.
- Live, non-sample firms are read from Supabase at build time and listed on the nearest city pages (within 25 miles).
- A city page is `noindex` and left out of the sitemap until at least 3 real firms are within 25 miles
  (`LOCAL_INDEX_MIN_FIRMS` in `site/sitegen.py`). This stops Google seeing hundreds of near-empty doorway pages.
- `.github/workflows/rebuild.yml` triggers a nightly Render deploy so new firms show up; it needs the `RENDER_DEPLOY_HOOK` secret.
- Post front matter: `title`, `short` (breadcrumb), `description`, `date`, `updated`, `category` (a service slug),
  `nations` (list), `service` (app service id for the call to action). A `## Frequently asked questions` section with `###` questions
  becomes FAQ structured data automatically. Set `draft: true` to hold a post back.
- Deep links into the app: `/#results?svc=purchase&nation=Northern%20Ireland&city=Belfast`.

## Backend (Supabase project `comparelegal`, ref dtsxrmmdnyjmplhoyinv, London)
- `firms` — one row per firm account (created automatically on sign-up from the firm name and nation), plus 18 seeded sample firms (`sample = true`, cannot receive enquiries).
- `enquiries` — quote requests; anyone can create one for a live, non-sample firm; only that firm can read them.
- Storage bucket `logos` — public read; each account can only write inside its own folder.
- Row-level security on everything; the browser only uses the publishable key.

## To do before scaling up
- Custom SMTP in Supabase Auth (e.g. Resend) so confirmation and password-reset emails reach any address.
- Privacy notice and terms for consumers and firms.
- Remove sample firms once enough real firms have listed: `delete from firms where sample;`

## Enquiry emails
- `supabase/functions/notify-enquiry/` emails the firm (or the account owner if the firm has no enquiries email) through Resend, with the client as reply-to. Each enquiry is emailed once (`enquiries.emailed_at`), only for live non-sample firms, and at most 40 an hour per firm.
- `supabase/migrations/20261113150000_enquiry_emails.sql` adds `emailed_at` and a trigger that calls the function after each insert (pg_net).
- Function secrets: `RESEND_API_KEY`, `MAIL_FROM`. Deploy with JWT verification off. Without the secrets, the function does nothing and enquiries still appear in the portal.
- Tests: `node --experimental-strip-types --test tests/notify-enquiry.test.ts`
