"""Wrap src/app.html (artifact body) into a full HTML document for static hosting."""
import pathlib
src = pathlib.Path('src/app.html').read_text()
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
pathlib.Path('public/index.html').write_text(doc)
print('built public/index.html', len(doc), 'bytes')
