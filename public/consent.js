/* Google Analytics, loaded only after the visitor accepts analytics cookies.
   Choice is stored in localStorage as cl:analytics = "granted" | "denied".
   Any element with data-cookie-settings reopens the banner. */
(() => {
  const TAG_ID = 'GTM-NXVRK5GB'; // Google tag for GA4 property "Compare Legal"
  const KEY = 'cl:analytics';
  let loaded = false;

  const read = () => { try { return localStorage.getItem(KEY); } catch (e) { return null; } };
  const save = v => { try { localStorage.setItem(KEY, v); } catch (e) {} };

  function load() {
    if (loaded) return;
    loaded = true;
    window.dataLayer = window.dataLayer || [];
    window.gtag = function () { dataLayer.push(arguments); };
    gtag('js', new Date());
    const s = document.createElement('script');
    s.async = true;
    s.src = 'https://www.googletagmanager.com/gtm.js?id=' + TAG_ID;
    document.head.appendChild(s);
  }

  function clearGaCookies() {
    const host = location.hostname.replace(/^www\./, '');
    document.cookie.split(';').map(c => c.split('=')[0].trim()).filter(n => /^_ga/.test(n)).forEach(n => {
      ['', '; domain=' + host, '; domain=.' + host].forEach(d => {
        document.cookie = n + '=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/' + d;
      });
    });
  }

  const css = `
#cl-consent{position:fixed;z-index:1000;left:16px;right:16px;bottom:16px;max-width:560px;margin-inline:auto;
  background:var(--surface,#fff);color:var(--ink,#15212C);border:1px solid var(--line,#D9DFDC);border-radius:10px;
  box-shadow:0 1px 2px rgba(21,33,44,.06),0 8px 24px rgba(21,33,44,.12);padding:16px 18px;
  font:15px/1.45 var(--f-body,system-ui,-apple-system,"Segoe UI",sans-serif)}
#cl-consent p{margin:0 0 12px}
#cl-consent .row{display:flex;flex-wrap:wrap;gap:8px}
#cl-consent button{font:inherit;font-weight:600;cursor:pointer;border-radius:6px;padding:8px 14px;
  border:1px solid var(--accent,#24507A)}
#cl-consent .yes{background:var(--accent,#24507A);color:var(--accent-ink,#fff)}
#cl-consent .no{background:transparent;color:var(--accent,#24507A)}
#cl-consent button:focus-visible{outline:2px solid var(--accent,#24507A);outline-offset:2px}
@media (prefers-color-scheme:dark){#cl-consent{background:var(--surface,#161F28);color:var(--ink,#E6ECF1);border-color:var(--line,#2B3743)}}`;

  function banner() {
    if (document.getElementById('cl-consent')) return;
    if (!document.getElementById('cl-consent-css')) {
      const st = document.createElement('style');
      st.id = 'cl-consent-css';
      st.textContent = css;
      document.head.appendChild(st);
    }
    const box = document.createElement('div');
    box.id = 'cl-consent';
    box.setAttribute('role', 'region');
    box.setAttribute('aria-label', 'Cookie choice');
    box.innerHTML = '<p>We would like to use Google Analytics cookies to count visits and see which pages are useful, so we can improve Compare Legal. They stay off unless you accept. You can change your mind any time from the footer.</p>'
      + '<div class="row"><button type="button" class="yes">Accept analytics</button><button type="button" class="no">Reject</button></div>';
    box.querySelector('.yes').onclick = () => { save('granted'); box.remove(); load(); };
    box.querySelector('.no').onclick = () => {
      const was = read() === 'granted';
      save('denied'); box.remove(); clearGaCookies();
      if (was && loaded) location.reload(); // stop the tag already running on this page
    };
    document.body.appendChild(box);
  }

  document.addEventListener('click', ev => {
    const t = ev.target.closest && ev.target.closest('[data-cookie-settings]');
    if (t) { ev.preventDefault(); banner(); }
  });

  const choice = read();
  if (choice === 'granted') load();
  else if (choice !== 'denied') {
    if (document.body) banner();
    else document.addEventListener('DOMContentLoaded', banner);
  }
})();
