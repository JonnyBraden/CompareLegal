# Redirects to set in Render

Compare Legal is a Render static site. Redirects are configured in the dashboard (the service, then **Redirects/Rewrites**), not from a file in this repo. Every page has a self-referencing canonical with a trailing slash. Add the rules below so other URLs send people to that canonical.

Render does not apply a rule when a file already exists at the source path. Files such as `/sitemap.xml`, `/robots.txt`, `/llms.txt`, `/og.png`, `/favicon.ico` and the IndexNow key are therefore left as they are.

Set each rule's action to **Redirect** (301). Put the `/index.html` rules above the slashless rules.

## `/index.html` to `/`

| Source | Destination |
| --- | --- |
| `/index.html` | `/` |
| `/*/index.html` | `/*/` |

`/index.html` is a real file, so Render may serve it instead of redirecting. The canonical on the homepage is still `https://comparelegal.com/`. Nested pages are published as `index.html` inside their directory, and the public URL for each of them ends with a slash.

## Slashless paths to the trailing-slash URL

These rules cover every directory page: one segment (`/blog`, `/about`, `/conveyancing`), two (`/blog/your-guide`, `/conveyancing/england`, `/firms/example`), three (`/blog/category/conveyancing`, `/conveyancing/england/leeds`) and four. A path that already has a file, including anything with an extension, is not redirected.

| Source | Destination |
| --- | --- |
| `/:a` | `/:a/` |
| `/:a/:b` | `/:a/:b/` |
| `/:a/:b/:c` | `/:a/:b/:c/` |
| `/:a/:b/:c/:d` | `/:a/:b/:c/:d/` |

Do not add a catch-all `/*` to `/*/` rule. It would also match paths that already end in a slash.
