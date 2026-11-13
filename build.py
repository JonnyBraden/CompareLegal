"""Build the site for static hosting.

1. Wrap src/app.html (the app) into public/index.html.
2. Generate the blog and local fee pages (site/sitegen.py) as plain HTML for search engines.
"""
import pathlib
import sys

src = pathlib.Path('src/app.html').read_text()
head_end = src.index('</style>') + len('</style>')
head, body = src[:head_end], src[head_end:]
ld = '''<script type="application/ld+json">{"@context":"https://schema.org","@graph":[{"@type":"Organization","@id":"https://comparelegal.com/#org","name":"Compare Legal","url":"https://comparelegal.com/","areaServed":"GB"},{"@type":"WebSite","@id":"https://comparelegal.com/#site","name":"Compare Legal","url":"https://comparelegal.com/","inLanguage":"en-GB","publisher":{"@id":"https://comparelegal.com/#org"}}]}</script>'''
doc = f"""<!doctype html>
<html lang="en-GB">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<link rel="canonical" href="https://comparelegal.com/">
<meta property="og:type" content="website">
<meta property="og:site_name" content="Compare Legal">
<meta property="og:title" content="Compare Legal: compare solicitors' fees across the UK">
<meta property="og:description" content="Compare solicitors' fees for conveyancing, wills, probate and family law across the UK.">
<meta property="og:url" content="https://comparelegal.com/">
<meta property="og:locale" content="en_GB">
<link rel="alternate" type="application/rss+xml" title="Compare Legal guides" href="/blog/feed.xml">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<style>:root{{padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}}body{{margin:0}}img{{max-width:100%}}[hidden]{{display:none!important}}</style>
<script src="/vendor/supabase-2.119.0.js"></script>
{ld}
{head}
</head>
<body>
{body}
</body>
</html>
"""
pathlib.Path('public/index.html').write_text(doc)
print('built public/index.html', len(doc), 'bytes')

sys.path.insert(0, 'site')
import sitegen  # noqa: E402
sitegen.main()
