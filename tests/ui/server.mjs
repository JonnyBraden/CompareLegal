// Static server for the UI tests. Serves a built copy of the site, replaces the Supabase browser
// bundle with the fake client, stands in for the Edge Functions, and sends the same Content-Security-Policy
// that render.yaml sets in production (so the tests prove the site works under it).
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const TYPES = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css', '.txt': 'text/plain' };
// 1x1 transparent PNG, served for any stored logo.
const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==', 'base64');

export function productionCsp(root) {
  const yaml = fs.readFileSync(path.join(root, 'render.yaml'), 'utf8');
  const m = yaml.match(/name: Content-Security-Policy\s+value: "([^"]+)"/);
  if (!m) throw new Error('Content-Security-Policy not found in render.yaml');
  return m[1];
}

export async function startServer({ outDir, root, useFake }) {
  const csp = productionCsp(root);
  const state = { functionCalls: [], alreadySubscribed: false };

  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, 'http://localhost');
    const send = (status, body, type = 'application/json') => {
      res.writeHead(status, { 'Content-Type': type, 'Content-Security-Policy': csp, 'Cache-Control': 'no-store' });
      res.end(body);
    };

    if (req.method === 'POST' && url.pathname.startsWith('/functions/v1/')) {
      const name = url.pathname.split('/').pop();
      let raw = ''; for await (const c of req) raw += c;
      const call = { name, auth: req.headers.authorization || '', apikey: req.headers.apikey || '', body: raw ? JSON.parse(raw) : {} };
      state.functionCalls.push(call);
      if (name === 'submit-enquiry') {
        if (call.body.email === 'bad@x') return send(400, JSON.stringify({ error: 'Please enter a valid email address.' }));
        return send(200, JSON.stringify({ ok: true }));
      }
      if (!call.auth.startsWith('Bearer fake-token-')) return send(401, JSON.stringify({ error: 'Please sign in again.' }));
      if (name === 'create-checkout-session') {
        if (state.alreadySubscribed) return send(409, JSON.stringify({ error: 'already_subscribed' }));
        return send(200, JSON.stringify({ url: 'https://checkout.stripe.com/c/pay/cs_test_123' }));
      }
      if (name === 'create-portal-session') return send(200, JSON.stringify({ url: 'https://billing.stripe.com/p/session/test_123' }));
      return send(404, '{}');
    }

    if (url.pathname.startsWith('/storage/v1/object/public/logos/')) {
      res.writeHead(200, { 'Content-Type': 'image/png' }); return res.end(PNG);
    }

    let rel = decodeURIComponent(url.pathname); if (rel === '/') rel = '/index.html';
    if (useFake && rel === '/vendor/supabase.js') return send(200, fs.readFileSync(path.join(here, 'fake-supabase-browser.js')), TYPES['.js']);
    const file = path.join(outDir, path.normalize(rel));
    if (!file.startsWith(outDir) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) return send(404, 'not found', 'text/plain');
    send(200, fs.readFileSync(file), TYPES[path.extname(file)] || 'application/octet-stream');
  });

  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  const port = server.address().port;
  return { port, origin: `http://127.0.0.1:${port}`, state, close: () => new Promise((r) => server.close(r)) };
}
