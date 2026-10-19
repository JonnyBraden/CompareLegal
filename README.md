# Compare Legal

Prototype of comparelegal.com — a price comparison site for UK high-street legal services (conveyancing, wills and powers of attorney, probate and estates, family).

- `src/app.html` — the whole app (HTML, CSS, vanilla JS). Also published as a Claude artifact preview.
- `build.py` — wraps `src/app.html` into `public/index.html` for static hosting.
- `render.yaml` — Render static site blueprint with the comparelegal.com domains.

## Prototype limits
- Sample firms and prices are fictional and clearly labelled.
- Firm accounts, prices, logos and quote requests are stored in the visitor's browser (localStorage).

## Next step to production
Move firms, prices, logos (storage) and enquiries into a database with real authentication (e.g. Supabase Auth + Postgres + Storage), add email delivery for quote requests, and firm verification against regulator records.
