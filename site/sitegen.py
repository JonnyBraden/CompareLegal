"""Static pages for search engines: the blog, and fee pages for every service in every city.

Run by build.py. Everything is rendered to plain HTML at build time so Google sees the
content without running JavaScript. Firm data is read from Supabase at build time;
if that fails the pages still build, just without firm listings.
"""
import datetime as dt
import html
import json
import math
import os
import pathlib
import re
import shutil
import urllib.parse
import urllib.request

from md import front_matter, render, slugify

ROOT = pathlib.Path(__file__).resolve().parent.parent
PUBLIC = ROOT / 'public'
POSTS_DIR = ROOT / 'content' / 'posts'
BASE = 'https://comparelegal.com'
TODAY = dt.date.today()

# A city page is only offered to search engines once it has real firms nearby,
# so Google never sees hundreds of near-identical empty pages.
LOCAL_RADIUS_MILES = 25
LOCAL_INDEX_MIN_FIRMS = 3

SB_URL = 'https://dtsxrmmdnyjmplhoyinv.supabase.co'
SB_KEY = 'sb_publishable_gyrD_o-zaQ2XvgFLTUBgrQ_3r0MYZ3g'

e = lambda s: html.escape(str(s if s is not None else ''), quote=True)

# ---------------------------------------------------------------- reference data
NATIONS = {
    'England': {'slug': 'england', 'jur': 'EW', 'tax': 'Stamp Duty Land Tax', 'taxShort': 'SDLT', 'taxBody': 'HMRC',
                'land': 'HM Land Registry', 'reg': 'Solicitors Regulation Authority', 'of': 'England'},
    'Wales': {'slug': 'wales', 'jur': 'EW', 'tax': 'Land Transaction Tax', 'taxShort': 'LTT', 'taxBody': 'the Welsh Revenue Authority',
              'land': 'HM Land Registry', 'reg': 'Solicitors Regulation Authority', 'of': 'Wales'},
    'Scotland': {'slug': 'scotland', 'jur': 'SC', 'tax': 'Land and Buildings Transaction Tax', 'taxShort': 'LBTT', 'taxBody': 'Revenue Scotland',
                 'land': 'Registers of Scotland', 'reg': 'Law Society of Scotland', 'of': 'Scotland'},
    'Northern Ireland': {'slug': 'northern-ireland', 'jur': 'NI', 'tax': 'Stamp Duty Land Tax', 'taxShort': 'SDLT', 'taxBody': 'HMRC',
                         'land': 'Land Registry NI', 'reg': 'Law Society of Northern Ireland', 'of': 'Northern Ireland'},
}
JUR_LABEL = {'EW': 'England and Wales', 'SC': 'Scotland', 'NI': 'Northern Ireland'}

AREAS = {
    'conveyancing': dict(app='conveyancing', svc='purchase', name='Conveyancing', menu='Conveyancing',
                         pro={'EW': 'conveyancing solicitors', 'SC': 'conveyancing solicitors', 'NI': 'conveyancing solicitors'},
                         what='buying, selling or remortgaging a home', price_label='Buying a £250,000 home',
                         services=['purchase', 'sale', 'remortgage']),
    'wills': dict(app='wills', svc='will1', name='Wills & powers of attorney', menu='Wills',
                  pro={'EW': 'will writing solicitors', 'SC': 'will writing solicitors', 'NI': 'will writing solicitors'},
                  what='making a will or a power of attorney', price_label='A single will',
                  services=['will1', 'will2', 'poa']),
    'probate': dict(app='probate', svc='grant', name='Probate & estates', menu='Probate',
                    pro={'EW': 'probate solicitors', 'SC': 'executry solicitors', 'NI': 'probate solicitors'},
                    what='dealing with an estate after a death', price_label={'EW': 'Grant of probate only', 'SC': 'Confirmation only', 'NI': 'Grant of probate only'},
                    services=['grant', 'estate']),
    'family-law': dict(app='family', svc='divorce', name='Divorce & family law', menu='Family',
                       pro={'EW': 'divorce solicitors', 'SC': 'divorce solicitors', 'NI': 'divorce solicitors'},
                       what='divorce, finances and arrangements for children', price_label='An uncontested divorce',
                       services=['divorce', 'finances', 'children']),
}
TIMES = {'purchase': '8–12 weeks', 'will1': '1–2 weeks', 'grant': '8–16 weeks', 'divorce': '6–8 months'}

# Jurisdiction notes used on local and nation pages. Kept free of rates and fees that
# change each Budget; the dated guides carry those.
NOTES = {
    'conveyancing': {
        'England': "Buyers in England pay Stamp Duty Land Tax to HMRC once the price passes the nil-rate threshold, and first-time buyers get a higher threshold. Your solicitor or licensed conveyancer orders local authority, drainage and environmental searches, checks the title, handles the exchange of contracts and registers you as owner at HM Land Registry after completion.",
        'Wales': "Wales has its own tax, Land Transaction Tax, collected by the Welsh Revenue Authority. Its nil-rate threshold is higher than England's, but there is no separate first-time buyer relief. The legal process is otherwise the same as in England: searches, exchange of contracts, completion and registration at HM Land Registry.",
        'Scotland': "Scottish conveyancing works differently. Sellers must usually provide a Home Report, many homes are sold by \"offers over\" with a closing date, and the contract is formed by an exchange of letters between solicitors called missives. Buyers pay Land and Buildings Transaction Tax to Revenue Scotland, and ownership is registered with Registers of Scotland. The solicitor must be qualified in Scots law.",
        'Northern Ireland': "Northern Ireland has its own property law and its own solicitors' profession, regulated by the Law Society of Northern Ireland. Buyers pay Stamp Duty Land Tax at the same rates as England. Most titles are registered at Land Registry NI, but some older titles are still recorded in the Registry of Deeds, which can add work. Searches include property certificates from the local council and a search in the Enforcement of Judgments Office.",
    },
    'wills': {
        'England': "In England and Wales a will must be signed in front of two independent witnesses, and getting married or entering a civil partnership normally cancels an existing will. Lasting powers of attorney, one for property and finances and one for health and welfare, are registered with the Office of the Public Guardian.",
        'Wales': "Wales follows the law of England and Wales: a will needs two independent witnesses, and marriage normally cancels an existing will. Lasting powers of attorney are registered with the Office of the Public Guardian, and the forms are available in Welsh.",
        'Scotland': "A Scottish will needs only one witness, and you sign every page. Marriage does not cancel a will in Scotland, but a spouse, civil partner and children have \"legal rights\" to part of the moveable estate whatever the will says. Continuing and welfare powers of attorney are registered with the Office of the Public Guardian (Scotland).",
        'Northern Ireland': "In Northern Ireland a will needs two witnesses and marriage normally cancels an existing will. The usual power of attorney is an enduring power of attorney, which covers property and finances only. It must be registered with the Office of Care and Protection if you start to lose capacity.",
    },
    'probate': {
        'England': "To deal with an estate in England, executors usually need a grant of probate (or letters of administration where there is no will) from HM Courts and Tribunals Service. Firms in England and Wales must publish prices for uncontested probate, so a fixed-fee grant-only service is easy to compare against full estate administration.",
        'Wales': "Estates in Wales go through the same probate service as England. Executors can apply for the grant themselves, pay a firm for the grant only, or hand over the full administration. Firms must publish prices for uncontested probate.",
        'Scotland': "Scotland has no probate. Executors apply to the sheriff court for confirmation, and the work of administering the estate is called executry. Estates of up to £36,000 can use a simpler small estate procedure; larger estates are usually handled by a solicitor.",
        'Northern Ireland': "In Northern Ireland, grants of probate and letters of administration are issued by the Probate Office at the Royal Courts of Justice in Belfast, which handles applications from across Northern Ireland. Executors can apply themselves through nidirect or instruct a local solicitor.",
    },
    'family-law': {
        'England': "Divorce in England and Wales has been no-fault since April 2022: you confirm the marriage has broken down, with no need to blame anyone. There is a minimum 20-week wait before the conditional order. Finances are a separate step, and an agreement only becomes binding once a court approves a consent order.",
        'Wales': "Wales follows the no-fault divorce law of England and Wales, with the same online process and the same 20-week minimum wait before the conditional order. Agreeing finances and making them binding through a consent order is separate work.",
        'Scotland': "In Scotland you can divorce after one year's separation if your spouse agrees, or two years if they don't. Where there are no children under 16 and no financial claims, the cheaper simplified procedure may be available. Financial agreements are usually recorded in a minute of agreement.",
        'Northern Ireland': "Northern Ireland has not adopted no-fault divorce. You can't petition until two years after the marriage, and you must rely on one of five facts, such as two years' separation with consent or five years' separation. Reform has been consulted on, so check the current position with a local solicitor.",
    },
}

# Guides linked from each nation (filled from post front matter)


def load_cities():
    src = (ROOT / 'src' / 'app.html').read_text()
    m = re.search(r'const CITIES = (\{.*?\n\});', src, re.S)
    js = m.group(1)
    js = re.sub(r"'([^'\n]*)'", lambda x: json.dumps(x.group(1)), js)
    data = json.loads(js)
    out = {}
    for nation, rows in data.items():
        out[nation] = [{'name': n, 'lat': la, 'lng': lo, 'slug': slugify(n), 'nation': nation} for n, la, lo in rows]
    return out


def miles(a_lat, a_lng, b_lat, b_lng):
    r = math.pi / 180
    d_la, d_lo = (b_lat - a_lat) * r, (b_lng - a_lng) * r
    h = math.sin(d_la / 2) ** 2 + math.cos(a_lat * r) * math.cos(b_lat * r) * math.sin(d_lo / 2) ** 2
    return 2 * 3958.8 * math.asin(math.sqrt(h))


# ---------------------------------------------------------------- firms + prices
BANDS = [250000, 500000, 1000000, None]


def fetch_firms():
    if os.environ.get('FIRMS_JSON'):
        return json.loads(pathlib.Path(os.environ['FIRMS_JSON']).read_text())
    q = urllib.parse.urlencode({'select': 'id,name,nation,town,address,postcode,phone,website,reg_no,color,vat,services,lat,lng,listed,sample,updated_at',
                                'listed': 'eq.true', 'sample': 'eq.false'})
    req = urllib.request.Request(f'{SB_URL}/rest/v1/firms?{q}', headers={'apikey': SB_KEY, 'Authorization': f'Bearer {SB_KEY}'})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            rows = json.load(r)
        print(f'sitegen: {len(rows)} live firms from Supabase')
        return rows
    except Exception as ex:  # build must not fail because the database is unreachable
        print(f'sitegen: could not load firms ({ex}); building without listings')
        return []


def tiers_of(o):
    if isinstance(o.get('tiers'), list) and o['tiers']:
        ts = o['tiers']
        return [{'max': None if (i == len(ts) - 1 or t.get('max') is None) else float(t['max']), 'fee': t.get('fee')} for i, t in enumerate(ts)]
    bands = o.get('bands') or {}
    return [{'max': b, 'fee': bands.get(f'b{i + 1}')} for i, b in enumerate(BANDS)]


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def quote(firm, sid, price=250000):
    s = (firm.get('services') or {}).get(sid)
    if not s:
        return None
    disb = sum(num(d.get('amount')) for d in (s.get('disb') or []))
    if s.get('type') == 'hourly':
        return {'type': 'hourly', 'rate': num(s.get('rate')), 'total': None}
    if sid in ('purchase', 'sale', 'remortgage'):
        ts = tiers_of(s)
        t = next((t for t in ts if t['max'] is None or price <= t['max']), ts[-1])
        legal = num(t['fee'])
    else:
        legal = num(s.get('fee'))
    if not legal:
        return None
    vat = 0 if firm.get('vat') is False else legal * 0.2
    return {'type': s.get('type'), 'total': legal + vat + disb}


gbp = lambda n: '£{:,.0f}'.format(n)


def offers_area(firm, area):
    return any(quote(firm, sid) for sid in AREAS[area]['services'])


# ---------------------------------------------------------------- page shell
FONT = 'https://fonts.googleapis.com/css2?family=Young+Serif&family=Figtree:wght@400;500;600;700&display=swap'
LOGO = '<svg width="28" height="28" viewBox="0 0 30 30" aria-hidden="true"><rect x="1" y="1" width="28" height="28" rx="7" fill="var(--ink)"/><rect x="7" y="9" width="10" height="3" rx="1.5" fill="var(--paper)"/><rect x="7" y="14" width="16" height="3" rx="1.5" fill="#FFE07A"/><rect x="7" y="19" width="13" height="3" rx="1.5" fill="var(--paper)"/></svg>'


def app_link(svc, nation=None, city=None):
    p = {'svc': svc}
    if nation:
        p['nation'] = nation
    if city:
        p['city'] = city
    return '/#results?' + urllib.parse.urlencode(p, quote_via=urllib.parse.quote)


def crumbs_html(crumbs):
    items = []
    for i, (label, url) in enumerate(crumbs):
        last = i == len(crumbs) - 1
        items.append(f'<li>{e(label)}</li>' if last else f'<li><a href="{e(url)}">{e(label)}</a></li>')
    return '<nav class="crumbs" aria-label="Breadcrumb"><ol>' + ''.join(items) + '</ol></nav>'


def crumbs_ld(crumbs):
    return {'@context': 'https://schema.org', '@type': 'BreadcrumbList', 'itemListElement': [
        {'@type': 'ListItem', 'position': i + 1, 'name': label, 'item': BASE + url} for i, (label, url) in enumerate(crumbs)]}


def faq_ld(faqs):
    strip = lambda h: re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', h)).strip()
    return {'@context': 'https://schema.org', '@type': 'FAQPage', 'mainEntity': [
        {'@type': 'Question', 'name': q, 'acceptedAnswer': {'@type': 'Answer', 'text': strip(a)}} for q, a in faqs]}


def shell(*, title, desc, path, body, ld=(), index=True, og_type='website', active=''):
    nav = [('Compare fees', '/#results', 'compare'), ('Guides', '/blog/', 'blog'), ('Find a solicitor', '/solicitors/', 'local'), ('For law firms', '/#firms', 'firms')]
    navh = ''.join(f'<a href="{u}"{" aria-current=page" if k == active else ""}{" class=hide-sm" if k in ("compare", "local") else ""}>{t}</a>' for t, u, k in nav)
    esc_ld = lambda x: json.dumps(x, ensure_ascii=False).replace('</', '<' + chr(92) + '/')
    lds = ''.join('<script type="application/ld+json">' + esc_ld(x) + '</script>' for x in ld)
    robots = 'index,follow,max-image-preview:large' if index else 'noindex,follow'
    foot_links = ''.join(f'<li><a href="/{a}/">{e(AREAS[a]["name"])}</a></li>' for a in AREAS)
    return f'''<!doctype html>
<html lang="en-GB">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title>
<meta name="description" content="{e(desc)}">
<meta name="robots" content="{robots}">
<link rel="canonical" href="{BASE}{path}">
<meta property="og:type" content="{og_type}">
<meta property="og:site_name" content="Compare Legal">
<meta property="og:title" content="{e(title)}">
<meta property="og:description" content="{e(desc)}">
<meta property="og:url" content="{BASE}{path}">
<meta property="og:locale" content="en_GB">
<meta name="twitter:card" content="summary">
<link rel="alternate" type="application/rss+xml" title="Compare Legal guides" href="/blog/feed.xml">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="{FONT}">
<link rel="stylesheet" href="/assets/site.css?v={TODAY:%Y%m%d}">
{lds}
</head>
<body>
<a class="skip" href="#main">Skip to content</a>
<header class="top"><div class="wrap">
  <a class="brand" href="/">{LOGO}<b>Compare Legal</b></a>
  <nav class="nav" aria-label="Main">{navh}</nav>
</div></header>
<main id="main">
{body}
</main>
<footer><div class="wrap foot">
  <div><b>Compare Legal</b><p>Compare solicitors' fees for conveyancing, wills, probate and family law across England, Wales, Scotland and Northern Ireland. Fees are set by each firm; always confirm the final quote before you instruct them.</p></div>
  <div><h2>Fees by service</h2><ul>{foot_links}</ul></div>
  <div><h2>Guides</h2><ul><li><a href="/blog/">All guides</a></li><li><a href="/solicitors/">Solicitors by city</a></li><li><a href="/#firms">List your firm</a></li></ul></div>
</div></footer>
</body>
</html>
'''


def write(path, content):
    f = PUBLIC / path.strip('/') / 'index.html' if not path.endswith('.xml') and not path.endswith('.txt') else PUBLIC / path.strip('/')
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(content)


# ---------------------------------------------------------------- blog
CATS = {a: AREAS[a]['name'] for a in AREAS}


def load_posts():
    posts = []
    for f in sorted(POSTS_DIR.glob('*.md')):
        meta, body = front_matter(f.read_text())
        if str(meta.get('draft', '')).lower() == 'true':
            continue
        html_body, heads, faqs = render(body)
        words = len(re.sub(r'<[^>]+>', ' ', html_body).split())
        nations = meta.get('nations') or list(NATIONS)
        if isinstance(nations, str):
            nations = [nations]
        posts.append(dict(slug=f.stem, title=meta['title'], desc=meta['description'], date=meta['date'],
                          updated=meta.get('updated', meta['date']), cat=meta['category'], nations=nations,
                          svc=meta.get('service', AREAS[meta['category']]['svc']), html=html_body, heads=heads, faqs=faqs,
                          mins=max(2, round(words / 220)), url=f'/blog/{f.stem}/', short=meta.get('short', meta['title'])))
    posts.sort(key=lambda p: (p['date'], p['updated']), reverse=True)
    return posts


def nice_date(s):
    d = dt.date.fromisoformat(s)
    return f'{d.day} {d:%B %Y}'


def post_card(p):
    return f'''<article class="card">
  <p class="eyebrow"><a href="/blog/category/{p['cat']}/">{e(CATS[p['cat']])}</a></p>
  <h3><a href="{p['url']}">{e(p['title'])}</a></h3>
  <p class="muted">{e(p['desc'])}</p>
  <p class="meta">{nice_date(p['updated'])} · {p['mins']} min read</p>
</article>'''


def city_links(area, nations, cities, limit=None):
    blocks = []
    for n in nations:
        cs = cities.get(n, [])
        if limit:
            cs = cs[:limit]
        links = ''.join(f'<li><a href="/{area}/{NATIONS[n]["slug"]}/{c["slug"]}/">{e(c["name"])}</a></li>' for c in cs)
        blocks.append(f'<div class="cityblock"><h3><a href="/{area}/{NATIONS[n]["slug"]}/">{e(n)}</a></h3><ul class="cities">{links}</ul></div>')
    return ''.join(blocks)


def build_blog(posts, cities):
    # index
    cats = ''.join(f'<li><a class="chip" href="/blog/category/{k}/">{e(v)}</a></li>' for k, v in CATS.items())
    lead, rest = (posts[0], posts[1:]) if posts else (None, [])
    lead_h = ''
    if lead:
        lead_h = f'''<article class="lead card">
  <p class="eyebrow"><a href="/blog/category/{lead['cat']}/">{e(CATS[lead['cat']])}</a> · Latest guide</p>
  <h2><a href="{lead['url']}">{e(lead['title'])}</a></h2>
  <p class="muted">{e(lead['desc'])}</p>
  <p class="meta">{nice_date(lead['updated'])} · {lead['mins']} min read</p>
</article>'''
    body = f'''<section class="pagehead"><div class="wrap">
  {crumbs_html([('Home', '/'), ('Guides', '/blog/')])}
  <h1>Legal fees guides</h1>
  <p class="lede">Plain-English guides to what conveyancing, wills, probate and divorce cost in England, Wales, Scotland and Northern Ireland, and how the law differs in each.</p>
  <ul class="chips">{cats}</ul>
</div></section>
<section class="band"><div class="wrap">
  {lead_h}
  <div class="grid">{''.join(post_card(p) for p in rest)}</div>
</div></section>
{local_band('conveyancing', cities, 'Compare conveyancing fees near you')}'''
    ld = [crumbs_ld([('Home', '/'), ('Guides', '/blog/')]),
          {'@context': 'https://schema.org', '@type': 'Blog', 'name': 'Compare Legal guides', 'url': BASE + '/blog/',
           'blogPost': [{'@type': 'BlogPosting', 'headline': p['title'], 'url': BASE + p['url'], 'datePublished': p['date'], 'dateModified': p['updated']} for p in posts]}]
    write('/blog/', shell(title='Legal Fees Guides: Conveyancing, Wills, Probate & Divorce | Compare Legal',
                          desc='Guides to what solicitors charge for conveyancing, wills, probate and divorce across England, Wales, Scotland and Northern Ireland.',
                          path='/blog/', body=body, ld=ld, active='blog'))
    # categories
    for k, name in CATS.items():
        ps = [p for p in posts if p['cat'] == k]
        crumbs = [('Home', '/'), ('Guides', '/blog/'), (name, f'/blog/category/{k}/')]
        body = f'''<section class="pagehead"><div class="wrap">
  {crumbs_html(crumbs)}
  <h1>{e(name)} guides</h1>
  <p class="lede">Guides on {e(AREAS[k]['what'])}: costs, the process, and how the rules differ across the UK.</p>
</div></section>
<section class="band"><div class="wrap"><div class="grid">{''.join(post_card(p) for p in ps) or '<p class="muted">New guides are on the way.</p>'}</div></div></section>
{local_band(k, cities, f'Compare {AREAS[k]["name"].lower()} fees by city')}'''
        write(f'/blog/category/{k}/', shell(title=f'{name} Guides | Compare Legal', desc=f'Guides on {AREAS[k]["what"]} in England, Wales, Scotland and Northern Ireland, including typical costs and how to compare solicitors.',
                                            path=f'/blog/category/{k}/', body=body, ld=[crumbs_ld(crumbs)], index=bool(ps), active='blog'))
    # posts
    for p in posts:
        related = [q for q in posts if q is not p and (q['cat'] == p['cat'] or set(q['nations']) & set(p['nations']))][:3]
        toc = ''.join(f'<li><a href="#{k}">{e(t)}</a></li>' for lvl, k, t in p['heads'] if lvl == 2)
        crumbs = [('Home', '/'), ('Guides', '/blog/'), (CATS[p['cat']], f'/blog/category/{p["cat"]}/'), (p['short'], p['url'])]
        cta_nation = p['nations'][0] if len(p['nations']) == 1 else None
        area = AREAS[p['cat']]
        body = f'''<article class="post">
<header class="pagehead"><div class="wrap narrow">
  {crumbs_html(crumbs)}
  <h1>{e(p['title'])}</h1>
  <p class="lede">{e(p['desc'])}</p>
  <p class="meta">By the Compare Legal team · Updated <time datetime="{p['updated']}">{nice_date(p['updated'])}</time> · {p['mins']} min read</p>
</div></header>
<div class="wrap postgrid">
  <div class="prose">
    {f'<nav class="toc" aria-label="Contents"><p class="eyebrow">In this guide</p><ol>{toc}</ol></nav>' if toc else ''}
    {p['html']}
    <p class="disclaimer">This guide is general information about the law and fees at the date shown, not legal advice for your situation. Rates and court fees change, so check the official sources linked above or ask a solicitor before you rely on a figure.</p>
  </div>
  <aside class="side">
    <div class="ctabox">
      <p class="eyebrow">Compare fees</p>
      <p><b>See what firms{(' in ' + e(cta_nation)) if cta_nation else ''} charge</b> for {e(area['what'])}. Totals include VAT and disbursements.</p>
      <a class="btn" href="{app_link(p['svc'], cta_nation)}">Compare {e(area['menu'].lower())} fees</a>
    </div>
    <div class="sidecities"><p class="eyebrow">Find a solicitor near you</p>{city_links(p['cat'], [n for n in p['nations']], cities, limit=8 if len(p['nations']) > 1 else None)}</div>
  </aside>
</div>
</article>
{f'<section class="band alt"><div class="wrap"><h2>Related guides</h2><div class="grid">{"".join(post_card(q) for q in related)}</div></div></section>' if related else ''}'''
        ld = [crumbs_ld(crumbs), {
            '@context': 'https://schema.org', '@type': 'BlogPosting', 'headline': p['title'], 'description': p['desc'],
            'datePublished': p['date'], 'dateModified': p['updated'], 'inLanguage': 'en-GB',
            'mainEntityOfPage': BASE + p['url'], 'url': BASE + p['url'],
            'author': {'@type': 'Organization', 'name': 'Compare Legal', 'url': BASE + '/'},
            'publisher': {'@type': 'Organization', 'name': 'Compare Legal', 'url': BASE + '/', 'logo': {'@type': 'ImageObject', 'url': BASE + '/logo.png'}},
            'about': CATS[p['cat']], 'spatialCoverage': [{'@type': 'Place', 'name': n} for n in p['nations']]}]
        if p['faqs']:
            ld.append(faq_ld(p['faqs']))
        write(p['url'], shell(title=f'{p["title"]} | Compare Legal', desc=p['desc'], path=p['url'], body=body, ld=ld, og_type='article', active='blog'))
    # feed
    items = ''.join(f'<item><title>{e(p["title"])}</title><link>{BASE}{p["url"]}</link><guid>{BASE}{p["url"]}</guid><pubDate>{dt.datetime.fromisoformat(p["date"]):%a, %d %b %Y} 08:00:00 +0000</pubDate><description>{e(p["desc"])}</description></item>' for p in posts)
    (PUBLIC / 'blog' / 'feed.xml').write_text(f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>Compare Legal guides</title><link>{BASE}/blog/</link><description>Guides to legal fees across the UK</description><language>en-gb</language>{items}</channel></rss>')


def local_band(area, cities, heading):
    return f'''<section class="band alt"><div class="wrap">
  <h2>{e(heading)}</h2>
  <div class="cityblocks">{city_links(area, list(NATIONS), cities, limit=None)}</div>
</div></section>'''


# ---------------------------------------------------------------- local pages
def firm_row(f, area, city=None):
    svc = AREAS[area]['svc']
    q = quote(f, svc)
    if q and q['total']:
        price = f'<b class="num">{gbp(q["total"])}</b><small>{"fixed fee" if q["type"] == "fixed" else "from"}, inc. VAT</small>'
    elif q and q['type'] == 'hourly':
        price = f'<b class="num">{gbp(q["rate"])}/hr</b><small>hourly rate</small>'
    else:
        price = '<small>Other services listed</small>'
    ini = ''.join(w[0] for w in re.sub(r'&', '', f.get('name') or '?').split() if w[:1].isalpha())[:2].upper() or '?'
    dist = f' · {round(f["_d"])} miles' if city and f.get('_d') is not None and f['_d'] >= 1 else ''
    return f'''<li class="firm"><span class="ini" style="background:{e(f.get('color') or '#24507A')}" aria-hidden="true">{e(ini)}</span>
  <span class="fname"><b>{e(f.get('name'))}</b><small>{e(f.get('town') or '')}{dist}</small></span>
  <span class="fprice">{price}</span></li>'''


def price_summary(firms, area, jur):
    svc = AREAS[area]['svc']
    totals = sorted(q['total'] for q in (quote(f, svc) for f in firms) if q and q['total'])
    if len(totals) < LOCAL_INDEX_MIN_FIRMS:
        return None
    lab = AREAS[area]['price_label']
    lab = lab[jur] if isinstance(lab, dict) else lab
    return dict(label=lab, n=len(totals), lo=totals[0], hi=totals[-1], mid=totals[len(totals) // 2])


def local_faqs(area, nation, city, near, summary):
    jur = NATIONS[nation]['jur']
    where = city or nation
    pro = AREAS[area]['pro'][jur]
    if summary:
        cost = f"Across {summary['n']} firms listed {'near ' + where if city else 'in ' + where} on Compare Legal, {summary['label'].lower()} costs between {gbp(summary['lo'])} and {gbp(summary['hi'])} in total, including VAT and disbursements. The median quote is {gbp(summary['mid'])}."
    else:
        cost = f"Fees are set by each firm and vary with the work involved. Compare fixed fees side by side on Compare Legal, check what each quote includes, and make sure VAT and disbursements are in the total before you choose."
    qa = [(f'How much do {pro} in {where} charge?', cost)]
    if area == 'conveyancing':
        if jur == 'EW':
            qa.append((f'Does my conveyancer need to be based in {where}?', f"No. Any solicitor or licensed conveyancer in England and Wales can handle a purchase in {where}, wherever their office is. A local firm can help if you want to meet in person or the property has local quirks."))
        else:
            qa.append((f'Does my solicitor need to be based in {where}?', f"Not in {where} itself, but they must be qualified in {JUR_LABEL[jur]}. Property law in {JUR_LABEL[jur]} is separate, so a firm from elsewhere in the UK usually can't act on a {nation} property."))
        qa.append(('How long does conveyancing take?', 'A typical purchase takes 8 to 12 weeks from offer to completion. Chains, leasehold paperwork, mortgage offers and survey issues can make it longer.'))
    elif area == 'wills':
        qa.append(('Can I make a will without a solicitor?', f"Yes, but a homemade will is easier to get wrong, and it may not be valid if it isn't signed and witnessed properly. In {JUR_LABEL[jur]} {'one witness is needed' if jur == 'SC' else 'two witnesses are needed'}. A fixed-fee will from a solicitor is usually worth it if you own property, have children or a blended family."))
    elif area == 'probate':
        term = 'confirmation' if jur == 'SC' else 'probate'
        qa.append((f'Do I always need {term}?', f"Not always. Small estates, and assets held jointly that pass automatically to the survivor, may not need it. Banks and other holders set their own limits, so check with each one before you apply."))
    else:
        qa.append((f'How long does a divorce take in {nation}?', {'EW': 'Usually around six to eight months for an uncontested divorce, because of the minimum 20-week wait before the conditional order and the six-week wait before the final order.',
                                                                 'SC': 'A simplified divorce can be quick once the separation period has passed. A divorce with disputes over children or money takes longer.',
                                                                 'NI': 'An undefended divorce usually takes several months from petition to decree absolute. A petition can only be presented two years or more after the marriage.'}[jur]))
    qa.append(('What should a quote include?', "The firm's own fee, VAT, and disbursements: the costs the firm pays for you, such as searches, registration fees or court fees. Ask whether the fee is fixed and what would make it go up."))
    return qa


def build_local(cities, firms, posts):
    pages = []  # (path, indexable)
    for f in firms:
        if f.get('lat') is None or f.get('lng') is None:
            f['_ok'] = False
    for area, A in AREAS.items():
        guides = [p for p in posts if p['cat'] == area]
        # area hub
        crumbs = [('Home', '/'), (A['name'], f'/{area}/')]
        nat_cards = ''
        for n, N in NATIONS.items():
            nf = [f for f in firms if f.get('nation') == n and offers_area(f, area)]
            nat_cards += f'<a class="card natcard" href="/{area}/{N["slug"]}/"><h3>{e(n)}</h3><p class="muted">{e(NOTES[area][n].split(". ")[0])}.</p><p class="meta">{len(cities[n])} cities{f" · {len(nf)} firms listed" if nf else ""}</p></a>'
        body = f'''<section class="pagehead"><div class="wrap">
  {crumbs_html(crumbs)}
  <h1>Compare {e(A['name'].lower())} fees across the UK</h1>
  <p class="lede">Find {e(A['pro']['EW'])} near you and compare their fees for {e(A['what'])}. Every total includes VAT and disbursements, so you can compare like with like.</p>
  <a class="btn" href="{app_link(A['svc'])}">Compare fees now</a>
</div></section>
<section class="band"><div class="wrap"><h2>Choose your nation</h2><p class="muted sub">The law, the process and the taxes differ between the UK's three legal systems.</p><div class="grid g4">{nat_cards}</div></div></section>
<section class="band alt"><div class="wrap"><h2>Find a solicitor by city</h2><div class="cityblocks">{city_links(area, list(NATIONS), cities)}</div></div></section>
{guides_band(guides)}'''
        write(f'/{area}/', shell(title=f'Compare {A["name"]} Fees | Solicitors Across the UK | Compare Legal',
                                 desc=f'Compare {A["pro"]["EW"]} fees for {A["what"]} in England, Wales, Scotland and Northern Ireland. Totals include VAT and disbursements.',
                                 path=f'/{area}/', body=body, ld=[crumbs_ld(crumbs)], active='local'))
        pages.append((f'/{area}/', True))
        for n, N in NATIONS.items():
            jur = N['jur']
            nfirms = [f for f in firms if f.get('nation') == n and offers_area(f, area)]
            summ = price_summary(nfirms, area, jur)
            ncrumbs = crumbs + [(n, f'/{area}/{N["slug"]}/')]
            ng = [p for p in guides if n in p['nations']] or guides
            faqs = local_faqs(area, n, None, nfirms, summ)
            pro = A['pro'][jur]
            body = f'''<section class="pagehead"><div class="wrap">
  {crumbs_html(ncrumbs)}
  <h1>{e(pro.capitalize())} in {e(n)}: compare fees</h1>
  <p class="lede">{e(NOTES[area][n])}</p>
  <a class="btn" href="{app_link(A['svc'], n)}">Compare fees in {e(n)}</a>
</div></section>
{summary_band(summ, n) if summ else ''}
<section class="band"><div class="wrap"><h2>Choose your nearest city</h2><ul class="cities big">{''.join(f'<li><a href="/{area}/{N["slug"]}/{c["slug"]}/">{e(c["name"])}</a></li>' for c in cities[n])}</ul></div></section>
{firms_band(nfirms[:12], area, f'Firms in {n}') if nfirms else ''}
{faq_band(faqs)}
{guides_band(ng)}'''
            write(f'/{area}/{N["slug"]}/', shell(title=f'{pro.title()} in {n}: Compare Fees | Compare Legal',
                                                desc=f'Compare {pro} in {n}. See fixed fees for {A["what"]}, including VAT and disbursements, and how the process works in {n}.',
                                                path=f'/{area}/{N["slug"]}/', body=body, ld=[crumbs_ld(ncrumbs), faq_ld(faqs)], active='local'))
            pages.append((f'/{area}/{N["slug"]}/', True))
            for c in cities[n]:
                near = []
                for f in nfirms:
                    if f.get('lat') is None:
                        continue
                    d = miles(c['lat'], c['lng'], float(f['lat']), float(f['lng']))
                    if d <= LOCAL_RADIUS_MILES:
                        near.append({**f, '_d': d})
                near.sort(key=lambda f: f['_d'])
                csumm = price_summary(near, area, jur)
                indexable = len(near) >= LOCAL_INDEX_MIN_FIRMS
                others = sorted((x for x in cities[n] if x is not c), key=lambda x: miles(c['lat'], c['lng'], x['lat'], x['lng']))[:6]
                ccrumbs = ncrumbs + [(c['name'], f'/{area}/{N["slug"]}/{c["slug"]}/')]
                faqs = local_faqs(area, n, c['name'], near, csumm)
                cg = [p for p in guides if n in p['nations']] or guides
                firms_html = firms_band(near[:10], area, f'Firms within {LOCAL_RADIUS_MILES} miles of {c["name"]}', city=c) if near else f'''<section class="band"><div class="wrap"><div class="empty">
  <h2>Firms near {e(c['name'])}</h2>
  <p>We're adding firms in {e(c['name'])} now. In the meantime, compare firms across {e(n)}; any solicitor qualified in {e(JUR_LABEL[jur])} can act for you wherever their office is.</p>
  <a class="btn ghost" href="{app_link(A['svc'], n, c['name'])}">Compare firms across {e(n)}</a>
  <p class="muted small">Are you a firm in {e(c['name'])}? <a href="/#firms">List your fees free</a>.</p>
</div></div></section>'''
                body = f'''<section class="pagehead"><div class="wrap">
  {crumbs_html(ccrumbs)}
  <h1>{e(pro.capitalize())} in {e(c['name'])}</h1>
  <p class="lede">Compare fees from {e(pro)} in and around {e(c['name'])} for {e(A['what'])}. Every total includes VAT and the costs firms pay on your behalf.</p>
  <a class="btn" href="{app_link(A['svc'], n, c['name'])}">Compare fees near {e(c['name'])}</a>
</div></section>
{summary_band(csumm, c['name']) if csumm else ''}
{firms_html}
<section class="band alt"><div class="wrap two">
  <div><h2>How it works in {e(n)}</h2><p>{e(NOTES[area][n])}</p>
  <p class="muted small">Want the detail? Read our guides below.</p></div>
  <div><h2>Nearby cities</h2><ul class="cities">{''.join(f'<li><a href="/{area}/{N["slug"]}/{x["slug"]}/">{e(x["name"])}</a> <small class="muted">{round(miles(c["lat"], c["lng"], x["lat"], x["lng"]))} mi</small></li>' for x in others)}</ul>
  <h2 class="mt">Other services in {e(c['name'])}</h2><ul class="cities">{''.join(f'<li><a href="/{a2}/{N["slug"]}/{c["slug"]}/">{e(AREAS[a2]["name"])}</a></li>' for a2 in AREAS if a2 != area)}</ul></div>
</div></section>
{faq_band(faqs)}
{guides_band(cg)}'''
                ld = [crumbs_ld(ccrumbs), faq_ld(faqs)]
                if near:
                    ld.append({'@context': 'https://schema.org', '@type': 'ItemList', 'name': f'{pro.capitalize()} near {c["name"]}', 'itemListElement': [
                        {'@type': 'ListItem', 'position': i + 1, 'item': {k: v for k, v in {
                            '@type': 'LegalService', 'name': f.get('name'), 'telephone': f.get('phone') or None, 'url': f.get('website') or None,
                            'address': {'@type': 'PostalAddress', 'addressLocality': f.get('town') or None, 'postalCode': f.get('postcode') or None, 'addressCountry': 'GB'}}.items() if v}}
                        for i, f in enumerate(near[:10])]})
                path = f'/{area}/{N["slug"]}/{c["slug"]}/'
                write(path, shell(title=f'{pro.title()} in {c["name"]}: Compare Fees | Compare Legal',
                                  desc=f'Compare {pro} in {c["name"]}. Fixed fees for {A["what"]}, with VAT and disbursements included, from firms near {c["name"]}.',
                                  path=path, body=body, ld=ld, index=indexable, active='local'))
                pages.append((path, indexable))
    # A–Z hub
    blocks = ''.join(f'<section class="band{" alt" if i % 2 else ""}"><div class="wrap"><h2><a href="/{a}/">{e(A["name"])}</a></h2><div class="cityblocks">{city_links(a, list(NATIONS), cities)}</div></div></section>' for i, (a, A) in enumerate(AREAS.items()))
    crumbs = [('Home', '/'), ('Find a solicitor', '/solicitors/')]
    body = f'''<section class="pagehead"><div class="wrap">{crumbs_html(crumbs)}<h1>Find a solicitor near you</h1>
<p class="lede">Compare fees from solicitors in {sum(len(v) for v in cities.values())} cities across England, Wales, Scotland and Northern Ireland.</p></div></section>{blocks}'''
    write('/solicitors/', shell(title='Find a Solicitor Near You: Compare Fees by City | Compare Legal',
                                desc='Compare conveyancing, wills, probate and divorce solicitors by city across England, Wales, Scotland and Northern Ireland.',
                                path='/solicitors/', body=body, ld=[crumbs_ld(crumbs)], active='local'))
    pages.append(('/solicitors/', True))
    return pages


def summary_band(s, where):
    return f'''<section class="band tight"><div class="wrap"><div class="stats">
  <div><span class="eyebrow">{e(s['label'])} near {e(where)}</span><b class="num">{gbp(s['lo'])} – {gbp(s['hi'])}</b><small>Range of total quotes from {s['n']} firms, inc. VAT and disbursements</small></div>
  <div><span class="eyebrow">Typical quote</span><b class="num">{gbp(s['mid'])}</b><small>The median of the quotes listed</small></div>
</div></div></section>'''


def firms_band(fs, area, heading, city=None):
    return f'''<section class="band"><div class="wrap">
  <h2>{e(heading)}</h2>
  <p class="muted sub">{e(AREAS[area]['price_label'] if isinstance(AREAS[area]['price_label'], str) else 'Headline service')}: total quoted, including VAT and disbursements. Prices are set by each firm.</p>
  <ul class="firms">{''.join(firm_row(f, area, city) for f in fs)}</ul>
</div></section>'''


def faq_band(faqs):
    items = ''.join(f'<details><summary>{e(q)}</summary><p>{e(a)}</p></details>' for q, a in faqs)
    return f'<section class="band"><div class="wrap narrowish"><h2>Frequently asked questions</h2><div class="faq">{items}</div></div></section>'


def guides_band(ps):
    if not ps:
        return ''
    return f'<section class="band alt"><div class="wrap"><h2>Guides</h2><div class="grid">{"".join(post_card(p) for p in ps[:3])}</div></div></section>'


# ---------------------------------------------------------------- sitemap etc.
def build_meta(posts, pages):
    urls = [('/', TODAY.isoformat(), '1.0'), ('/blog/', posts[0]['updated'] if posts else TODAY.isoformat(), '0.8')]
    urls += [(f'/blog/category/{k}/', TODAY.isoformat(), '0.5') for k in CATS if any(p['cat'] == k for p in posts)]
    urls += [(p['url'], p['updated'], '0.7') for p in posts]
    urls += [(u, TODAY.isoformat(), '0.6') for u, ok in pages if ok]
    body = ''.join(f'<url><loc>{BASE}{u}</loc><lastmod>{d}</lastmod><priority>{pr}</priority></url>' for u, d, pr in urls)
    (PUBLIC / 'sitemap.xml').write_text(f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>\n')
    (PUBLIC / 'robots.txt').write_text(f'User-agent: *\nAllow: /\nDisallow: /vendor/\n\nSitemap: {BASE}/sitemap.xml\n')
    (PUBLIC / 'favicon.svg').write_text(LOGO.replace('var(--ink)', '#15212C').replace('var(--paper)', '#F5F7F6').replace('width="28" height="28" ', 'xmlns="http://www.w3.org/2000/svg" '))
    assets = PUBLIC / 'assets'
    assets.mkdir(exist_ok=True)
    shutil.copy(ROOT / 'site' / 'site.css', assets / 'site.css')
    return len(urls)


GENERATED = ['blog', 'solicitors', 'assets', *AREAS]


def main():
    for d in GENERATED:
        shutil.rmtree(PUBLIC / d, ignore_errors=True)
    cities = load_cities()
    firms = fetch_firms()
    posts = load_posts()
    build_blog(posts, cities)
    pages = build_local(cities, firms, posts)
    n = build_meta(posts, pages)
    idx = sum(1 for _, ok in pages if ok)
    print(f'sitegen: {len(posts)} posts, {len(pages)} local pages ({idx} indexable), {n} URLs in sitemap')


if __name__ == '__main__':
    main()
