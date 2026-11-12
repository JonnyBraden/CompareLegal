# Compare Legal

Price comparison for UK high-street legal services (conveyancing, wills & attorneys, probate & estates, family) at comparelegal.com.

## Structure
- `src/app.html` — the whole app (markup, styles, script). Edit this.
- `build.py` — wraps `src/app.html` into `public/index.html` for static hosting.
- `public/vendor/` — bundled supabase-js (no third-party CDN at runtime).
- `render.yaml` — Render static site config (comparelegal.com and www).

## Backend (Supabase project `comparelegal`, ref dtsxrmmdnyjmplhoyinv, London)
- `firms` — one row per firm account (created automatically on sign-up from the firm name and nation), plus 18 seeded sample firms (`sample = true`, cannot receive enquiries).
- `enquiries` — quote requests; anyone can create one for a live, non-sample firm; only that firm can read them.
- Storage bucket `logos` — public read; each account can only write inside its own folder.
- Row-level security on everything; the browser only uses the publishable key.

## To do before scaling up
- Custom SMTP in Supabase Auth (e.g. Resend) so confirmation and password-reset emails reach any address.
- Email firms when an enquiry arrives (database webhook + edge function).
- Privacy notice and terms for consumers and firms.
- Remove sample firms once enough real firms have listed: `delete from firms where sample;`
