"""Build the static site into public/.

  * Wraps src/app.html (the app body) into a full HTML document -> public/index.html
  * Reads configuration from environment variables and writes public/config.js
  * Copies src/backend.js (the Supabase/Stripe data layer) -> public/backend.js

Live mode (real accounts, subscriptions) switches on when SUPABASE_URL and SUPABASE_ANON_KEY are
set. Without them the site builds in browser-only prototype mode, exactly as before.

Environment variables (all optional):
  SUPABASE_URL          https://<project-ref>.supabase.co
  SUPABASE_ANON_KEY     the PUBLIC anon / publishable key (never the service-role / secret key)
  SITE_URL              default https://comparelegal.com
  PREMIUM_PRICE_LABEL   text shown for the Premium plan, default "£49 per month". Must match Stripe.
  SHOW_SAMPLE_FIRMS     "true" to keep the fictional sample firms in live mode (default false)
"""
import base64
import json
import os
import pathlib
import re
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parent
SRC = ROOT / 'src'
OUT = pathlib.Path(os.environ.get('BUILD_OUT_DIR') or ROOT / 'public')  # BUILD_OUT_DIR is for tests
MARKER = '<!--BACKEND_SCRIPTS-->'


def env(name, default=''):
    return os.environ.get(name, default).strip()


def fail(msg):
    sys.exit(f'build.py: {msg}')


def check_public_key(key):
    """The key ships to every visitor's browser, so refuse anything that looks like a secret."""
    if key.startswith('sb_secret_'):
        fail('SUPABASE_ANON_KEY is a secret key. Use the public anon / publishable key instead.')
    if key.count('.') == 2:  # a JWT: look at its role claim
        try:
            payload = key.split('.')[1]
            payload += '=' * (-len(payload) % 4)
            role = json.loads(base64.urlsafe_b64decode(payload)).get('role')
        except Exception:
            fail('SUPABASE_ANON_KEY does not look like a valid key.')
        if role != 'anon':
            fail(f'SUPABASE_ANON_KEY has role "{role}". Only the public "anon" key may be used in the browser.')
    elif not key.startswith('sb_publishable_'):
        fail('SUPABASE_ANON_KEY does not look like a Supabase anon or publishable key.')


def build_config():
    url = env('SUPABASE_URL').rstrip('/')
    key = env('SUPABASE_ANON_KEY')
    if bool(url) != bool(key):
        fail('set both SUPABASE_URL and SUPABASE_ANON_KEY, or neither (prototype mode).')
    if not url:
        return None
    if not re.fullmatch(r'https://[A-Za-z0-9.-]+(:\d+)?|http://localhost(:\d+)?|http://127\.0\.0\.1(:\d+)?', url):
        fail('SUPABASE_URL must be an origin such as https://abcdefgh.supabase.co (no path).')
    check_public_key(key)
    return {
        'supabaseUrl': url,
        'supabaseAnonKey': key,
        'siteUrl': env('SITE_URL', 'https://comparelegal.com').rstrip('/'),
        'premiumPrice': env('PREMIUM_PRICE_LABEL', '£49 per month'),
        'showSamples': env('SHOW_SAMPLE_FIRMS').lower() == 'true',
    }


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    cfg = build_config()
    src = (SRC / 'app.html').read_text()
    if MARKER not in src:
        fail(f'{MARKER} missing from src/app.html')

    if cfg:
        scripts = '<script src="config.js"></script>\n<script src="vendor/supabase.js"></script>\n<script src="backend.js"></script>'
        (OUT / 'config.js').write_text('window.CL_CONFIG = ' + json.dumps(cfg, ensure_ascii=False) + ';\n')
        shutil.copyfile(SRC / 'backend.js', OUT / 'backend.js')
    else:
        scripts = ''
        for stale in ('config.js', 'backend.js'):
            (OUT / stale).unlink(missing_ok=True)
    src = src.replace(MARKER, scripts)

    head_end = src.index('</style>') + len('</style>')
    head, body = src[:head_end], src[head_end:]
    doc = f"""<!doctype html>
<html lang="en-GB">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta property="og:title" content="Compare Legal">
<meta property="og:description" content="Compare solicitors' fees for conveyancing, wills, probate and family law across the UK.">
<meta property="og:url" content="https://comparelegal.com/">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 30 30'%3E%3Crect x='1' y='1' width='28' height='28' rx='7' fill='%2315212C'/%3E%3Crect x='7' y='9' width='10' height='3' rx='1.5' fill='%23F5F7F6'/%3E%3Crect x='7' y='14' width='16' height='3' rx='1.5' fill='%23FFE07A'/%3E%3Crect x='7' y='19' width='13' height='3' rx='1.5' fill='%23F5F7F6'/%3E%3C/svg%3E">
<style>:root{{padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}}body{{margin:0}}img{{max-width:100%}}[hidden]{{display:none!important}}</style>
{head}
</head>
<body>
{body}
</body>
</html>
"""
    (OUT / 'index.html').write_text(doc)
    mode = 'live (Supabase + Stripe)' if cfg else 'prototype (browser-only)'
    print(f'built public/index.html, {len(doc)} bytes, {mode} mode')


if __name__ == '__main__':
    main()
