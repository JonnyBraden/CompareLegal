"""A small Markdown renderer (stdlib only, so the Render build needs no packages).

Supports: ## / ### headings (with ids), paragraphs, - and 1. lists, > callouts,
| tables |, **bold**, *italic*, [links](url) and --- rules.
"""
import html
import re


def slugify(s):
    s = s.lower().replace('&', ' and ').replace('~', ' ')
    s = re.sub(r"[’']", '', s)
    s = re.sub(r'[^a-z0-9]+', '-', s)
    return s.strip('-')


def inline(text):
    t = html.escape(text, quote=False)
    t = re.sub(r'\[([^\]]+)\]\(([^)\s]+)\)', _link, t)
    t = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', t)
    t = re.sub(r'(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])', r'<em>\1</em>', t)
    return t


def _link(m):
    label, url = m.group(1), m.group(2).replace('&amp;', '&')
    ext = url.startswith('http') and 'comparelegal.com' not in url
    attrs = ' rel="noopener" target="_blank"' if ext else ''
    return f'<a href="{html.escape(url)}"{attrs}>{label}</a>'


def render(src):
    """Return (html, headings, faqs). headings: [(level, id, text)]; faqs: [(q, answer_html)]."""
    lines = src.replace('\r\n', '\n').split('\n')
    out, heads, faqs = [], [], []
    i, n = 0, len(lines)
    in_faq, cur_q, cur_a = False, None, []
    used = set()

    def hid(text):
        base = slugify(text) or 'section'
        k, j = base, 2
        while k in used:
            k, j = f'{base}-{j}', j + 1
        used.add(k)
        return k

    def close_q():
        nonlocal cur_q, cur_a
        if cur_q:
            faqs.append((cur_q, ''.join(cur_a)))
        cur_q, cur_a = None, []

    def emit(h):
        out.append(h)
        if in_faq and cur_q:
            cur_a.append(h)

    while i < n:
        line = lines[i]
        s = line.strip()
        if not s:
            i += 1
            continue
        m = re.match(r'^(#{2,4})\s+(.*)$', s)
        if m:
            lvl, text = len(m.group(1)), m.group(2).strip()
            if lvl == 2:
                close_q()
                in_faq = bool(re.match(r'(?i)^(frequently asked questions|faqs?)\b', text))
            elif lvl == 3 and in_faq:
                close_q()
                cur_q = text
            k = hid(text)
            heads.append((lvl, k, text))
            out.append(f'<h{lvl} id="{k}">{inline(text)}</h{lvl}>')
            i += 1
            continue
        if s == '---':
            emit('<hr>')
            i += 1
            continue
        if s.startswith('|'):
            rows = []
            while i < n and lines[i].strip().startswith('|'):
                rows.append([c.strip() for c in lines[i].strip().strip('|').split('|')])
                i += 1
            body = [r for r in rows if not all(re.fullmatch(r':?-{2,}:?', c) for c in r)]
            if body:
                h = ['<div class="tbl"><table>', '<thead><tr>' + ''.join(f'<th scope="col">{inline(c)}</th>' for c in body[0]) + '</tr></thead><tbody>']
                for r in body[1:]:
                    h.append('<tr>' + ''.join(f'<td>{inline(c)}</td>' for c in r) + '</tr>')
                h.append('</tbody></table></div>')
                emit(''.join(h))
            continue
        if s.startswith('>'):
            buf = []
            while i < n and lines[i].strip().startswith('>'):
                buf.append(lines[i].strip()[1:].strip())
                i += 1
            emit('<aside class="note"><p>' + inline(' '.join(buf)) + '</p></aside>')
            continue
        if re.match(r'^([-*]|\d+\.)\s+', s):
            ordered = bool(re.match(r'^\d+\.', s))
            items = []
            while i < n and re.match(r'^\s*([-*]|\d+\.)\s+', lines[i]):
                items.append(re.sub(r'^\s*([-*]|\d+\.)\s+', '', lines[i]).strip())
                i += 1
                while i < n and lines[i].startswith('  ') and lines[i].strip() and not re.match(r'^\s*([-*]|\d+\.)\s+', lines[i]):
                    items[-1] += ' ' + lines[i].strip()
                    i += 1
            tag = 'ol' if ordered else 'ul'
            emit(f'<{tag}>' + ''.join(f'<li>{inline(x)}</li>' for x in items) + f'</{tag}>')
            continue
        buf = [s]
        i += 1
        while i < n and lines[i].strip() and not re.match(r'^(#{2,4}\s|[-*]\s|\d+\.\s|>|\||---$)', lines[i].strip()):
            buf.append(lines[i].strip())
            i += 1
        emit('<p>' + inline(' '.join(buf)) + '</p>')
    close_q()
    return '\n'.join(out), heads, faqs


def front_matter(text):
    """Parse a simple '---' key: value header. Lists use [a, b]."""
    meta, body = {}, text
    if text.startswith('---'):
        end = text.index('\n---', 3)
        for raw in text[3:end].strip().split('\n'):
            if ':' not in raw:
                continue
            k, v = raw.split(':', 1)
            v = v.strip()
            if v.startswith('[') and v.endswith(']'):
                v = [x.strip().strip('"\'') for x in v[1:-1].split(',') if x.strip()]
            else:
                v = v.strip('"\'')
            meta[k.strip()] = v
        body = text[end + 4:]
    return meta, body
