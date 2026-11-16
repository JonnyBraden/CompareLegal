"""Build the site for static hosting.

1. Wrap src/app.html (the app) into public/index.html.
2. Generate the blog and local fee pages (site/sitegen.py) as plain HTML for search engines.
"""
import html
import json
import pathlib
import sys

sys.path.insert(0, 'site')
import sitegen  # noqa: E402

src = pathlib.Path('src/app.html').read_text()

# Plain-HTML version of the home page inside <main id="app">. The app replaces it as soon as
# its script runs, so visitors see the app; crawlers that don't run JavaScript (Bing, most AI
# search bots) still get the same content and the links to the guides and fee pages.
posts = sitegen.load_posts()
# Fee guides (the ones with a short answer) first, then the rest, newest first within each.
posts = [p for p in posts if p.get('summary')] + [p for p in posts if not p.get('summary')]
esc = lambda x: html.escape(str(x), quote=True)
area_lines = {
    'conveyancing': 'buying, selling or remortgaging a home. Fees are fixed and banded by the property price, plus searches and registration fees.',
    'wills': 'a single or mirror will, or a power of attorney. Simple wills are usually a fixed fee; trusts and business assets cost more.',
    'probate': 'getting a grant of probate (confirmation in Scotland) or having a solicitor administer the whole estate.',
    'family-law': 'an uncontested divorce and a consent order recording your financial agreement, or advice on children arrangements.',
}
svc_list = ''.join(f'<li><a href="/{a}/">{esc(sitegen.AREAS[a]["name"])}</a>: {esc(t)}</li>' for a, t in area_lines.items())
guide_list = ''.join(f'<li><a href="{p["url"]}">{esc(p["title"])}</a></li>' for p in posts[:10])
static_home = f'''<div class="wrap" style="padding:40px 0">
<p class="eyebrow">Solicitors' fees, side by side</p>
<h1>Know the full price before you pick a solicitor.</h1>
<p class="lede">Compare fixed fees for conveyancing, wills, probate and family law from firms across the UK. Every total includes VAT and disbursements, so the number you see is the number you pay. Prices are set by the firms themselves, it covers all four UK nations, and it's free to use with no sign-up.</p>
<h2>What you can compare</h2>
<ul>{svc_list}</ul>
<h2>How it works</h2>
<ol><li>Tell us what you need and where you are. For property, add the price, since conveyancing fees are set in value bands.</li>
<li>Compare the full cost: each firm's legal fee, VAT and disbursements added up, so you compare like with like.</li>
<li>Ask the firms you like for a written quote. They reply to you directly.</li></ol>
<h2>Three legal systems, one search</h2>
<p>England and Wales: solicitors regulated by the SRA, property registered at HM Land Registry, and Land Transaction Tax for buyers in Wales. Scotland: solicitors regulated by the Law Society of Scotland, confirmation instead of probate, and LBTT for buyers. Northern Ireland: solicitors regulated by the Law Society of Northern Ireland, Stamp Duty Land Tax at England's rates, and enduring powers of attorney.</p>
<h2>Guides to legal fees</h2>
<ul>{guide_list}</ul>
<p><a href="/blog/">All guides</a> · <a href="/solicitors/">Find a solicitor by city</a> · <a href="/about/">About Compare Legal and how prices are compared</a></p>
</div>'''
assert src.count('<main id="app"></main>') == 1
src = src.replace('<main id="app"></main>', f'<main id="app">{static_home}</main>')
guides_js = json.dumps([{'t': p['title'], 'u': p['url'], 'd': p['desc'], 'c': sitegen.CATS[p['cat']]} for p in posts[:6]], ensure_ascii=False).replace('</', '<\\/')
src = src.replace('</style>', f'</style>\n<script>window.CL_GUIDES={guides_js};</script>', 1)
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

sitegen.main()
