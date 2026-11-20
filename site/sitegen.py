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

# Date the page templates last changed in a way that matters to readers. Used as the
# sitemap lastmod for generated pages, so it doesn't reset to "today" on every build.
TEMPLATE_DATE = '2026-11-20'

# Guides are credited to the organisation Compare Legal. None means the byline stays
# "By the Compare Legal team". To name a reviewer, set e.g.:
# REVIEWER = {'name': 'Compare Legal', 'job': 'Editor', 'bio': 'One or two sentences.', 'url': 'https://comparelegal.com/about/'}
REVIEWER = None

# Public contact address shown on the About page for corrections. None hides the line.
CONTACT_EMAIL = None

SB_URL = 'https://dtsxrmmdnyjmplhoyinv.supabase.co'
SB_KEY = 'sb_publishable_gyrD_o-zaQ2XvgFLTUBgrQ_3r0MYZ3g'

e = lambda s: html.escape(str(s if s is not None else ''), quote=True)

# One organisation id for every page, so Article author/publisher and the homepage
# graph all point at the same entity. Logo is the square mark; the social image is separate.
ORG_ID = BASE + '/#organization'
SITE_ID = BASE + '/#website'
OG_IMAGE = BASE + '/og.png'
ORG_DESC = "Compare Legal is an independent price comparison for solicitors' fees in England, Wales, Scotland and Northern Ireland."
BRAND = ' | Compare Legal'
TITLE_MAX = 60
DESC_MIN, DESC_MAX = 150, 160


def page_title(text):
    """SEO title of at most 60 characters. Append the brand only when it fits."""
    text = re.sub(r'\s+', ' ', str(text or '')).strip()
    if text.endswith(BRAND):
        text = text[:-len(BRAND)].strip()
    if text and len(text) + len(BRAND) <= TITLE_MAX:
        return text + BRAND
    if len(text) <= TITLE_MAX:
        return text
    cut = text[:TITLE_MAX]
    sp = cut.rfind(' ')
    if sp >= 24:
        cut = cut[:sp]
    return cut.rstrip(' :|-–—,&')


def organization_node():
    node = {
        '@context': 'https://schema.org',
        '@type': 'Organization',
        '@id': ORG_ID,
        'name': 'Compare Legal',
        'url': BASE + '/',
        'description': ORG_DESC,
        'logo': {
            '@type': 'ImageObject',
            'url': BASE + '/logo.png',
            'width': 512,
            'height': 512,
        },
    }
    if CONTACT_EMAIL:
        node['email'] = CONTACT_EMAIL
    return node


def website_node():
    return {
        '@context': 'https://schema.org',
        '@type': 'WebSite',
        '@id': SITE_ID,
        'name': 'Compare Legal',
        'url': BASE + '/',
        'description': ORG_DESC,
        'inLanguage': 'en-GB',
        'publisher': {'@id': ORG_ID},
    }


def fit_meta(text):
    """Meta description of 150–160 characters. In-range copy is left as written."""
    text = re.sub(r'\s+', ' ', str(text or '')).strip()
    if text and text[-1] not in '.!?':
        text += '.'
    if DESC_MIN <= len(text) <= DESC_MAX:
        return text
    closers = (
        'Totals include VAT and disbursements.',
        'You can compare firms side by side.',
        'Firms set their own prices.',
        'Compare like for like.',
        'Ask what the fee includes.',
        'Check the full price.',
        'Check the total.',
        'Fees vary.',
    )
    if len(text) < DESC_MIN:
        guard = 0
        while len(text) < DESC_MIN and guard < 8:
            guard += 1
            added = False
            for extra in closers:
                trial = f'{text} {extra}'
                if len(trial) <= DESC_MAX:
                    text = trial
                    added = True
                    if len(text) >= DESC_MIN:
                        break
            if not added or len(text) >= DESC_MIN:
                break
    if len(text) > DESC_MAX:
        window = text[:DESC_MAX]
        cut = None
        for i, ch in enumerate(window):
            if ch in '.!?' and DESC_MIN <= i + 1 <= DESC_MAX:
                cut = i + 1
        if cut:
            text = window[:cut]
        else:
            sp = window.rfind(' ')
            while sp >= DESC_MIN - 1:
                chunk = window[:sp].rstrip(' ,;:-')
                if DESC_MIN <= len(chunk) + 1 <= DESC_MAX:
                    text = chunk + '.'
                    break
                sp = window.rfind(' ', 0, sp)
            else:
                sp = window.rfind(' ')
                chunk = (window[:sp] if sp > 80 else window[:DESC_MAX - 1]).rstrip(' ,;:-')
                text = (chunk if chunk.endswith('.') else chunk + '.')[:DESC_MAX]
    return text


def one_line(text):
    return re.sub(r'\s+', ' ', str(text or '')).strip()


def place_desc(pro, place, what):
    """Description for a nation or city fee page. Figures are not invented here."""
    return fit_meta(f'Compare {pro} in {place} for {what}.')


def firm_title(name, town):
    """Short enough for page_title to keep the whole phrase, then add the brand if it fits."""
    for base in (f'{name} in {town}', f'{name} fees', name):
        if len(base) <= TITLE_MAX:
            return base
    return name


def firm_desc(name, town):
    text = f'Fees and contact details for {name} in {town}.'
    extras = (
        'Totals include VAT and the disbursements the firm has listed.',
        'Prices are set by the firm.',
        'Compare them with other solicitors nearby.',
        'Ask the firm to confirm the quote.',
        'Check what is included.',
        'Compare fees.',
        'Fees vary.',
    )
    guard = 0
    while len(text) < DESC_MIN and guard < 8:
        guard += 1
        added = False
        for extra in extras:
            trial = f'{text} {extra}'
            if len(trial) <= DESC_MAX:
                text = trial
                added = True
                if len(text) >= DESC_MIN:
                    break
        if not added or len(text) >= DESC_MIN:
            break
    return fit_meta(text)

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
SVC_NAMES = {  # mirrors SERVICES in src/app.html
    'purchase': 'Buying a home', 'sale': 'Selling a home', 'remortgage': 'Remortgage',
    'will1': 'Single will', 'will2': 'Mirror wills for a couple',
    'poa': {'EW': 'Lasting power of attorney', 'SC': 'Power of attorney', 'NI': 'Enduring power of attorney'},
    'grant': {'EW': 'Grant of probate only', 'SC': 'Confirmation only', 'NI': 'Grant of probate only'},
    'estate': {'EW': 'Full estate administration', 'SC': 'Full executry administration', 'NI': 'Full estate administration'},
    'divorce': 'Uncontested divorce',
    'finances': {'EW': 'Financial consent order', 'SC': 'Minute of agreement', 'NI': 'Financial consent order'},
    'children': 'Child arrangements: first consultation',
}
BANDED = {'purchase', 'sale', 'remortgage'}
svc_name = lambda sid, jur: SVC_NAMES[sid] if isinstance(SVC_NAMES[sid], str) else SVC_NAMES[sid][jur]


def lastmod(*firm_lists):
    dates = [TEMPLATE_DATE] + [str(f.get('updated_at') or '')[:10] for fl in firm_lists for f in fl if f.get('updated_at')]
    return max(dates)


def firm_slug(f):
    return f"{slugify(f.get('name') or 'firm')}-{str(f.get('id'))[:6]}"


# Listings go live without approval, so keep obvious test or placeholder accounts
# (name or town like "Test", "asdf", "xxx") off the public pages and the sitemap.
PLACEHOLDER = re.compile(r'^\s*(test\w*|asdf\w*|qwerty\w*|x{2,}|n/?a|none|tbc|-+)?\s*$', re.I)


def is_placeholder(f):
    return any(f.get(k) and PLACEHOLDER.match(str(f[k])) for k in ('name', 'town')) or \
        bool(re.search(r'\btest\b', f.get('name') or '', re.I))


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
    q = urllib.parse.urlencode({'select': 'id,name,nation,town,address,postcode,phone,website,reg_no,about,color,logo_url,remote,vat,services,lat,lng,listed,sample,updated_at',
                                'listed': 'eq.true', 'sample': 'eq.false'})
    req = urllib.request.Request(f'{SB_URL}/rest/v1/firms?{q}', headers={'apikey': SB_KEY, 'Authorization': f'Bearer {SB_KEY}'})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            rows = json.load(r)
        kept = [f for f in rows if not is_placeholder(f)]
        print(f'sitegen: {len(rows)} live firms from Supabase, {len(rows) - len(kept)} test/placeholder listings skipped')
        return kept
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
FONT = '/fonts/fonts.css'
LOGO = '<svg width="34" height="34" viewBox="0 0 64 64" aria-hidden="true"><rect width="64" height="64" rx="17" fill="#0E6B4F"/><g fill="none" stroke="#fff" stroke-width="5" stroke-linecap="round"><path d="M32 17v29"/><path d="M23 47h18"/><path d="M13 24l38-8"/></g><rect x="7" y="33" width="18" height="6" rx="3" fill="#FFC94A"/><rect x="40" y="25" width="18" height="6" rx="3" fill="#fff"/></svg>'


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
    title = page_title(title)
    desc = fit_meta(desc)
    nav = [('Compare fees', '/#results', 'compare'), ('Guides', '/blog/', 'blog'), ('Find a solicitor', '/solicitors/', 'local'), ('For law firms', '/#firms', 'firms')]
    navh = ''.join(f'<a href="{u}"{" aria-current=page" if k == active else ""}{" class=hide-sm" if k in ("compare", "local") else ""}>{t}</a>' for t, u, k in nav)
    esc_ld = lambda x: json.dumps(x, ensure_ascii=False).replace('</', '<' + chr(92) + '/')
    ld = (organization_node(), website_node(), *ld)
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
<meta property="og:image" content="{OG_IMAGE}">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="Compare Legal">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{e(title)}">
<meta name="twitter:description" content="{e(desc)}">
<meta name="twitter:image" content="{OG_IMAGE}">
<link rel="alternate" type="application/rss+xml" title="Compare Legal guides" href="/blog/feed.xml">
<link rel="icon" href="/favicon.ico" sizes="48x48">
<link rel="icon" type="image/png" sizes="96x96" href="/favicon-96.png">
<link rel="icon" type="image/png" sizes="192x192" href="/favicon-192.png">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<meta name="theme-color" content="#0E6B4F">
<link rel="preload" href="/fonts/figtree-latin-400-normal.woff2" as="font" type="font/woff2" crossorigin>
<link rel="preload" href="/fonts/bricolage-grotesque-latin-opsz-normal.woff2" as="font" type="font/woff2" crossorigin>
<link rel="stylesheet" href="{FONT}">
<link rel="stylesheet" href="/assets/site.css?v={TODAY:%Y%m%d}">
<script src="/consent.js" defer></script>
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
  <div><b>{LOGO.replace('width="34" height="34"', 'width="28" height="28"')}Compare Legal</b><p>Compare solicitors' fees for conveyancing, wills, probate and family law across England, Wales, Scotland and Northern Ireland. Fees are set by each firm; always confirm the final quote before you instruct them.</p></div>
  <div><h2>Fees by service</h2><ul>{foot_links}</ul></div>
  <div><h2>Guides</h2><ul><li><a href="/blog/">All guides</a></li><li><a href="/solicitors/">Solicitors by city</a></li><li><a href="/#firms">List your firm</a></li><li><a href="/about/">About Compare Legal</a></li><li><a href="#cookies" data-cookie-settings>Cookie settings</a></li></ul></div>
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
        posts.append(dict(slug=f.stem, title=meta['title'], seo_title=meta.get('seo_title') or meta['title'],
                          desc=meta['description'], date=meta['date'],
                          updated=meta.get('updated', meta['date']), cat=meta['category'], nations=nations,
                          svc=meta.get('service', AREAS[meta['category']]['svc']), summary=meta.get('summary'), html=html_body, heads=heads, faqs=faqs,
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


def related_posts(post, posts, limit=3):
    """Guides that share a category and one or more nations, then the closest remainder."""
    nations = set(post['nations'])
    ranked = []
    for other in posts:
        if other['slug'] == post['slug']:
            continue
        same_cat = other['cat'] == post['cat']
        shared_nations = set(other['nations']) & nations
        shared = len(shared_nations)
        if not same_cat and not shared:
            continue
        # Prefer the same category, then guides aimed at the same nations
        # rather than a broader or narrower set.
        specificity = shared / max(len(other['nations']), 1)
        coverage = shared / max(len(nations), 1)
        ranked.append((bool(same_cat and shared), same_cat, specificity, coverage, shared, other['updated'], other))
    ranked.sort(key=lambda row: row[:-1], reverse=True)
    return [row[-1] for row in ranked[:limit]]


def guides_by_nation(area, posts):
    blocks = []
    for nation, info in NATIONS.items():
        group = sorted((p for p in posts if nation in p['nations']), key=lambda p: p['title'])
        if not group:
            continue
        items = ''.join(f'<li><a href="{p["url"]}">{e(p["title"])}</a></li>' for p in group)
        blocks.append(
            f'<div><h3>{e(nation)}</h3>'
            f'<p class="muted"><a href="/{area}/{info["slug"]}/">Compare fees in {e(nation)}</a></p>'
            f'<ul>{items}</ul></div>')
    return f'<div class="nationguides">{"".join(blocks)}</div>'


# Category hubs. Copy stays qualitative, or repeats a figure already published in a guide.
# Conveyancing: £4,600 LBTT on a £300,000 home, and first-time buyer SDLT relief to £300,000.
# Family: court fee £628 from July 2026 and a £62 consent order.
HUBS = {
    'conveyancing': {
        'desc': 'What conveyancing costs in England, Wales, Scotland and Northern Ireland, how quotes are built, and how to compare solicitors on the total, not the headline.',
        'paras': [
            'Conveyancing is the legal work of buying, selling or remortgaging a home. The figure that matters is the total, not the headline legal fee. A quote has three parts: the firm\'s own fee, VAT on that fee, and disbursements (outlays in Scotland and Northern Ireland), such as searches and registration fees. Property tax is the same whichever firm you use, so compare the legal total.',
            'England and Wales share a legal system: searches, exchange of contracts, completion and registration at HM Land Registry. Buyers in England pay Stamp Duty Land Tax. Buyers in Wales pay Land Transaction Tax, which has a higher nil-rate band and no separate first-time buyer relief. Any solicitor or licensed conveyancer qualified in England and Wales can act, wherever the office is.',
            'Scotland is different. Sellers usually provide a Home Report, many homes sell by offers over with a closing date, and the contract is formed by missives, which can bind you weeks before the date of entry. Buyers pay Land and Buildings Transaction Tax. On a £300,000 home, someone who is not a first-time buyer pays £4,600. The solicitor must be qualified in Scots law.',
            'Northern Ireland has its own property law and its own profession, regulated by the Law Society of Northern Ireland. Buyers pay Stamp Duty Land Tax at England\'s rates, and first-time buyers pay nothing on the first £300,000. Most titles are at Land Registry NI; some older ones are still in the Registry of Deeds, which means more work. A firm qualified only in England and Wales cannot act.',
            'Leasehold, new builds, shared ownership and unregistered titles push a quote up. A remortgage is simpler, and you only need a conveyancer if you are switching lender. Compare totals, ask whether the fee is fixed, and ask what would make it rise. The guides below are grouped by nation. Firms set their own prices, and you can <a href="/conveyancing/">compare conveyancing fees</a> with VAT and disbursements included.',
        ],
        'faqs': [
            ('What should a conveyancing quote include?',
             'The firm\'s legal fee, VAT at 20% on that fee, and disbursements such as searches and registration fees. Compare that total. Stamp duty, or the equivalent property tax where the home is, is collected by the conveyancer but is the same whichever firm you use.'),
            ('Why do conveyancing fees differ between firms?',
             'Firms set their own prices. Leasehold, new builds, shared ownership and unregistered titles add work, so two quotes for the same street can still differ. A very low fee can mean a high caseload. Ask who will do the work and what would make the fee rise.'),
            ('Does the conveyancer have to be based nearby?',
             'In England and Wales, any solicitor or licensed conveyancer qualified there can act, wherever the office is. In Scotland and Northern Ireland the solicitor must be qualified in that jurisdiction. A firm from England cannot act on a Scottish or Northern Ireland property.'),
        ],
    },
    'family-law': {
        'desc': 'What an uncontested divorce costs in England, Wales, Scotland and Northern Ireland, from court fees to solicitors\' fees, and how to compare like for like.',
        'paras': [
            'The cost of a divorce depends far less on ending the marriage than on whether you agree about money and children. The divorce itself is fairly standard, and many firms offer an uncontested case for a fixed fee. Dividing the home, savings and pensions is separate. In England, Wales and Northern Ireland that agreement binds you only when a court approves a consent order. In Scotland it is usually a minute of agreement. Disputes are charged by the hour, which is where costs climb. Mediation is often much cheaper than a contested hearing.',
            'England and Wales have had no-fault divorce since April 2022: you confirm the marriage has broken down, with no need to blame anyone. There is a minimum 20-week wait before the conditional order and at least six weeks and a day before the final order, so an uncontested divorce usually takes six to eight months. The court fee is £628 from July 2026, and a consent order costs a further £62. You can apply online yourself. A solicitor\'s fixed fee usually covers the application and each order.',
            'Scotland allows divorce after one year\'s separation with consent, or two years without it. Where there are no children under 16 and no financial claims, a cheaper simplified procedure may be available. Other cases use the ordinary procedure, usually with a solicitor.',
            'Northern Ireland has not adopted no-fault divorce. You cannot petition until two years after the marriage, and you need one of five facts, most often separation. Even an undefended case normally includes a short hearing.',
            'When you compare firms, check whether the quote is the divorce only or also the financial agreement, whether the fee is fixed, and what happens if agreement fails. A solicitor usually acts for only one of you. The guide below covers each nation, and you can <a href="/family-law/">compare family law fees</a> with VAT and disbursements included.',
        ],
        'faqs': [
            ('How much does a divorce cost?',
             'In England and Wales the court fee is £628 from July 2026, and a consent order to make a financial agreement binding is a further £62. Solicitors\' fees are on top. An agreed divorce is often a fixed fee. Disputes about money or children are usually charged by the hour.'),
            ('Is the financial agreement included in a divorce quote?',
             'Not always. The divorce ends the marriage. Sharing the home, savings and pensions is separate work: a consent order in England, Wales and Northern Ireland, or a minute of agreement in Scotland. Check which of those the quote covers.'),
            ('Are the rules the same across the UK?',
             'No. England and Wales use no-fault divorce, with a minimum 20-week wait. Scotland uses periods of separation, and a simpler procedure where there are no children under 16 and no financial claims. Northern Ireland has not adopted no-fault divorce, and a petition cannot be brought until two years after the marriage.'),
        ],
    },
}


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
    write('/blog/', shell(title='Legal fees guides',
                          desc='Plain-English guides to what conveyancing, wills, probate and divorce cost in England, Wales, Scotland and Northern Ireland, and how the rules differ.',
                          path='/blog/', body=body, ld=ld, active='blog'))
    # categories. Conveyancing and family law are written hubs; the others stay as listings.
    cat_desc = {
        'wills': 'How wills and powers of attorney differ across England and Wales, Scotland and Northern Ireland, from witnessing rules to which power of attorney you need.',
        'probate': 'Court fees for probate and confirmation in England and Wales, Scotland and Northern Ireland, how solicitors charge, and when an estate may not need a grant.',
    }
    for k, name in CATS.items():
        ps = [p for p in posts if p['cat'] == k]
        crumbs = [('Home', '/'), ('Guides', '/blog/'), (name, f'/blog/category/{k}/')]
        hub = HUBS.get(k)
        if hub:
            intro = ''.join(f'<p>{para}</p>' for para in hub['paras'])
            body = f'''<section class="pagehead"><div class="wrap">
  {crumbs_html(crumbs)}
  <h1>{e(name)} guides</h1>
  <div class="prose hubintro">{intro}</div>
</div></section>
<section class="band"><div class="wrap">
  <h2>Guides by nation</h2>
  <p class="muted sub">Guides that cover more than one nation are listed under each nation they apply to.</p>
  {guides_by_nation(k, ps)}
</div></section>
{faq_band(hub['faqs'])}
{local_band(k, cities, f'Compare {AREAS[k]["name"].lower()} fees by city')}'''
            ld = [crumbs_ld(crumbs), faq_ld(hub['faqs'])]
            desc = hub['desc']
        else:
            body = f'''<section class="pagehead"><div class="wrap">
  {crumbs_html(crumbs)}
  <h1>{e(name)} guides</h1>
  <p class="lede">Guides on {e(AREAS[k]['what'])}: costs, the process, and how the rules differ across the UK.</p>
</div></section>
<section class="band"><div class="wrap"><div class="grid">{''.join(post_card(p) for p in ps) or '<p class="muted">New guides are on the way.</p>'}</div></div></section>
{local_band(k, cities, f'Compare {AREAS[k]["name"].lower()} fees by city')}'''
            ld = [crumbs_ld(crumbs)]
            desc = cat_desc.get(k) or f'Guides on {AREAS[k]["what"]} in England, Wales, Scotland and Northern Ireland, including how the rules differ and how to compare solicitors.'
        write(f'/blog/category/{k}/', shell(title=f'{name} guides', desc=desc,
                                            path=f'/blog/category/{k}/', body=body, ld=ld, index=bool(ps), active='blog'))
    # posts
    for p in posts:
        related = related_posts(p, posts)
        toc = ''.join(f'<li><a href="#{k}">{e(t)}</a></li>' for lvl, k, t in p['heads'] if lvl == 2)
        crumbs = [('Home', '/'), ('Guides', '/blog/'), (CATS[p['cat']], f'/blog/category/{p["cat"]}/'), (p['short'], p['url'])]
        cta_nation = p['nations'][0] if len(p['nations']) == 1 else None
        area = AREAS[p['cat']]
        body = f'''<article class="post">
<header class="pagehead"><div class="wrap narrow">
  {crumbs_html(crumbs)}
  <h1>{e(p['title'])}</h1>
  <p class="lede">{e(p['desc'])}</p>
  <p class="meta">{byline()} · Updated <time datetime="{p['updated']}">{nice_date(p['updated'])}</time> · {p['mins']} min read</p>
</div></header>
<div class="wrap postgrid">
  <div class="prose">
    {f'<div class="answer"><p class="eyebrow">Short answer</p><p>{e(p["summary"])}</p></div>' if p.get('summary') else ''}
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
            'mainEntityOfPage': BASE + p['url'], 'url': BASE + p['url'], 'image': OG_IMAGE,
            'author': {'@id': ORG_ID},
            **({'reviewedBy': person_ld(), 'lastReviewed': p['updated']} if REVIEWER else {}),
            **({'abstract': p['summary']} if p.get('summary') else {}),
            'publisher': {'@id': ORG_ID},
            'about': CATS[p['cat']], 'spatialCoverage': [{'@type': 'Place', 'name': n} for n in p['nations']]}]
        if p['faqs']:
            ld.append(faq_ld(p['faqs']))
        write(p['url'], shell(title=p['seo_title'], desc=p['desc'], path=p['url'], body=body, ld=ld, og_type='article', active='blog'))
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
  <span class="fname"><a href="/firms/{firm_slug(f)}/"><b>{e(f.get('name'))}</b></a><small>{e(f.get('town') or '')}{dist}</small></span>
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
    pages = []  # (path, indexable, lastmod)
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
        area_desc = {
            'conveyancing': 'Compare conveyancing fees for buying, selling or remortgaging across England, Wales, Scotland and Northern Ireland. Totals include VAT and disbursements.',
            'wills': "Compare solicitors' fees for a will or power of attorney in England, Wales, Scotland and Northern Ireland. Totals include VAT, and firms set their own prices.",
            'probate': 'Compare probate and confirmation fees across England, Wales, Scotland and Northern Ireland, for a grant only or full estate administration. Totals include VAT.',
            'family-law': "Compare solicitors' fees for an uncontested divorce in England, Wales, Scotland and Northern Ireland. Totals include VAT and the disbursements firms list.",
        }[area]
        write(f'/{area}/', shell(title=f'Compare {A["name"]} fees',
                                 desc=area_desc,
                                 path=f'/{area}/', body=body, ld=[crumbs_ld(crumbs)], active='local'))
        pages.append((f'/{area}/', True, lastmod([f for f in firms if offers_area(f, area)])))
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
            write(f'/{area}/{N["slug"]}/', shell(title=f'{pro.capitalize()} in {n}',
                                                desc=place_desc(pro, n, A['what']),
                                                path=f'/{area}/{N["slug"]}/', body=body, ld=[crumbs_ld(ncrumbs), faq_ld(faqs)], active='local'))
            pages.append((f'/{area}/{N["slug"]}/', True, lastmod(nfirms)))
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
                write(path, shell(title=f'{pro.capitalize()} in {c["name"]}',
                                  desc=place_desc(pro, c['name'], A['what']),
                                  path=path, body=body, ld=ld, index=indexable, active='local'))
                pages.append((path, indexable, lastmod(near)))
    # A–Z hub
    blocks = ''.join(f'<section class="band{" alt" if i % 2 else ""}"><div class="wrap"><h2><a href="/{a}/">{e(A["name"])}</a></h2><div class="cityblocks">{city_links(a, list(NATIONS), cities)}</div></div></section>' for i, (a, A) in enumerate(AREAS.items()))
    crumbs = [('Home', '/'), ('Find a solicitor', '/solicitors/')]
    body = f'''<section class="pagehead"><div class="wrap">{crumbs_html(crumbs)}<h1>Find a solicitor near you</h1>
<p class="lede">Compare fees from solicitors in {sum(len(v) for v in cities.values())} cities across England, Wales, Scotland and Northern Ireland.</p></div></section>{blocks}'''
    write('/solicitors/', shell(title='Find a solicitor near you',
                                desc='Find and compare conveyancing, wills, probate and divorce solicitors by city across England, Wales, Scotland and Northern Ireland. Totals include VAT.',
                                path='/solicitors/', body=body, ld=[crumbs_ld(crumbs)], active='local'))
    pages.append(('/solicitors/', True, lastmod(firms)))
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


# ---------------------------------------------------------------- firm profiles
def tier_totals(f, sid):
    s = f['services'][sid]
    disb = sum(num(d.get('amount')) for d in (s.get('disb') or []))
    out, prev = [], None
    for t_ in tiers_of(s):
        fee = num(t_['fee'])
        if fee:
            vat = 0 if f.get('vat') is False else fee * 0.2
            mx = t_['max']
            lab = ('Any price' if prev is None else f'Over {gbp(prev)}') if mx is None else (f'Up to {gbp(mx)}' if prev is None else f'{gbp(prev + 1)} to {gbp(mx)}')
            out.append((lab, fee + vat + disb))
        prev = t_['max']
    return out


def firm_services_html(f, jur):
    rows = []
    for sid in SVC_NAMES:
        s = (f.get('services') or {}).get(sid)
        if not s:
            continue
        typ = {'fixed': 'Fixed fee', 'from': 'Starting price', 'hourly': 'Hourly rate'}.get(s.get('type'), '')
        if s.get('type') == 'hourly':
            price = f'{gbp(num(s.get("rate")))} an hour + VAT' if num(s.get('rate')) else 'Ask for a quote'
        elif sid in BANDED:
            tt = tier_totals(f, sid)
            price = '<br>'.join(f'{e(l)}: <b>{gbp(v)}</b>' for l, v in tt) if tt else 'Ask for a quote'
        else:
            q = quote(f, sid)
            price = f'<b>{gbp(q["total"])}</b>' if q and q['total'] else 'Ask for a quote'
        inc = s.get('inc') or ''
        time = s.get('time') or ''
        extra = ''.join(x for x in (f'<p class="muted small">{e(inc)}</p>' if inc else '', f'<p class="muted small">Typical timescale: {e(time)}</p>' if time else ''))
        rows.append(f'<tr><th scope="row">{e(svc_name(sid, jur))}<br><small class="muted">{e(typ)}</small>{extra}</th><td class="num">{price}</td></tr>')
    return ''.join(rows)


def build_firms(firms, cities):
    pages = []
    for f in firms:
        n = f.get('nation')
        if n not in NATIONS or not f.get('name'):
            continue
        N, jur = NATIONS[n], NATIONS[n]['jur']
        slug = firm_slug(f)
        path = f'/firms/{slug}/'
        rows = firm_services_html(f, jur)
        near_city = None
        if f.get('lat') is not None:
            near_city = min(cities[n], key=lambda c: miles(c['lat'], c['lng'], float(f['lat']), float(f['lng'])))
        areas = [a for a in AREAS if offers_area(f, a)]
        crumbs = [('Home', '/'), ('Find a solicitor', '/solicitors/'), (f['name'], path)]
        town = f.get('town') or (near_city['name'] if near_city else n)
        logo = f'<img class="flogo" src="{e(f["logo_url"])}" alt="{e(f["name"])} logo" width="72" height="72">' if f.get('logo_url') else \
            f'<span class="ini big" style="background:{e(f.get("color") or "#24507A")}" aria-hidden="true">{e("".join(w[0] for w in re.sub("&", "", f["name"]).split() if w[:1].isalpha())[:2].upper())}</span>'
        web = f.get('website') or ''
        if web and not web.startswith('http'):
            web = 'https://' + web
        web_label = re.sub(r'^https?://(www\.)?', '', web).rstrip('/')
        phone_href = re.sub(r'[^0-9+]', '', f.get('phone') or '')
        no_prices = '<p class="muted">This firm hasn&#39;t listed prices yet.</p>'
        contact = ''.join(x for x in [
            f'<li><span class="eyebrow">Address</span>{e(", ".join(p for p in [f.get("address"), f.get("town"), f.get("postcode")] if p))}</li>' if (f.get('address') or f.get('postcode')) else '',
            f'<li><span class="eyebrow">Phone</span><a href="tel:{e(phone_href)}">{e(f["phone"])}</a></li>' if f.get('phone') else '',
            f'<li><span class="eyebrow">Website</span><a href="{e(web)}" rel="nofollow noopener" target="_blank">{e(web_label)}</a></li>' if web else '',
            f'<li><span class="eyebrow">Regulated by</span>{e(N["reg"])}{(" · " + e(f["reg_no"])) if f.get("reg_no") else ""}</li>',
        ])
        upd = (f.get('updated_at') or '')[:10]
        body = f'''<section class="pagehead"><div class="wrap">
  {crumbs_html(crumbs)}
  <div class="fhead">{logo}<div><h1>{e(f['name'])}</h1><p class="lede">Solicitors in {e(town)}, {e(n)}{' · acts for clients across ' + e(JUR_LABEL[jur]) + ' remotely' if f.get('remote') else ''}</p></div></div>
  {f'<p class="about">{e(f["about"])}</p>' if f.get('about') else ''}
  <a class="btn" href="{app_link(AREAS[areas[0]]['svc'] if areas else 'purchase', n, near_city['name'] if near_city else None)}">Compare with firms near {e(near_city['name'] if near_city else n)}</a>
</div></section>
<section class="band"><div class="wrap two">
  <div><h2>Fees</h2><p class="muted sub">Totals include VAT and the disbursements the firm has listed{(', updated ' + nice_date(upd)) if upd else ''}. Prices are set by the firm; confirm your quote before you instruct them.</p>
  {f'<div class="tbl"><table><tbody>{rows}</tbody></table></div>' if rows else no_prices}</div>
  <div><h2>Contact</h2><ul class="contact">{contact}</ul>
  {('<h2 class="mt">Nearby</h2><ul class="cities">' + ''.join(f'<li><a href="/{a}/{N["slug"]}/{near_city["slug"]}/">{e(AREAS[a]["pro"][jur].capitalize())} in {e(near_city["name"])}</a></li>' for a in areas) + '</ul>') if near_city and areas else ''}</div>
</div></section>'''
        ld = [crumbs_ld(crumbs), {k: v for k, v in {
            '@context': 'https://schema.org', '@type': 'LegalService', 'name': f['name'], 'url': BASE + path,
            'sameAs': web or None, 'telephone': f.get('phone') or None, 'image': f.get('logo_url') or None,
            'description': f.get('about') or None, 'areaServed': JUR_LABEL[jur],
            'address': {k2: v2 for k2, v2 in {'@type': 'PostalAddress', 'streetAddress': f.get('address') or None, 'addressLocality': f.get('town') or None,
                                               'postalCode': f.get('postcode') or None, 'addressCountry': 'GB'}.items() if v2},
            'geo': {'@type': 'GeoCoordinates', 'latitude': f['lat'], 'longitude': f['lng']} if f.get('lat') is not None else None}.items() if v}]
        indexable = bool(rows) and any(quote(f, sid) for sid in SVC_NAMES)
        write(path, shell(title=firm_title(f['name'], town),
                          desc=firm_desc(f['name'], town),
                          path=path, body=body, ld=ld, index=indexable, active='local'))
        pages.append((path, indexable, lastmod([f])))
    return pages


def byline():
    if REVIEWER:
        return f'Reviewed by <a href="/about/#reviewer">{e(REVIEWER["name"])}</a>, {e(REVIEWER["job"].lower())}'
    return 'By the Compare Legal team'


def person_ld():
    return {k: v for k, v in {'@type': 'Person', 'name': REVIEWER['name'], 'jobTitle': REVIEWER['job'],
                              'url': BASE + '/about/#reviewer', 'sameAs': REVIEWER.get('url')}.items() if v}


def build_about(posts, firms):
    n_cities = len(load_cities_flat())
    reviewer = f'''<section class="band" id="reviewer"><div class="wrap narrow prose"><h2>Who checks the guides</h2>
<p><b>{e(REVIEWER["name"])}</b>, {e(REVIEWER["job"].lower())}. {e(REVIEWER.get("bio", ""))}</p></div></section>''' if REVIEWER else ''
    contact = f'<p>Spotted a mistake, or want a listing checked? Email <a href="mailto:{e(CONTACT_EMAIL)}">{e(CONTACT_EMAIL)}</a> and we will look at it within two working days.</p>' if CONTACT_EMAIL else \
        '<p>Spotted a mistake? Firms can correct their own listing at any time from the firm portal.</p>'
    crumbs = [('Home', '/'), ('About', '/about/')]
    body = f'''<section class="pagehead"><div class="wrap narrow">
  {crumbs_html(crumbs)}
  <h1>About Compare Legal</h1>
  <p class="lede">Compare Legal is a free, independent price comparison site for high-street legal services in the UK: conveyancing, wills and powers of attorney, probate and estates, and family law. It covers England, Wales, Scotland and Northern Ireland.</p>
</div></section>
<section class="band"><div class="wrap narrow prose">
  <h2>Why it exists</h2>
  <p>Most people instruct a solicitor only a few times in their life, usually at a stressful moment, and quotes are hard to compare. One firm quotes a low legal fee and lists searches and registration fees separately; another quotes a single total. Compare Legal shows every firm's price in the same format, with VAT and disbursements added in, so you can compare like with like before you get in touch.</p>
  <h2>How prices are collected</h2>
  <ul>
    <li><b>Firms set their own prices.</b> Each firm enters its fees, value bands and disbursements in the Compare Legal firm portal and can change them at any time. The date a listing was last updated is shown on the firm's page.</li>
    <li><b>Totals are worked out the same way for everyone.</b> We add VAT to the legal fee where the firm charges it, then add the disbursements the firm has listed. For conveyancing, the fee is taken from the firm's band for the property price you enter.</li>
    <li><b>Quotes are a guide, not a contract.</b> A firm's written quote and terms of business, given after it knows the details of your matter, are what count. Always confirm the final price before you instruct a firm.</li>
    <li><b>Sample firms are labelled.</b> While the site is new, some listings in the comparison tool are clearly marked as sample firms with illustrative prices. They never appear on the city or firm pages.</li>
  </ul>
  <h2>How firms are listed</h2>
  <ul>
    <li>Firms can list for free.</li>
    <li>Compare Legal does not take a referral fee or commission from firms for enquiries.</li>
    <li>We only show firms qualified in the jurisdiction your matter is in, because property and succession law differ between England and Wales, Scotland and Northern Ireland.</li>
  </ul>
  <h2>Where we cover</h2>
  <p>Fee pages for {len(AREAS)} services in {n_cities} cities across all four UK nations, and {len(posts)} guides explaining what each service costs and how the law differs between the nations. A city page is only offered to search engines once at least {LOCAL_INDEX_MIN_FIRMS} real firms are listed within {LOCAL_RADIUS_MILES} miles, so you won't land on an empty page from a search.</p>
  <h2>How the guides are written</h2>
  <p>Every guide shows the date it was last updated and links to official sources for fees, tax rates and court charges, such as GOV.UK, nidirect, Revenue Scotland, the Welsh Revenue Authority and the regulators. We review guides when official figures change. Guides are general information, not legal advice for your situation.</p>
  {contact}
</div></section>
{reviewer}'''
    ld = [crumbs_ld(crumbs), {'@context': 'https://schema.org', '@type': 'AboutPage', 'url': BASE + '/about/', 'name': 'About Compare Legal',
                              'about': {'@id': ORG_ID}, 'inLanguage': 'en-GB'}]
    if REVIEWER:
        ld.append({'@context': 'https://schema.org', **person_ld()})
    write('/about/', shell(title="About Compare Legal: how we compare solicitors' fees", path='/about/', body=body, ld=ld,
                           desc="How Compare Legal collects solicitors' fees, adds VAT, and lists firms across England, Wales, Scotland and Northern Ireland. Free to use, with no referral fee."))


def load_cities_flat():
    return [c for cs in load_cities().values() for c in cs]


def build_llms(posts):
    lines = ['# Compare Legal', '',
             '> Independent price comparison for UK high-street legal services: conveyancing, wills and powers of attorney, probate and estates, and family law, in England, Wales, Scotland and Northern Ireland. Firms set their own prices; totals include VAT and disbursements.', '',
             'Guides are dated and link to official sources for fees and tax rates. They are general information, not legal advice.', '',
             '## Guides']
    lines += [f'- [{p["title"]}]({BASE}{p["url"]}): {one_line(p.get("summary") or p["desc"])}' for p in posts]
    lines += ['', '## Compare fees by service']
    for a, A in AREAS.items():
        lines.append(f'- [{A["name"]}]({BASE}/{a}/): ' + ', '.join(f'[{n}]({BASE}/{a}/{N["slug"]}/)' for n, N in NATIONS.items()))
    lines += ['', '## About', f'- [About Compare Legal and how prices are compared]({BASE}/about/)', f'- [Compare fees (interactive)]({BASE}/)', '']
    (PUBLIC / 'llms.txt').write_text('\n'.join(lines))


def build_404():
    body = '''<section class="pagehead"><div class="wrap"><h1>Page not found</h1>
<p class="lede">That page doesn't exist or has moved. New firm listings can take a day to appear here.</p>
<a class="btn" href="/#results">Compare fees</a> <a class="btn ghost" href="/solicitors/">Find a solicitor by city</a></div></section>'''
    (PUBLIC / '404.html').write_text(shell(title='Page not found', desc="This page doesn't exist or has moved. Compare solicitors' fees for conveyancing, wills, probate and family law, or read the guides to what each service costs.", path='/404.html', body=body, index=False))


# ---------------------------------------------------------------- sitemap etc.
def build_meta(posts, pages):
    newest = lambda ps: max([TEMPLATE_DATE] + [p['updated'] for p in ps])
    urls = [('/', newest(posts), '1.0'), ('/blog/', newest(posts), '0.8'), ('/about/', TEMPLATE_DATE, '0.5')]
    urls += [(f'/blog/category/{k}/', newest([p for p in posts if p['cat'] == k]), '0.5') for k in CATS if any(p['cat'] == k for p in posts)]
    urls += [(p['url'], p['updated'], '0.7') for p in posts]
    urls += [(u, d, '0.6') for u, ok, d in pages if ok]
    body = ''.join(f'<url><loc>{BASE}{u}</loc><lastmod>{d}</lastmod><priority>{pr}</priority></url>' for u, d, pr in urls)
    (PUBLIC / 'sitemap.xml').write_text(f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>\n')
    (PUBLIC / 'robots.txt').write_text(f'User-agent: *\nAllow: /\nDisallow: /vendor/\n\nSitemap: {BASE}/sitemap.xml\n')
    (PUBLIC / 'favicon.svg').write_text(LOGO.replace('width="34" height="34" ', 'xmlns="http://www.w3.org/2000/svg" '))
    assets = PUBLIC / 'assets'
    assets.mkdir(exist_ok=True)
    shutil.copy(ROOT / 'site' / 'site.css', assets / 'site.css')
    return len(urls)


GENERATED = ['blog', 'solicitors', 'assets', 'firms', 'about', *AREAS]


def main():
    for d in GENERATED:
        shutil.rmtree(PUBLIC / d, ignore_errors=True)
    cities = load_cities()
    firms = fetch_firms()
    posts = load_posts()
    build_blog(posts, cities)
    pages = build_local(cities, firms, posts)
    pages += build_firms(firms, cities)
    build_404()
    build_about(posts, firms)
    build_llms(posts)
    n = build_meta(posts, pages)
    idx = sum(1 for _, ok, _d in pages if ok)
    print(f'sitegen: {len(posts)} posts, {len(pages)} local and firm pages ({idx} indexable), {n} URLs in sitemap')
    audit_public(posts)


def _banned(text):
    """True if text names a person who must not appear on the site. The parts are split so the source does not contain those names."""
    low = text.lower()
    parts = ('bra' + 'den', 'jon' + 'ny', 'jona' + 'than')
    return any(part in low for part in parts)


def audit_public(posts):
    """Fail the build if a published page breaks the SEO limits or names a person who must not appear."""
    bad = []
    html_files = sorted(PUBLIC.rglob('*.html'))
    for path in html_files:
        text = path.read_text()
        rel = str(path.relative_to(PUBLIC))
        if _banned(text):
            bad.append(f'{rel}: contains a disallowed name')
        titles = re.findall(r'<title>(.*?)</title>', text, re.S)
        if len(titles) != 1:
            bad.append(f'{rel}: expected one title, found {len(titles)}')
        else:
            title = html.unescape(titles[0])
            if len(title) > TITLE_MAX:
                bad.append(f'{rel}: title {len(title)} > {TITLE_MAX}: {title}')
        descs = re.findall(r'<meta name="description" content="(.*?)">', text)
        if len(descs) != 1:
            bad.append(f'{rel}: expected one meta description, found {len(descs)}')
        else:
            desc = html.unescape(descs[0])
            if not DESC_MIN <= len(desc) <= DESC_MAX:
                bad.append(f'{rel}: description {len(desc)} not {DESC_MIN}-{DESC_MAX}: {desc}')
        canons = re.findall(r'<link rel="canonical" href="(.*?)">', text)
        if len(canons) != 1:
            bad.append(f'{rel}: expected one canonical, found {len(canons)}')
        else:
            href = html.unescape(canons[0])
            if not href.startswith(BASE):
                bad.append(f'{rel}: canonical is not on {BASE}: {href}')
            file_url = rel == '404.html'
            if not file_url and not href.endswith('/'):
                bad.append(f'{rel}: canonical has no trailing slash: {href}')
        if 'property="og:image"' not in text:
            bad.append(f'{rel}: missing og:image')
        if 'name="twitter:card"' not in text:
            bad.append(f'{rel}: missing twitter:card')
        if '"@id": "https://comparelegal.com/#organization"' not in text and '"@id":"https://comparelegal.com/#organization"' not in text:
            bad.append(f'{rel}: missing organization @id')
        if '"@type": "WebSite"' not in text and '"@type":"WebSite"' not in text:
            bad.append(f'{rel}: missing WebSite schema')
    llms = (PUBLIC / 'llms.txt').read_text()
    if _banned(llms):
        bad.append('llms.txt: contains a disallowed name')
    for post in posts:
        needle = f'({BASE}{post["url"]})'
        if needle not in llms:
            bad.append(f'llms.txt: missing {post["url"]}')
            continue
        line = next(ln for ln in llms.splitlines() if needle in ln)
        summary = line.split('): ', 1)[-1].strip() if '): ' in line else ''
        if not summary or '\n' in summary:
            bad.append(f'llms.txt: {post["slug"]} has no one-line summary')
    for key, hub in HUBS.items():
        plain = re.sub(r'<[^>]+>', ' ', ' '.join(hub['paras']))
        count = len(re.findall(r"[A-Za-z0-9£']+", plain))
        print(f'sitegen: {key} hub intro {count} words')
        if not 270 <= count <= 360:
            bad.append(f'{key} hub intro is {count} words, expected about 300')
    if bad:
        raise SystemExit('SEO audit failed:\n' + '\n'.join(bad))
    print(f'sitegen: SEO audit ok ({len(html_files)} pages)')


if __name__ == '__main__':
    main()
