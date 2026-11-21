/* PostPilot — single-page UI (vanilla JS). Composer + previews live in composer.js. */
'use strict';

// ------------------------------------------------------------------ helpers
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const S = {status: null, nets: {}, netList: [], accounts: [], page: '', live: null, lastNote: 0, notifyAsked: false, rev: -1, liveAt: 0};
const nf = n => n == null ? '—' : n >= 1e6 ? (n / 1e6).toFixed(1).replace(/\.0$/, '') + 'M' : n >= 1e4 ? (n / 1e3).toFixed(1).replace(/\.0$/, '') + 'K' : Number(n).toLocaleString();
const now = () => Date.now() / 1000;
// thumbnail for any media item: image/GIF/video poster as <img>, or a frame of the video itself when it has no poster yet
const mthumb = (m, attrs = '') => !m ? '' : (m.kind === 'video' && !m.poster)
  ? `<video src="${m.url}#t=3" preload="metadata" muted playsinline ${attrs}></video>`
  : (m.thumb ? `<img src="${m.thumb}" loading="lazy" alt="" ${attrs}>` : '');
// add up metrics over targets; a key stays undefined (shown as —) when no network reports it (e.g. YouTube shares)
function sumMetrics(targets) {
  const o = {};
  for (const t of targets) for (const k of ['views', 'likes', 'comments', 'shares']) { const v = (t.metrics || {})[k]; if (v != null) o[k] = (o[k] || 0) + Number(v); }
  return o;
}
const metricLine = m => [['👁', m.views, 'views'], ['♥', m.likes, 'likes'], ['💬', m.comments, 'comments'], ['↻', m.shares, 'shares']]
  .filter(x => x[1] != null).map(x => `<span title="${x[2]}">${x[0]} ${nf(x[1])}</span>`).join(' · ');
const debounce = (fn, ms = 300) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

function rel(ts) {
  if (!ts) return '—';
  const d = ts - now(), a = Math.abs(d);
  if (a < 50) return 'now';
  const s = a < 3600 ? Math.round(a / 60) + 'm' : a < 86400 ? Math.round(a / 3600) + 'h' : Math.round(a / 86400) + 'd';
  return d > 0 ? 'in ' + s : s + ' ago';
}
function fdate(ts, opt = {}) {
  if (!ts) return '—';
  const d = new Date(ts * 1000);
  return d.toLocaleDateString(undefined, {weekday: opt.wd ? 'short' : undefined, day: 'numeric', month: 'short'}) + (opt.noTime ? '' : ' · ' + ftime(ts));
}
const ftime = ts => new Date(ts * 1000).toLocaleTimeString(undefined, {hour: '2-digit', minute: '2-digit'});
function initials(s) { const w = String(s || '?').replace(/[^\p{L}\p{N} ]/gu, ' ').trim().split(/\s+/); return ((w[0] || '?')[0] + (w[1] ? w[1][0] : '')).toUpperCase(); }

async function api(path, opts = {}) {
  const o = {method: opts.method || (opts.body !== undefined || opts.form ? 'POST' : 'GET'), headers: {'X-PP': '1'}};
  if (opts.form) o.body = opts.form;
  else if (opts.body !== undefined) { o.headers['Content-Type'] = 'application/json'; o.body = JSON.stringify(opts.body); }
  let r;
  try { r = await fetch(path, o); } catch (e) { throw new Error('PostPilot is not running — open it again from the Start menu.'); }
  let data = {};
  try { data = await r.json(); } catch (e) { /* not json */ }
  if (!r.ok) throw new Error(data.error || `Request failed (${r.status})`);
  return data;
}
const post = (p, body = {}) => api(p, {body});
const del = p => api(p, {method: 'DELETE'});

function toast(msg, type = '') {
  const t = document.createElement('div');
  t.className = 'toast ' + type;
  t.innerHTML = ic(type === 'err' ? 'alert' : type === 'ok' ? 'check' : type === 'warn' ? 'clock' : 'bolt', 18) + `<div>${esc(msg)}</div>`;
  $('#toasts').appendChild(t);
  while ($('#toasts').children.length > 4) $('#toasts').firstElementChild.remove();
  setTimeout(() => { t.style.transition = 'opacity .3s'; t.style.opacity = '0'; setTimeout(() => t.remove(), 300); }, type === 'err' ? 7000 : 3800);
}
async function run(btn, fn) {
  const b = btn && btn.tagName ? btn : null;
  let html;
  if (b) { html = b.innerHTML; b.disabled = true; b.innerHTML = '<span class="spin"></span>' + esc(b.textContent.trim()); }
  try { return await fn(); }
  catch (e) { toast(e.message, 'err'); }
  finally { if (b) { b.disabled = false; b.innerHTML = html; } }
}
async function copyText(t) {
  try { await navigator.clipboard.writeText(t); toast('Copied to clipboard', 'ok'); }
  catch (e) { const ta = document.createElement('textarea'); ta.value = t; document.body.appendChild(ta); ta.select(); document.execCommand('copy'); ta.remove(); toast('Copied', 'ok'); }
}

// ------------------------------------------------------------------ icons
const IC = {
  home: '<path d="M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"/>',
  cal: '<rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/>',
  list: '<line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><circle cx="4" cy="6" r="1"/><circle cx="4" cy="12" r="1"/><circle cx="4" cy="18" r="1"/>',
  bulb: '<path d="M9 18h6M10 22h4M12 2a7 7 0 0 0-4 12.7V17h8v-2.3A7 7 0 0 0 12 2z"/>',
  image: '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/>',
  chart: '<line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/>',
  users: '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
  bolt: '<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>',
  gear: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-2.82 1.17V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 3.17 14H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9.3 3.17V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 2.6 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 20.83 10H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>',
  pulse: '<polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>',
  plus: '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>',
  x: '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
  check: '<polyline points="20 6 9 17 4 12"/>',
  alert: '<path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
  clock: '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
  edit: '<path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4z"/>',
  copy: '<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
  trash: '<polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/>',
  send: '<line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/>',
  refresh: '<polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>',
  ext: '<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/>',
  download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>',
  upload: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/>',
  bell: '<path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/>',
  moon: '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>',
  sun: '<circle cx="12" cy="12" r="5"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/>',
  spark: '<path d="M12 3l1.9 5.8L20 10l-6.1 1.2L12 17l-1.9-5.8L4 10l6.1-1.2z"/><path d="M19 17l.8 2.2L22 20l-2.2.8L19 23l-.8-2.2L16 20l2.2-.8z"/>',
  link: '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
  hash: '<line x1="4" y1="9" x2="20" y2="9"/><line x1="4" y1="15" x2="20" y2="15"/><line x1="10" y1="3" x2="8" y2="21"/><line x1="16" y1="3" x2="14" y2="21"/>',
  file: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/>',
  smile: '<circle cx="12" cy="12" r="10"/><path d="M8 14s1.5 2 4 2 4-2 4-2"/><line x1="9" y1="9" x2="9.01" y2="9"/><line x1="15" y1="9" x2="15.01" y2="9"/>',
  repeat: '<polyline points="17 1 21 5 17 9"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/><polyline points="7 23 3 19 7 15"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/>',
  rss: '<path d="M4 11a9 9 0 0 1 9 9"/><path d="M4 4a16 16 0 0 1 16 16"/><circle cx="5" cy="19" r="1"/>',
  db: '<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/>',
  power: '<path d="M18.36 6.64a9 9 0 1 1-12.73 0"/><line x1="12" y1="2" x2="12" y2="12"/>',
  heart: '<path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/>',
  chat: '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
  share: '<circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><line x1="8.59" y1="13.51" x2="15.42" y2="17.49"/><line x1="15.41" y1="6.51" x2="8.59" y2="10.49"/>',
  eye: '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>',
  play: '<polygon points="6 3 20 12 6 21 6 3"/>',
  search: '<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>',
  left: '<polyline points="15 18 9 12 15 6"/>', right: '<polyline points="9 18 15 12 9 6"/>',
  globe: '<circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>',
  key: '<circle cx="7.5" cy="15.5" r="5.5"/><path d="m21 2-9.6 9.6M15.5 7.5l3 3L22 7l-3-3"/>',
  shield: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>',
  pause: '<rect x="6" y="4" width="4" height="16" rx="1"/><rect x="14" y="4" width="4" height="16" rx="1"/>',
};
const ic = (n, s = 18) => `<svg class="i" viewBox="0 0 24 24" width="${s}" height="${s}" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${IC[n] || ''}</svg>`;
const icInline = (n, s = 14) => ic(n, s).replace('class="i"', 'class="i" style="display:inline;vertical-align:-2px"');

// ------------------------------------------------------------------ modal
function modal({title, body, foot = '', size = '', onClose}) {
  const bg = document.createElement('div');
  bg.className = 'modal-bg';
  bg.innerHTML = `<div class="modal ${size}" role="dialog"><div class="modal-h"><h3>${title}</h3><button class="btn icon ghost x" data-x>${ic('x')}</button></div>
    <div class="modal-b">${body}</div>${foot ? `<div class="modal-f">${foot}</div>` : ''}</div>`;
  const close = () => { bg.remove(); document.removeEventListener('keydown', onKey); onClose && onClose(); };
  const onKey = e => { if (e.key === 'Escape' && bg === $('#modalRoot').lastElementChild) close(); };
  bg.addEventListener('mousedown', e => { bg._down = e.target === bg; });
  bg.addEventListener('click', e => { if (e.target === bg && bg._down) close(); });
  bg.querySelector('[data-x]').onclick = close;
  document.addEventListener('keydown', onKey);
  $('#modalRoot').appendChild(bg);
  return {el: bg, close};
}
function confirmBox(title, text, ok = 'Confirm', danger = false) {
  return new Promise(res => {
    const m = modal({title, body: `<p style="margin:0;color:var(--ink-2)">${text}</p>`,
      foot: `<button class="btn" data-no>Cancel</button><button class="btn ${danger ? 'danger solid' : 'pri'}" data-ok>${ok}</button>`, onClose: () => res(false)});
    m.el.querySelector('[data-no]').onclick = () => m.close();
    m.el.querySelector('[data-ok]').onclick = () => { res(true); m.el.remove(); };
  });
}
const modalOpen = () => !!$('#modalRoot').children.length;
function popMenu(anchor, items) {
  $$('.menu').forEach(m => m.remove());
  const m = document.createElement('div');
  m.className = 'menu';
  m.innerHTML = items.map((it, i) => it === '-' ? '<div class="sep"></div>' : `<button data-i="${i}">${it.icon ? ic(it.icon, 16) : ''}${esc(it.label)}</button>`).join('');
  document.body.appendChild(m);
  const r = anchor.getBoundingClientRect();
  const top = r.bottom + 6 + m.offsetHeight > innerHeight ? r.top - m.offsetHeight - 6 : r.bottom + 6;
  m.style.top = (top + scrollY) + 'px';
  m.style.left = Math.min(r.left, innerWidth - m.offsetWidth - 10) + 'px';
  m.onclick = e => { const b = e.target.closest('button'); if (b) { m.remove(); items[+b.dataset.i].run(); } };
  setTimeout(() => document.addEventListener('click', function h(e) { if (!m.contains(e.target)) { m.remove(); document.removeEventListener('click', h); } }), 0);
}

// ------------------------------------------------------------------ networks & accounts
function netMeta(k) { return S.nets[k] || {name: k, color: '#888', abbr: '?', text_limit: 5000, supports: []}; }
function nb(network) { const m = netMeta(network); return `<span class="nb" style="background:${m.color}">${esc(m.abbr)}</span>`; }
function avatar(a, sm) {
  const img = a.avatar ? `<img src="${esc(a.avatar)}" alt="" referrerpolicy="no-referrer" onerror="this.outerHTML='<div class=ini>${esc(initials(a.name || a.account_name))}</div>'">` : `<div class="ini">${esc(initials(a.name || a.account_name))}</div>`;
  return `<div class="av ${sm ? 'sm' : ''}">${img}${nb(a.network)}</div>`;
}
async function loadAccounts() {
  const [n, a] = await Promise.all([api('/api/networks'), api('/api/accounts')]);
  S.netList = n.networks; S.nets = Object.fromEntries(n.networks.map(x => [x.key, x])); S.redirect = n.redirect;
  S.accounts = a.accounts; S.defaultSlots = a.default_slots;
}
function statusPill(s) {
  const m = {draft: ['', 'Draft'], scheduled: ['b', 'Scheduled'], published: ['g', 'Published'], partial: ['a', 'Partly failed'], failed: ['r', 'Failed'],
    pending: ['b', 'Queued'], publishing: ['s', 'Publishing…'], handed: ['g', 'Uploaded · goes live by itself'], reminder: ['a', 'Post manually'], skipped: ['', 'Skipped'],
    ok: ['g', 'Connected'], expired: ['r', 'Reconnect'], paused: ['a', 'Paused'], error: ['r', 'Error']}[s] || ['', s];
  return `<span class="pill ${m[0]}">${m[1]}</span>`;
}
function tgtChips(p) {
  return `<div class="tgts">${p.targets.map(t => {
    const cls = t.status === 'published' || t.status === 'handed' ? 'ok' : t.status === 'failed' ? 'bad' : t.status === 'reminder' ? 'wait' : '';
    const icn = t.status === 'published' ? '✓' : t.status === 'handed' ? '📅' : t.status === 'failed' ? '✕' : t.status === 'publishing' ? '…' : t.status === 'reminder' ? '⏰' : '';
    const why = t.status === 'handed' ? `Already uploaded — ${netMeta(t.network).name || t.network} publishes it at ${fdate(t.published_at)} by itself. Your PC can be off.` : t.error;
    const tip = why ? ` title="${esc(why)}"` : '';
    const inner = `${nb(t.network)}${esc(t.account_name)} ${icn}`;
    return t.url ? `<a class="tg ${cls}" href="${esc(t.url)}" target="_blank" rel="noopener"${tip}>${inner}</a>` : `<span class="tg ${cls}"${tip}>${inner}</span>`;
  }).join('')}</div>`;
}

// ------------------------------------------------------------------ shell
const NAV = [['dashboard', 'Dashboard', 'home'], ['inbox', 'Inbox', 'chat'], ['calendar', 'Calendar', 'cal'], ['posts', 'Posts', 'list'], ['ideas', 'Ideas', 'bulb'],
  ['media', 'Media library', 'image'], ['analytics', 'Analytics', 'chart'], ['accounts', 'Accounts', 'users'], ['automation', 'Automation', 'bolt'],
  ['activity', 'Activity', 'pulse'], ['settings', 'Settings', 'gear']];
function renderNav() {
  const c = S.status ? S.status.counts : {};
  const badge = {posts: c.failed ? `<span class="cnt red">${c.failed}</span>` : c.scheduled ? `<span class="cnt">${c.scheduled}</span>` : '',
    accounts: c.account_issues ? `<span class="cnt amber">${c.account_issues}</span>` : '',
    inbox: c.inbox_needs ? `<span class="cnt red">${c.inbox_needs}</span>` : c.inbox_new ? `<span class="cnt">${c.inbox_new}</span>` : ''};
  $('#nav').innerHTML = NAV.map(n => `<button class="nav-btn ${S.page === n[0] ? 'on' : ''}" onclick="go('${n[0]}')">${ic(n[2])}<span>${n[1]}</span>${badge[n[0]] || ''}</button>`).join('');
  if (S.status) {
    $('#brandSwitch').innerHTML = `<select onchange="switchBrand(this.value)" title="Brand / workspace">${S.status.brands.map(b => `<option value="${b.id}" ${b.id === S.status.brand_id ? 'selected' : ''}>● ${esc(b.name)}</option>`).join('')}<option value="new">+ New brand…</option></select>`;
    const aiTxt = {anthropic: 'Claude AI ready', ollama: 'Local AI (Ollama) ready', basic: 'AI: basic helpers', off: 'AI off'}[S.status.ai];
    $('#sideFoot').innerHTML = `<div class="row">${icInline('spark')} ${aiTxt}</div><div class="row">${icInline('globe')} 100% local · data stays on this PC</div>${S.status.fake ? '<div class="pill a">Demo mode</div>' : ''}`;
    $('#bellBtn').innerHTML = ic('bell') + (c.reminders ? `<span class="dotn">${c.reminders}</span>` : '');
    const q = S.status.queue || {};
    const br = S.status.brands.find(b => b.id === S.status.brand_id) || {};
    $('#topStatus').innerHTML = `<span class="pill brandpill" title="Current brand — everything you see belongs to it" onclick="go('settings')"><i style="background:${esc(br.color || '#6d5dfc')}"></i>${esc(br.name || '')}</span>`
      + (c.publishing ? ` <span class="pill s" title="${q.videos ? q.videos + ' video upload(s) running · ' : ''}${q.workers || 4} at the same time max"><span class="spin" style="width:10px;height:10px"></span> Publishing ${c.publishing}${S.status.due > c.publishing ? ` · ${S.status.due - c.publishing} waiting` : ''}…</span>` : '');
  }
}
async function switchBrand(v) {
  if (v === 'new') {
    const m = modal({title: 'New brand / workspace', body: `<p class="muted small" style="margin-top:0">Each brand has its own accounts, posts, media and settings — perfect for managing clients.</p>
      <div class="field"><label class="f">Brand name</label><input class="in" id="nbName" placeholder="e.g. Café Atlas"></div>`, foot: `<button class="btn" data-c>Cancel</button><button class="btn pri" id="nbGo">Create</button>`,
      onClose: () => renderNav()});
    m.el.querySelector('[data-c]').onclick = m.close;
    $('#nbGo').onclick = () => run($('#nbGo'), async () => { await post('/api/brands', {name: $('#nbName').value}); m.close(); await afterBrandChange(); });
    setTimeout(() => $('#nbName').focus(), 50);
    return;
  }
  await post('/api/brands/select', {id: +v});
  await afterBrandChange();
}
async function afterBrandChange() { await pollStatus(); await loadAccounts(); route(); toast('Switched brand', 'ok'); }
function toggleSide(on) { $('#side').classList.toggle('open', on); $('#scrim').classList.toggle('on', on); }
function applyTheme(t) { document.documentElement.dataset.theme = t; $('#themeBtn').innerHTML = ic(t === 'dark' ? 'sun' : 'moon'); }
function toggleTheme() {
  const t = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  applyTheme(t);
  try { localStorage.setItem('pp-theme', t); } catch (e) { /* ignore */ }
  post('/api/settings', {theme: t}).catch(() => {});
}

async function pollStatus() {
  try { S.status = await api('/api/status?since=' + S.lastNote); }
  catch (e) { $('#topStatus').innerHTML = `<span class="pill r">${esc(e.message)}</span>`; return; }
  const st = S.status;
  if (S.lastNote) {
    st.notifications.slice().reverse().filter(n => (n.level === 'error' || n.level === 'notify' || ['publish', 'feeds', 'recurring'].includes(n.kind)) && !(n.kind === 'inbox' && S.page === 'inbox')).forEach(n => {
      toast(n.text, n.level === 'error' ? 'err' : n.level === 'good' ? 'ok' : 'warn');
      if ((n.level === 'notify' || n.level === 'error') && 'Notification' in window && Notification.permission === 'granted') {
        try { new Notification('PostPilot', {body: n.text}); } catch (e) { /* ignore */ }
      }
    });
  }
  S.lastNote = st.last_activity || 0;
  renderNav();
  const changed = st.rev !== S.rev;
  S.rev = st.rev;
  if (S.live && !modalOpen() && !document.hidden && !S.selecting && (changed || Date.now() - S.liveAt > 60000)) { S.liveAt = Date.now(); S.live(); }
}

// ------------------------------------------------------------------ router
const PAGES = {};
function go(p) { location.hash = '#/' + p; toggleSide(false); }
async function route() {
  const [p, arg] = (location.hash.replace(/^#\/?/, '') || 'dashboard').split('/');
  const page = PAGES[p] ? p : 'dashboard';
  S.page = page; S.live = null;
  renderNav();
  const pg = PAGES[page];
  $('#pageTitle').textContent = pg.title;
  $('#pageSub').textContent = pg.sub || '';
  const el = $('#content');
  if (el.dataset.page !== page) el.innerHTML = '<div class="empty"><span class="spin" style="width:24px;height:24px;color:var(--brand)"></span></div>';
  el.dataset.page = page;
  try { await pg.render(el, arg); }
  catch (e) { el.innerHTML = `<div class="card"><div class="empty"><div class="ic">${ic('alert', 28)}</div><h3>Something went wrong</h3><p>${esc(e.message)}</p><button class="btn" onclick="route()">Try again</button></div></div>`; }
}
window.addEventListener('hashchange', route);
const emptyState = (icon, title, text, btn = '') => `<div class="empty"><div class="ic">${ic(icon, 28)}</div><h3>${title}</h3><p>${text}</p>${btn}</div>`;
function kpi(icon, col, label, v, sub) { return `<div class="kpi"><div class="lb"><span class="ic ${col}">${ic(icon, 15)}</span>${label}</div><div class="val">${v}</div><div class="s">${sub}</div></div>`; }

// ================================================================== DASHBOARD
PAGES.dashboard = {title: 'Dashboard', sub: 'Everything that is coming up and how it went', render: async el => {
  const d = await api('/api/dashboard');
  const c = S.status.counts;
  const hasAcc = d.accounts.length > 0;
  const e = d.engagement_30d;
  el.innerHTML = `${!hasAcc ? `<div class="hero"><h2>Welcome to PostPilot ✈️</h2><p>Plan, create and publish to Facebook, Instagram, X, LinkedIn, Threads, YouTube, Pinterest, Telegram, Bluesky, Mastodon and Discord — from one place, on your own PC.</p>
      <div class="row mt"><button class="btn" style="background:#fff;color:#5546e0;border:0" onclick="go('accounts')">${ic('plus', 16)}Connect your first account</button></div></div>` : ''}
    ${c.failed ? `<div class="alert r">${ic('alert')}<div><b>${c.failed} post(s) failed</b> in the last 7 days.</div><span class="sp"></span><button class="btn sm" onclick="go('posts/failed')">Review</button></div>` : ''}
    ${c.account_issues ? `<div class="alert a">${ic('users')}<div><b>${c.account_issues} account(s)</b> need attention (login expiring or expired).</div><span class="sp"></span><button class="btn sm" onclick="go('accounts')">Fix</button></div>` : ''}
    ${c.inbox_needs ? `<div class="alert b">${ic('chat')}<div><b>${c.inbox_needs} message(s)</b> on your posts need a reply (questions & complaints first).</div><span class="sp"></span><button class="btn sm" onclick="go('inbox')">Open inbox</button></div>` : ''}
    ${c.reminders ? `<div class="alert a">${ic('bell')}<div><b>${c.reminders} post(s)</b> are waiting for you to publish manually (TikTok, WhatsApp…).</div><span class="sp"></span><button class="btn sm" onclick="openReminders()">Open</button></div>` : ''}
    <div class="kpis">${kpi('cal', 'b', 'Scheduled', c.scheduled, `${c.drafts} draft(s)`)}${kpi('send', 'g', 'Published · 7 days', d.published_7d, 'posts across all networks')}
      ${kpi('heart', 'r', 'Engagement · 30 days', nf(e.likes + e.comments + e.shares), `${nf(e.views)} views · ${nf(e.likes)} likes · ${nf(e.comments)} comments`)}${kpi('users', 's', 'Accounts', d.accounts.length, c.account_issues ? `${c.account_issues} need attention` : 'all healthy')}</div>
    <div class="grid g-side"><div>
      <div class="card"><div class="card-h"><h2>${ic('cal')}This week</h2><div class="act"><button class="btn sm" onclick="go('calendar')">Calendar</button></div></div>
        <div class="card-b"><div class="week">${d.week.map((w, i) => `<div class="wd ${i === 0 ? 'today' : ''}" onclick="go('calendar')"><div class="tiny muted b">${w.day}</div><div class="n">${w.n}</div><div class="tiny muted">${w.date}</div></div>`).join('')}</div></div></div>
      <div class="card"><div class="card-h"><h2>${ic('clock')}Up next</h2><div class="act"><button class="btn sm pri" onclick="openComposer()">${ic('plus', 14)}Create post</button></div></div>
        <div>${d.upcoming.length ? d.upcoming.map(postRow).join('') : emptyState('cal', 'Nothing scheduled', 'Create a post and pick a time — or add it to your queue.', `<button class="btn pri" onclick="openComposer()">${ic('plus')}Create post</button>`)}</div></div>
      <div class="card"><div class="card-h"><h2>${ic('send')}Recently published</h2><div class="act"><button class="btn sm" onclick="go('posts/published')">All</button></div></div>
        <div>${d.recent.length ? d.recent.map(postRow).join('') : '<div class="empty small">Published posts and their results appear here.</div>'}</div></div>
    </div><div>
      <div id="dashLogins"></div>
      <div class="card"><div class="card-h"><h2>${ic('users')}Accounts</h2><div class="act"><button class="btn sm" onclick="go('accounts')">${ic('plus', 14)}Connect</button></div></div>
        <div class="card-b" style="padding-top:6px;padding-bottom:6px">${d.accounts.length ? d.accounts.map(a => `<div class="act-row">${avatar(a, true)}<div style="min-width:0;flex:1"><div class="b ell">${esc(a.name)}</div><div class="tiny muted ell">${esc(a.network_name)}${a.error ? ' · ' + esc(a.error) : ''}</div></div>${statusPill(a.status)}</div>`).join('') : '<div class="muted small" style="padding:14px 0">No accounts yet.</div>'}</div></div>
      <div class="card"><div class="card-h"><h2>${ic('pulse')}Activity</h2></div><div class="card-b" style="padding-top:4px;padding-bottom:4px">${d.activity.length ? d.activity.map(actRow).join('') : '<div class="muted small" style="padding:14px 0">Nothing yet.</div>'}</div></div>
    </div></div>`;
  if (hasAcc) dashLoginsCard();
  S.live = debounce(() => { if (S.page === 'dashboard') PAGES.dashboard.render(el); }, 100);
}};
function actRow(a) {
  const m = {good: ['g', 'check'], warn: ['a', 'clock'], error: ['r', 'alert'], notify: ['a', 'bell'], info: ['b', 'bolt']}[a.level] || ['b', 'bolt'];
  return `<div class="act-row"><div class="ic ${m[0]}">${ic(m[1], 14)}</div><div style="min-width:0">${esc(a.text)}</div><div class="tm" title="${fdate(a.ts)}">${rel(a.ts)}</div></div>`;
}
function postRow(p) {
  const when = p.status === 'scheduled' ? p.scheduled_at : (p.published_at || p.scheduled_at || p.updated_at);
  const th = p.media_items[0];
  const hasM = p.targets.some(t => t.metrics && Object.keys(t.metrics).length);
  return `<div class="prow"><div class="when">${when ? `<b>${ftime(when)}</b><span class="muted">${fdate(when, {noTime: true, wd: true})}</span>` : '<span class="muted">No date</span>'}</div>
    ${mthumb(th, 'class="thumb"')}
    <div class="tx"><div class="row" style="gap:6px;margin-bottom:2px">${statusPill(p.status)}${p.recurring && p.recurring.every_days ? `<span class="pill">${icInline('repeat', 12)} every ${p.recurring.every_days}d</span>` : ''}${p.source !== 'composer' ? `<span class="pill">${esc(p.source)}</span>` : ''}
      ${hasM ? `<span class="tiny muted">${metricLine(sumMetrics(p.targets))}</span>` : ''}</div>
      <div class="t" onclick="openPost(${p.id})">${esc(p.text || '(media only)')}</div>${tgtChips(p)}
      ${p.targets.filter(t => t.status === 'failed' || (t.status === 'pending' && t.error)).map(t => `<div class="tiny" style="color:${t.status === 'failed' ? 'var(--red-d)' : 'var(--amber-d)'};margin-top:4px">${esc(t.account_name)}: ${esc(t.error)}</div>`).join('')}</div>
    <div class="acts">${['draft', 'scheduled'].includes(p.status) ? `<button class="btn sm ghost" title="Edit" onclick="openPost(${p.id})">${ic('edit', 15)}</button>` : ''}
      <button class="btn sm ghost" title="More" onclick="postMenu(this,${p.id},'${p.status}')">⋯</button></div></div>`;
}
async function openPost(id) {
  const p = (await api('/api/posts/' + id)).post;
  if (['draft', 'scheduled'].includes(p.status)) openComposer({post: p});
  else showPostDetail(p);
}
function showPostDetail(p) {
  const m = modal({title: 'Post details', size: 'mid', body: `<div class="row mb">${statusPill(p.status)}<span class="muted small">${p.published_at ? 'Published ' + fdate(p.published_at) : ''}</span></div>
    <div style="white-space:pre-wrap" class="mb">${esc(p.text)}</div>
    ${p.media_items.length ? `<div class="mstrip mb">${p.media_items.map(x => `<div class="mitem">${mthumb(x)}</div>`).join('')}</div>` : ''}
    <table class="tbl"><thead><tr><th>Account</th><th>Status</th><th class="num">Likes</th><th class="num">Comments</th><th class="num">Shares</th><th class="num">Views</th><th></th></tr></thead><tbody>
    ${p.targets.map(t => `<tr><td><div class="row nw">${nb(t.network)}<span class="ell" style="max-width:200px">${esc(t.account_name)}</span></div>${t.error ? `<div class="tiny" style="color:var(--red-d)">${esc(t.error)}</div>` : ''}</td>
      <td>${statusPill(t.status)}</td><td class="num">${nf(t.metrics.likes)}</td><td class="num">${nf(t.metrics.comments)}</td><td class="num">${nf(t.metrics.shares)}</td><td class="num">${nf(t.metrics.views)}</td>
      <td>${t.url ? `<a class="btn sm ghost" href="${esc(t.url)}" target="_blank">${ic('ext', 14)}</a>` : ''}</td></tr>`).join('')}</tbody></table>`,
    foot: `${['failed', 'partial'].includes(p.status) ? `<button class="btn pri" id="pdRetry">${ic('refresh', 15)}Retry failed</button>` : ''}<button class="btn" id="pdDup">${ic('copy', 15)}Duplicate</button><button class="btn" data-c>Close</button>`});
  m.el.querySelector('[data-c]').onclick = m.close;
  $('#pdDup').onclick = async () => { const r = await post(`/api/posts/${p.id}/action`, {action: 'duplicate'}); m.close(); openPost(r.id); };
  if ($('#pdRetry')) $('#pdRetry').onclick = () => run($('#pdRetry'), async () => { await post(`/api/posts/${p.id}/action`, {action: 'retry'}); m.close(); toast('Retrying…', 'ok'); refreshPage(); });
}
function postMenu(btn, id, status) {
  const items = [];
  if (['draft', 'scheduled'].includes(status)) items.push({label: 'Edit', icon: 'edit', run: () => openPost(id)}, {label: 'Publish now', icon: 'send', run: () => postAct(id, 'now')});
  if (status === 'scheduled') items.push({label: 'Move to drafts', icon: 'file', run: () => postAct(id, 'unschedule')});
  if (['failed', 'partial'].includes(status)) items.push({label: 'Retry failed', icon: 'refresh', run: () => postAct(id, 'retry')});
  if (!['draft', 'scheduled'].includes(status)) items.push({label: 'View results', icon: 'chart', run: () => openPost(id)});
  items.push({label: 'Duplicate', icon: 'copy', run: async () => { const r = await post(`/api/posts/${id}/action`, {action: 'duplicate'}); openPost(r.id); }}, '-',
    {label: 'Delete', icon: 'trash', run: async () => { if (await confirmBox('Delete this post?', 'It is removed from PostPilot. Posts already published on the networks stay there.', 'Delete', true)) postAct(id, 'delete'); }});
  popMenu(btn, items);
}
async function postAct(id, action) {
  await run(null, async () => { await post(`/api/posts/${id}/action`, {action}); toast({now: 'Publishing now…', retry: 'Retrying…', unschedule: 'Moved to drafts', delete: 'Deleted'}[action] || 'Done', 'ok'); refreshPage(); pollStatus(); });
}
function refreshPage() { const pg = PAGES[S.page]; if (pg) pg.render($('#content'), (location.hash.split('/')[2])); }

// ================================================================== CALENDAR
const CAL = {view: 'month', anchor: new Date(), account: ''};
PAGES.calendar = {title: 'Calendar', sub: 'Drag posts to reschedule · click a day to create', render: async el => {
  const a = CAL.anchor;
  let start, end;
  if (CAL.view === 'month') {
    const first = new Date(a.getFullYear(), a.getMonth(), 1);
    start = new Date(first); start.setDate(1 - ((first.getDay() + 6) % 7));
    end = new Date(start); end.setDate(start.getDate() + 42);
  } else {
    start = new Date(a); start.setHours(0, 0, 0, 0); start.setDate(start.getDate() - ((start.getDay() + 6) % 7));
    end = new Date(start); end.setDate(start.getDate() + 7);
  }
  const d = await api(`/api/posts?from=${Math.floor(start / 1000)}&to=${Math.floor(end / 1000)}${CAL.account ? '&account=' + CAL.account : ''}`);
  const byDay = {};
  d.posts.forEach(p => { const ts = p.scheduled_at || p.published_at; if (!ts) return; const k = new Date(ts * 1000).toDateString(); (byDay[k] = byDay[k] || []).push(p); });
  Object.values(byDay).forEach(l => l.sort((x, y) => (x.scheduled_at || x.published_at) - (y.scheduled_at || y.published_at)));
  const title = CAL.view === 'month' ? a.toLocaleDateString(undefined, {month: 'long', year: 'numeric'})
    : `${start.toLocaleDateString(undefined, {day: 'numeric', month: 'short'})} – ${new Date(end - 86400000).toLocaleDateString(undefined, {day: 'numeric', month: 'short', year: 'numeric'})}`;
  const today = new Date().toDateString();
  const days = [];
  for (let t = new Date(start); t < end; t.setDate(t.getDate() + 1)) days.push(new Date(t));
  const maxEv = CAL.view === 'month' ? 3 : 30;
  el.innerHTML = `<div class="card"><div class="card-h"><button class="btn icon sm" onclick="calNav(-1)">${ic('left')}</button><button class="btn sm" onclick="CAL.anchor=new Date();route()">Today</button><button class="btn icon sm" onclick="calNav(1)">${ic('right')}</button>
      <h2 style="margin-left:6px">${esc(title)}</h2><div class="act"><select class="in sm" style="width:auto" onchange="CAL.account=this.value;route()"><option value="">All accounts</option>${S.accounts.map(x => `<option value="${x.id}" ${String(x.id) === CAL.account ? 'selected' : ''}>${esc(x.network_name)} · ${esc(x.name)}</option>`).join('')}</select>
      <div class="chips"><button class="chip ${CAL.view === 'month' ? 'on' : ''}" onclick="CAL.view='month';route()">Month</button><button class="chip ${CAL.view === 'week' ? 'on' : ''}" onclick="CAL.view='week';route()">Week</button></div>
      <button class="btn soft sm" onclick="planWizard()">${ic('spark', 14)}AI plan</button><button class="btn pri sm" onclick="openComposer()">${ic('plus', 14)}Create</button></div></div>
    <div class="cal">${DAYS.map(x => `<div class="hd">${x}</div>`).join('')}
    ${days.map(day => {
      const list = byDay[day.toDateString()] || [];
      const out = CAL.view === 'month' && day.getMonth() !== a.getMonth();
      const past = day < new Date(new Date().toDateString());
      return `<div class="day ${out ? 'out' : ''} ${day.toDateString() === today ? 'today' : ''}" data-day="${day.getFullYear()}-${day.getMonth()}-${day.getDate()}" style="${CAL.view === 'week' ? 'min-height:420px' : ''}">
        <div class="dn">${day.getDate()}</div>${past ? '' : `<button class="btn icon sm ghost add" title="Create post" onclick="calCreate('${day.toISOString()}')">${ic('plus', 14)}</button>`}
        ${list.slice(0, maxEv).map(p => calEv(p)).join('')}${list.length > maxEv ? `<div class="more" onclick="CAL.view='week';CAL.anchor=new Date('${day.toISOString()}');route()">+${list.length - maxEv} more</div>` : ''}</div>`;
    }).join('')}</div></div>`;
  $$('.cev[draggable=true]', el).forEach(ev => ev.ondragstart = e => { e.dataTransfer.setData('text/plain', ev.dataset.id + '|' + ev.dataset.ts); });
  $$('.day', el).forEach(dy => {
    dy.ondragover = e => { e.preventDefault(); dy.classList.add('drop'); };
    dy.ondragleave = () => dy.classList.remove('drop');
    dy.ondrop = async e => {
      e.preventDefault(); dy.classList.remove('drop');
      const [id, ts] = e.dataTransfer.getData('text/plain').split('|');
      const [y, mo, da] = dy.dataset.day.split('-').map(Number);
      const old = new Date(+ts * 1000);
      const nt = new Date(y, mo, da, old.getHours(), old.getMinutes());
      if (nt < new Date()) return toast("You can't move a post into the past.", 'err');
      await run(null, async () => { await post(`/api/posts/${id}/action`, {action: 'move', scheduled_at: Math.floor(nt / 1000)}); toast('Rescheduled to ' + fdate(nt / 1000), 'ok'); route(); });
    };
  });
  S.live = debounce(() => { if (S.page === 'calendar') PAGES.calendar.render(el); }, 100);
}};
function calEv(p) {
  const ts = p.scheduled_at || p.published_at;
  const can = ['scheduled', 'draft'].includes(p.status);
  return `<div class="cev ${p.status}" ${can ? 'draggable="true"' : ''} data-id="${p.id}" data-ts="${ts}" onclick="openPost(${p.id})" title="${esc(p.text)}">
    <span class="tm">${ftime(ts)}</span><span class="dots">${[...new Set(p.targets.map(t => t.network))].map(n => `<i style="background:${netMeta(n).color}"></i>`).join('')}</span><span class="x">${esc(p.text || 'media')}</span></div>`;
}
function calNav(n) { const a = CAL.anchor; CAL.anchor = CAL.view === 'month' ? new Date(a.getFullYear(), a.getMonth() + n, 1) : new Date(a.getFullYear(), a.getMonth(), a.getDate() + 7 * n); route(); }
function calCreate(iso) { const d = new Date(iso); d.setHours(10, 0, 0, 0); if (d < new Date()) d.setTime(Date.now() + 3600000); openComposer({when: Math.floor(d / 1000)}); }

// ================================================================== POSTS
const PL = {tab: 'scheduled', q: '', shown: 100, sel: new Set()};
PAGES.posts = {title: 'Posts', sub: 'Your queue, drafts and history', render: async (el, arg) => {
  if (arg && ['scheduled', 'draft', 'published', 'failed'].includes(arg) && arg !== PL.tab) { PL.tab = arg; PL.shown = 100; PL.sel.clear(); }
  const d = await api(`/api/posts?status=${PL.tab}&q=${encodeURIComponent(PL.q)}&limit=${PL.shown}`);
  PL.posts = d.posts;
  PL.sel = new Set([...PL.sel].filter(id => d.posts.some(p => p.id === id)));
  const c = S.status.counts;
  let lastDay = '';
  el.innerHTML = `<div class="card"><div class="card-h"><div class="chips">${[['scheduled', 'Scheduled', c.scheduled], ['draft', 'Drafts', c.drafts], ['published', 'Published'], ['failed', 'Failed', c.failed]]
      .map(([k, l, n]) => `<button class="chip ${PL.tab === k ? 'on' : ''}" onclick="PL.tab='${k}';PL.shown=100;PL.sel.clear();route()">${l}${n ? ` <span class="n">${n}</span>` : ''}</button>`).join('')}</div>
      <div class="act"><div class="search">${ic('search', 15)}<input class="in sm" placeholder="Search posts" value="${esc(PL.q)}" oninput="PL.q=this.value;debouncedPosts()"></div>
      ${d.posts.length ? `<button class="btn sm" onclick="plSelectAll()">${PL.sel.size && PL.sel.size === d.posts.length ? 'Unselect all' : 'Select all'}</button>` : ''}
      <button class="btn soft sm" onclick="bulkDialog()">${ic('upload', 14)}Bulk schedule</button><button class="btn pri sm" onclick="openComposer()">${ic('plus', 14)}Create post</button></div></div>
    <div id="plBar"></div>
    <div id="plList">${d.posts.length ? d.posts.map(p => {
      const ts = PL.tab === 'scheduled' ? p.scheduled_at : (p.published_at || p.updated_at);
      const day = ts ? new Date(ts * 1000).toLocaleDateString(undefined, {weekday: 'long', day: 'numeric', month: 'long'}) : '';
      const head = PL.tab === 'scheduled' && day !== lastDay ? `<div class="daygroup">${day}</div>` : '';
      lastDay = day;
      return head + `<div class="selrow ${PL.sel.has(p.id) ? 'on' : ''}"><label class="selbox" title="Select"><input type="checkbox" ${PL.sel.has(p.id) ? 'checked' : ''} onchange="plToggle(${p.id},this.checked)"></label>${postRow(p)}</div>`;
    }).join('') : emptyState('list', {scheduled: 'Nothing scheduled', draft: 'No drafts', published: 'Nothing published yet', failed: 'No failed posts 🎉'}[PL.tab],
      PL.tab === 'failed' ? 'Everything went out fine.' : 'Create a post — or bulk schedule many videos at once.', PL.tab !== 'failed' ? `<button class="btn pri" onclick="openComposer()">${ic('plus')}Create post</button> <button class="btn" onclick="bulkDialog()">${ic('upload')}Bulk schedule</button>` : '')}</div>
    ${d.has_more ? `<div class="card-b" style="text-align:center"><button class="btn" onclick="PL.shown+=100;PAGES.posts.render($('#content'))">Show more (${d.total - d.posts.length} more)</button></div>` : d.total > 100 ? `<div class="card-b tiny muted" style="text-align:center">All ${d.total} shown</div>` : ''}</div>`;
  plBar();
  S.live = debounce(() => { if (S.page === 'posts' && !document.activeElement.matches('input') && !PL.sel.size) PAGES.posts.render(el); }, 100);
}};
function plToggle(id, on) { on ? PL.sel.add(id) : PL.sel.delete(id); const row = $(`#plList input[onchange="plToggle(${id},this.checked)"]`); if (row) row.closest('.selrow').classList.toggle('on', on); plBar(); }
function plSelectAll() { const all = PL.posts.length && PL.sel.size === PL.posts.length; PL.sel = new Set(all ? [] : PL.posts.map(p => p.id)); PAGES.posts.render($('#content')); }
function plBar() {
  const n = PL.sel.size, el = $('#plBar');
  if (!el) return;
  S.selecting = n > 0;
  el.innerHTML = n ? `<div class="bulkbar"><b>${n} selected</b><span class="sp"></span>
    ${PL.tab === 'failed' ? `<button class="btn sm" onclick="plAct('retry')">${ic('refresh', 14)}Retry</button>` : ''}
    ${['scheduled', 'draft'].includes(PL.tab) ? `<button class="btn sm" onclick="plShift()">${ic('clock', 14)}Move times…</button><button class="btn sm" onclick="plAct('now')">${ic('send', 14)}Publish now</button>` : ''}
    ${PL.tab === 'scheduled' ? `<button class="btn sm" onclick="plAct('unschedule')">${ic('file', 14)}Move to drafts</button>` : ''}
    <button class="btn sm danger" onclick="plAct('delete')">${ic('trash', 14)}Delete</button><button class="btn sm ghost" onclick="PL.sel.clear();PAGES.posts.render($('#content'))">Cancel</button></div>` : '';
}
async function plAct(action, extra = {}) {
  const n = PL.sel.size;
  const words = {delete: ['Delete', `Delete ${n} post(s)?`, 'They are removed from PostPilot. Posts already on the networks stay there.'],
    now: ['Publish now', `Publish ${n} post(s) now?`, 'They go out right away, one at a time per account (network limits are respected).'],
    unschedule: ['Move', `Move ${n} post(s) to drafts?`, 'They will not be published until you schedule them again.'],
    retry: ['Retry', `Retry ${n} failed post(s)?`, 'Failed accounts are tried again now.']}[action];
  if (words && !await confirmBox(words[1], words[2], words[0], action === 'delete')) return;
  await run(null, async () => {
    const r = await post('/api/posts/bulk-action', {ids: [...PL.sel], action, ...extra});
    toast(`${r.done} post(s) updated${r.skipped ? ` · ${r.skipped} skipped (already uploaded to the network or not possible)` : ''}`, r.skipped ? 'warn' : 'ok');
    PL.sel.clear(); PAGES.posts.render($('#content')); pollStatus();
  });
}
function plShift() {
  const m = modal({title: `Move ${PL.sel.size} post(s)`, body: `<p class="small muted" style="margin-top:0">Every selected post moves by the same amount. Posts already uploaded to YouTube/Facebook's own scheduler stay as they are.</p>
    <div class="row"><select class="in" id="shD" style="width:auto"><option value="1">Later</option><option value="-1">Earlier</option></select>
    <input class="in" type="number" id="shN" value="1" min="1" style="width:90px"><select class="in" id="shU" style="width:auto"><option value="60">hour(s)</option><option value="1440">day(s)</option><option value="1">minute(s)</option></select></div>`,
    foot: `<button class="btn" data-c>Cancel</button><button class="btn pri" id="shGo">Move</button>`});
  m.el.querySelector('[data-c]').onclick = m.close;
  $('#shGo').onclick = () => { const mins = +$('#shD').value * Math.max(1, +$('#shN').value) * +$('#shU').value; m.close(); plAct('shift', {minutes: mins}); };
}
const debouncedPosts = debounce(() => PAGES.posts.render($('#content')), 300);

// ================================================================== IDEAS
PAGES.ideas = {title: 'Ideas', sub: 'Capture ideas, develop them, turn them into posts', render: async el => {
  const d = await api('/api/ideas');
  const cols = [['idea', '💡 Ideas'], ['draft', '✍️ In progress'], ['ready', '✅ Ready to post']];
  el.innerHTML = `<div class="row mb"><button class="btn pri" onclick="editIdea()">${ic('plus', 16)}New idea</button><button class="btn soft" onclick="brainstorm(this)">${ic('spark', 16)}Brainstorm with AI</button></div>
    <div class="kan">${cols.map(([k, l]) => `<div class="kcol" data-stage="${k}"><h3>${l} <span class="pill">${d.ideas.filter(i => i.stage === k).length}</span></h3>
      ${d.ideas.filter(i => i.stage === k).map(i => `<div class="idea" draggable="true" data-id="${i.id}"><div class="t">${esc(i.title)}</div>${i.body ? `<div class="d">${esc(i.body)}</div>` : ''}
        <div class="a"><button class="btn sm soft" onclick="ideaToPost(${i.id})">${ic('send', 13)}Create post</button><span class="sp"></span><button class="btn sm ghost" onclick="editIdea(${i.id})">${ic('edit', 13)}</button>
        <button class="btn sm ghost" onclick="delIdea(${i.id})">${ic('trash', 13)}</button></div></div>`).join('')}
      <button class="btn ghost sm" style="width:100%" onclick="editIdea(null,'${k}')">${ic('plus', 14)}Add</button></div>`).join('')}</div>`;
  S.ideas = d.ideas;
  $$('.idea', el).forEach(c => c.ondragstart = e => e.dataTransfer.setData('text/plain', c.dataset.id));
  $$('.kcol', el).forEach(col => {
    col.ondragover = e => { e.preventDefault(); col.classList.add('drop'); };
    col.ondragleave = () => col.classList.remove('drop');
    col.ondrop = async e => { e.preventDefault(); col.classList.remove('drop'); const i = S.ideas.find(x => x.id === +e.dataTransfer.getData('text/plain')); if (!i) return;
      await post('/api/ideas', {...i, stage: col.dataset.stage}); PAGES.ideas.render(el); };
  });
}};
function editIdea(id, stage = 'idea') {
  const i = id ? S.ideas.find(x => x.id === id) : {title: '', body: '', stage};
  const m = modal({title: id ? 'Edit idea' : 'New idea', body: `<div class="field"><label class="f">Idea</label><input class="in" id="idT" value="${esc(i.title)}" placeholder="e.g. Behind the scenes of our packaging"></div>
    <div class="field"><label class="f">Notes / draft</label><textarea class="in" id="idB" rows="6" placeholder="Angles, links, draft text…">${esc(i.body)}</textarea></div>`,
    foot: `<button class="btn" data-c>Cancel</button><button class="btn pri" id="idS">Save</button>`});
  m.el.querySelector('[data-c]').onclick = m.close;
  setTimeout(() => $('#idT').focus(), 50);
  $('#idS').onclick = () => run($('#idS'), async () => { await post('/api/ideas', {id, title: $('#idT').value, body: $('#idB').value, stage: i.stage}); m.close(); PAGES.ideas.render($('#content')); });
}
async function delIdea(id) { await del('/api/ideas/' + id); PAGES.ideas.render($('#content')); }
function ideaToPost(id) { const i = S.ideas.find(x => x.id === id); openComposer({text: i.body || i.title, ideaId: id}); }
async function brainstorm(btn) {
  const m = modal({title: 'Brainstorm post ideas', body: `<div class="field"><label class="f">What is your business or topic?</label><input class="in" id="bsT" placeholder="e.g. handmade leather bags shop in Marrakech"></div><div id="bsOut"></div>`,
    foot: `<button class="btn" data-c>Close</button><button class="btn pri" id="bsGo">${ic('spark', 15)}Generate 10 ideas</button>`});
  m.el.querySelector('[data-c]').onclick = () => { m.close(); PAGES.ideas.render($('#content')); };
  setTimeout(() => $('#bsT').focus(), 50);
  $('#bsGo').onclick = () => run($('#bsGo'), async () => {
    const r = await post('/api/ai', {task: 'ideas', text: $('#bsT').value});
    $('#bsOut').innerHTML = `<div class="small muted mb-s">Click an idea to save it to your board:</div>` + (r.items || []).map(t => `<div class="act-row click" style="cursor:pointer" data-t="${esc(t)}"><div class="ic b">${ic('bulb', 14)}</div><div>${esc(t)}</div></div>`).join('');
    $$('#bsOut .act-row').forEach(row => row.onclick = async () => { await post('/api/ideas', {title: row.dataset.t}); row.style.opacity = .4; row.onclick = null; toast('Saved to Ideas', 'ok'); });
  });
  void btn;
}

// ================================================================== MEDIA
const MD = {q: '', kind: '', shown: 200, sel: new Set(), unused: false};
PAGES.media = {title: 'Media library', sub: 'Every image and video, reusable in any post', render: async el => {
  const d = await api(`/api/media?q=${encodeURIComponent(MD.q)}&kind=${MD.kind}&limit=${MD.shown}`);
  const list = MD.unused ? d.media.filter(m => !m.used) : d.media;
  el.innerHTML = `<label class="dropzone" id="dz">${ic('upload', 26).replace('class="i"', 'class="i" style="margin:0 auto 6px"')}<b>Drop images or videos here</b> or click to upload — as many as you like<div class="tiny">JPG, PNG, GIF, WEBP, MP4, MOV… — uploaded one by one with progress · resized automatically for each network</div><input type="file" multiple hidden id="mdFile" accept="image/*,video/*"></label>
    <div class="row mb"><div class="search" style="flex:1;max-width:300px">${ic('search', 15)}<input class="in sm" placeholder="Search by name or alt text" value="${esc(MD.q)}" oninput="MD.q=this.value;debouncedMedia()"></div>
      <div class="chips">${[['', 'All'], ['video', 'Videos'], ['image', 'Photos']].map(([k, l]) => `<button class="chip ${MD.kind === k ? 'on' : ''}" onclick="MD.kind='${k}';MD.shown=200;route()">${l}</button>`).join('')}
      <button class="chip ${MD.unused ? 'on' : ''}" onclick="MD.unused=!MD.unused;route()" title="Files no post uses yet">Not used yet</button></div>
      <span class="muted small">${d.total} file(s)</span><span class="sp"></span>
      ${list.length ? `<button class="btn sm" onclick="mdSelectAll()">${MD.sel.size && list.every(m => MD.sel.has(m.id)) ? 'Unselect all' : 'Select all shown'}</button>` : ''}
      <button class="btn pri sm" ${MD.sel.size ? '' : 'disabled'} onclick="bulkDialog([...MD.sel])">${ic('cal', 14)}Schedule ${MD.sel.size || ''} selected</button></div>
    ${list.length ? `<div class="mgrid">${list.map(m => `<div class="mcard ${MD.sel.has(m.id) ? 'picked' : ''}" data-mid="${m.id}"><label class="mpick" onclick="event.stopPropagation()"><input type="checkbox" ${MD.sel.has(m.id) ? 'checked' : ''} onchange="mdToggle(${m.id},this.checked)"></label><div class="img" onclick="mediaDetail(${m.id})">${mthumb(m)}</div>
      <div class="cap" onclick="mediaDetail(${m.id})"><div class="ell b">${esc(m.name)}</div><div class="tiny muted">${m.kind}${m.width ? ` · ${m.width}×${m.height}` : ''} · ${(m.size / 1048576).toFixed(1)} MB${m.used ? ` · used ${m.used}×` : ' · <b style="color:var(--brand)">new</b>'}</div></div></div>`).join('')}</div>
      ${d.has_more ? `<div style="text-align:center" class="mt"><button class="btn" onclick="MD.shown+=200;PAGES.media.render($('#content'))">Show more (${d.total - d.media.length} more)</button></div>` : ''}` : emptyState('image', 'No media yet', 'Upload your photos and videos once — reuse them in any post.')}`;
  S.media = d.media;
  MD.list = list;
  const dz = $('#dz');
  $('#mdFile').onchange = e => uploadFiles(e.target.files).then(() => PAGES.media.render(el));
  dz.ondragover = e => { e.preventDefault(); dz.classList.add('on'); };
  dz.ondragleave = () => dz.classList.remove('on');
  dz.ondrop = e => { e.preventDefault(); dz.classList.remove('on'); uploadFiles(e.dataTransfer.files).then(() => PAGES.media.render(el)); };
}};
const debouncedMedia = debounce(() => PAGES.media.render($('#content')), 300);
function mdToggle(id, on) { on ? MD.sel.add(id) : MD.sel.delete(id); const c = $(`.mcard[data-mid="${id}"]`); if (c) c.classList.toggle('picked', on); const b = $$('#content .btn.pri.sm').find(x => x.textContent.includes('selected')); if (b) { b.disabled = !MD.sel.size; b.innerHTML = `${ic('cal', 14)}Schedule ${MD.sel.size || ''} selected`; } }
function mdSelectAll() { const all = MD.list.every(m => MD.sel.has(m.id)); MD.list.forEach(m => all ? MD.sel.delete(m.id) : MD.sel.add(m.id)); PAGES.media.render($('#content')); }
// uploads: one file per request, 3 at the same time, each with its own progress — a bad file never stops the others
function uploadOne(f, onProgress) {
  return new Promise(res => {
    const fd = new FormData(); fd.append('files', f);
    const x = new XMLHttpRequest();
    x.open('POST', '/api/media'); x.setRequestHeader('X-PP', '1');
    x.upload.onprogress = e => { if (e.lengthComputable) onProgress(e.loaded / e.total); };
    x.onload = () => { let d = {}; try { d = JSON.parse(x.responseText); } catch (e) { /* not json */ } res(x.status < 300 ? {media: d.media || [], errors: d.errors || []} : {media: [], errors: [d.error || `${f.name}: upload failed (${x.status})`]}); };
    x.onerror = () => res({media: [], errors: [`${f.name}: PostPilot is not reachable`]});
    x.send(fd);
  });
}
async function uploadFiles(files) {
  files = [...(files || [])];
  if (!files.length) return [];
  const box = document.createElement('div'); box.className = 'toast upbox';
  $('#toasts').appendChild(box);
  const prog = files.map(() => 0), state = files.map(() => 'wait');
  const total = files.reduce((a, f) => a + f.size, 0) || 1;
  const draw = () => {
    const sent = files.reduce((a, f, i) => a + f.size * prog[i], 0), done = state.filter(s => s === 'ok' || s === 'err').length;
    box.innerHTML = `<div style="flex:1;min-width:0"><div class="row nw"><span class="spin"></span><b>Uploading ${done}/${files.length}</b><span class="sp"></span><span class="tiny">${Math.round(sent / total * 100)}%</span></div>
      <div class="upbar"><i style="width:${(sent / total * 100).toFixed(1)}%"></i></div>
      <div class="uplist">${files.map((f, i) => state[i] === 'up' ? `<div class="tiny ell">${esc(f.name)} · ${Math.round(prog[i] * 100)}%</div>` : '').join('')}</div></div>`;
  };
  const out = [], errs = [];
  let next = 0;
  const worker = async () => {
    while (next < files.length) {
      const i = next++;
      state[i] = 'up'; draw();
      const r = await uploadOne(files[i], p => { prog[i] = p; draw(); });
      prog[i] = 1; state[i] = r.errors.length ? 'err' : 'ok';
      out.push(...r.media); errs.push(...r.errors); draw();
    }
  };
  draw();
  await Promise.all([worker(), worker(), worker()]);
  box.remove();
  errs.slice(0, 5).forEach(e => toast(e, 'err'));
  if (errs.length > 5) toast(`${errs.length - 5} more file(s) could not be added`, 'err');
  if (out.length) toast(`${out.length} file(s) added`, 'ok');
  return out;
}
function mediaDetail(id) {
  const m = S.media.find(x => x.id === id);
  const md = modal({title: esc(m.name), size: 'mid', body: `<div style="text-align:center;background:var(--surface-3);border-radius:14px;padding:10px" class="mb">${m.kind === 'video' ? `<video src="${m.url}" ${m.poster ? `poster="${m.poster}"` : ''} preload="metadata" controls style="max-width:100%;max-height:50vh"></video>` : `<img src="${m.url}" style="max-width:100%;max-height:50vh;border-radius:10px">`}</div>
    <div class="field"><label class="f">Alt text (describes the image for blind users — also helps reach)</label><textarea class="in" id="mdAlt" rows="2">${esc(m.alt)}</textarea></div>
    <div class="small muted">${m.kind} · ${m.width ? m.width + '×' + m.height + ' · ' : ''}${(m.size / 1048576).toFixed(2)} MB · added ${fdate(m.created_at)}</div>`,
    foot: `<button class="btn danger" style="margin-right:auto" id="mdDel">${ic('trash', 15)}Delete</button><button class="btn" id="mdUse">${ic('send', 15)}Use in a post</button><button class="btn pri" id="mdSave">Save</button>`});
  $('#mdSave').onclick = () => run($('#mdSave'), async () => { await post(`/api/media/${id}`, {alt: $('#mdAlt').value}); md.close(); toast('Saved', 'ok'); PAGES.media.render($('#content')); });
  $('#mdUse').onclick = () => { md.close(); openComposer({media: [m]}); };
  $('#mdDel').onclick = async () => { if (!await confirmBox('Delete this file?', 'It is removed from the library.', 'Delete', true)) return; await run(null, async () => { await del('/api/media/' + id); md.close(); PAGES.media.render($('#content')); }); };
}

// ================================================================== ANALYTICS
const AN = {days: 30};
PAGES.analytics = {title: 'Analytics', sub: 'What works, where and when', render: async el => {
  const d = await api('/api/analytics?days=' + AN.days);
  const t = d.totals;
  el.innerHTML = `<div class="row mb"><div class="chips">${[7, 30, 90, 365].map(n => `<button class="chip ${AN.days === n ? 'on' : ''}" onclick="AN.days=${n};route()">${n === 365 ? '1 year' : n + ' days'}</button>`).join('')}</div><span class="sp"></span>
      <button class="btn sm" onclick="run(this,async()=>{const r=await post('/api/analytics/refresh');toast('Updated stats for '+r.updated+' post(s)','ok');route()})">${ic('refresh', 14)}Refresh stats</button></div>
    <div class="kpis">${kpi('send', 'b', 'Posts published', d.total_posts, `last ${AN.days} days`)}${kpi('heart', 'r', 'Likes & reactions', nf(t.likes), 'all networks')}
      ${kpi('chat', 's', 'Comments', nf(t.comments), 'replies')}${kpi('share', 'g', 'Shares', nf(t.shares), 'reposts & saves')}${kpi('eye', 'a', 'Views', nf(t.views), 'where networks report them')}</div>
    <div class="grid g-side"><div>
      <div class="card"><div class="card-h"><h2>Posts & engagement</h2><div class="act legend"><span><i style="background:var(--brand)"></i>Engagement</span><span><i style="background:#c4bcff"></i>Posts</span></div></div><div class="card-b">${lineChart(d.series)}</div></div>
      <div class="card"><div class="card-h"><h2>Top posts</h2></div>${d.top.length ? `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Post</th><th>Network</th><th class="num">♥</th><th class="num">💬</th><th class="num">↻</th><th class="num">Views</th><th></th></tr></thead><tbody>
        ${d.top.map(p => `<tr><td><div class="row nw">${mthumb(p.media, 'style="width:38px;height:38px;border-radius:8px;object-fit:cover;flex:0 0 auto;background:var(--surface-3)"')}<div class="ell" style="max-width:320px">${esc(p.text)}</div></div><div class="tiny muted">${fdate(p.published_at)}</div></td>
        <td>${nb(p.network)}</td><td class="num">${nf(p.metrics.likes)}</td><td class="num">${nf(p.metrics.comments)}</td><td class="num">${nf(p.metrics.shares)}</td><td class="num">${nf(p.metrics.views)}</td><td>${p.url ? `<a class="btn sm ghost" target="_blank" href="${esc(p.url)}">${ic('ext', 14)}</a>` : ''}</td></tr>`).join('')}</tbody></table></div>`
        : '<div class="empty small">Results appear here a few hours after publishing.</div>'}</div>
    </div><div>
      <div class="card"><div class="card-h"><h2>By network</h2></div><div class="card-b" style="padding-top:4px;padding-bottom:4px">${d.networks.length ? d.networks.map(n => `<div class="act-row"><span class="nb" style="background:${n.color};width:28px;height:28px;border-radius:9px">${esc(netMeta(n.network).abbr)}</span>
        <div style="flex:1"><div class="b">${esc(n.name)}</div><div class="tiny muted">${n.posts} posts · ${nf(n.views)} 👁 · ${nf(n.likes)} ♥ · ${nf(n.comments)} 💬 · ${nf(n.shares)} ↻</div></div><div style="text-align:right"><div class="b">${n.eng_per_post}</div><div class="tiny muted">per post</div></div></div>`).join('') : '<div class="muted small" style="padding:14px 0">No data yet.</div>'}</div></div>
      <div class="card"><div class="card-h"><h2>Best times</h2><div class="sub">avg. engagement by day & hour</div></div><div class="card-b">${heatmap(d.heat)}</div></div>
    </div></div>`;
}};
function lineChart(series) {
  const W = 720, H = 230, P = 30, n = series.length, step = (W - P * 2) / Math.max(1, n - 1);
  const me = Math.max(5, ...series.map(s => s.engagement)), mp = Math.max(2, ...series.map(s => s.posts));
  const y = v => H - 24 - (v / me) * (H - 44);
  const bw = Math.max(2, (W - P * 2) / n * .5);
  const bars = series.map((s, i) => s.posts ? `<rect x="${P + i * step - bw / 2}" y="${H - 24 - (s.posts / mp) * (H - 60)}" width="${bw}" height="${(s.posts / mp) * (H - 60)}" rx="2" fill="#c4bcff"><title>${s.day}: ${s.posts} posts</title></rect>` : '').join('');
  const path = series.map((s, i) => `${i ? 'L' : 'M'}${P + i * step},${y(s.engagement)}`).join(' ');
  const area = `${path} L${P + (n - 1) * step},${H - 24} L${P},${H - 24}Z`;
  const labels = series.map((s, i) => (i % Math.ceil(n / 8) === 0 || i === n - 1) ? `<text x="${P + i * step}" y="${H - 6}" text-anchor="middle">${s.day}</text>` : '').join('');
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none"><defs><linearGradient id="ga" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#6d5dfc" stop-opacity=".25"/><stop offset="1" stop-color="#6d5dfc" stop-opacity="0"/></linearGradient></defs>
    ${[0, .5, 1].map(f => `<line x1="${P}" x2="${W - P}" y1="${y(me * f)}" y2="${y(me * f)}" stroke="var(--line)" stroke-dasharray="3 4"/>`).join('')}${bars}<path d="${area}" fill="url(#ga)"/><path d="${path}" fill="none" stroke="#6d5dfc" stroke-width="2.5" stroke-linejoin="round"/>${labels}</svg>`;
}
function heatmap(heat) {
  const mx = Math.max(...heat, 0.001);
  let g = '<div></div>' + Array.from({length: 24}, (_, h) => `<div class="hh">${h % 3 === 0 ? h : ''}</div>`).join('');
  for (let d = 0; d < 7; d++) {
    g += `<div class="hl">${DAYS[d]}</div>`;
    for (let h = 0; h < 24; h++) { const v = heat[d * 24 + h]; g += `<div class="c" style="${v ? `background:rgba(109,93,252,${(.15 + .85 * v / mx).toFixed(2)})` : ''}" title="${DAYS[d]} ${h}:00 — ${v} avg engagement"></div>`; }
  }
  return `<div class="heat">${g}</div><div class="tiny muted mt-s">Darker = more engagement per post. Fills in as you publish.</div>`;
}

// ================================================================== ACTIVITY
PAGES.activity = {title: 'Activity', sub: 'Everything PostPilot did', render: async el => {
  const d = await api('/api/activity');
  el.innerHTML = `<div class="card"><div class="card-b" style="padding-top:4px;padding-bottom:4px">${d.items.length ? d.items.map(a => actRow(a).replace(`>${rel(a.ts)}<`, `>${fdate(a.ts)}<`)).join('') : '<div class="empty">Nothing yet.</div>'}</div></div>`;
  S.live = debounce(() => { if (S.page === 'activity') PAGES.activity.render(el); }, 100);
}};

// ================================================================== REMINDERS
async function openReminders() {
  const d = await api('/api/reminders');
  if (!d.reminders.length) return toast('No reminders right now — posts for TikTok, WhatsApp… appear here at their scheduled time.');
  const m = modal({title: `⏰ Ready to post manually (${d.reminders.length})`, size: 'mid', body: d.reminders.map(r => `<div class="card" style="box-shadow:none" data-t="${r.target_id}"><div class="card-h"><h2>${esc(r.account)}</h2><span class="pill a">${esc(r.app)}</span><div class="act tiny muted">due ${fdate(r.due)}</div></div>
    <div class="card-b"><div style="white-space:pre-wrap;background:var(--surface-2);border-radius:10px;padding:12px" class="mb">${esc(r.text)}</div>
    ${r.media.length ? `<div class="mstrip mb">${r.media.map(x => `<a class="mitem" href="${x.url}" download="${esc(x.name)}" title="Download">${mthumb(x)}${x.kind === 'video' ? '<span class="vid">VIDEO</span>' : ''}</a>`).join('')}</div>` : ''}
    <div class="row"><button class="btn" data-copy="${r.target_id}">${ic('copy', 15)}Copy caption</button>${r.media.map(x => `<a class="btn" href="${x.url}" download="${esc(x.name)}">${ic('download', 15)}${esc(x.name.slice(0, 18))}</a>`).join('')}
    <span class="sp"></span><button class="btn pri" data-done="${r.target_id}">${ic('check', 15)}Mark as posted</button></div></div></div>`).join(''), foot: '<button class="btn" data-c>Close</button>'});
  m.el.querySelector('[data-c]').onclick = m.close;
  $$('[data-copy]', m.el).forEach(b => b.onclick = () => copyText(d.reminders.find(r => r.target_id === +b.dataset.copy).text));
  $$('[data-done]', m.el).forEach(b => b.onclick = async () => { await post(`/api/targets/${b.dataset.done}/done`); b.closest('.card').remove(); pollStatus(); if (!$$('[data-done]', m.el).length) m.close(); });
}

// ------------------------------------------------------------------ boot
(function boot() {
  let t = 'light';
  try { t = localStorage.getItem('pp-theme') || t; } catch (e) { /* ignore */ }
  applyTheme(t);
  Promise.all([pollStatus(), loadAccounts()]).then(() => { if (S.status && S.status.theme && S.status.theme !== t) applyTheme(S.status.theme); route(); });
  setInterval(() => { if (!document.hidden) pollStatus(); }, 4000);
})();
