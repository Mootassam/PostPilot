/* PostPilot 1.1 — Unified inbox, AI content calendar, account health, login expiry and posting limits. */
'use strict';

// ================================================================== UNIFIED INBOX
const IBX = {status: 'needs', account: '', q: '', sel: null};
PAGES.inbox = {title: 'Inbox', sub: 'Comments, mentions and messages from all your networks', render: async el => {
  const d = await api(`/api/inbox?status=${IBX.status}&account=${IBX.account}&q=${encodeURIComponent(IBX.q)}`);
  IBX.items = d.items;
  const c = d.counts;
  const unsupported = d.sources.filter(s => !s.supported);
  const errs = d.sources.filter(s => s.supported && s.error);
  el.innerHTML = `<div class="row mb"><div class="chips">${[['needs', 'Needs reply', c.needs], ['new', 'Unread', c.new], ['all', 'All'], ['replied', 'Replied', c.replied], ['archived', 'Archived', c.archived]]
      .map(([k, l, n]) => `<button class="chip ${IBX.status === k ? 'on' : ''}" onclick="IBX.status='${k}';IBX.sel=null;route()">${l}${n ? ` <span class="n">${n}</span>` : ''}</button>`).join('')}</div>
    <select class="in sm" style="width:auto" onchange="IBX.account=this.value;route()"><option value="">All accounts</option>${d.sources.filter(s => s.supported).map(s => `<option value="${s.id}" ${String(s.id) === IBX.account ? 'selected' : ''}>${esc(s.network_name)} · ${esc(s.name)}</option>`).join('')}</select>
    <div class="search">${ic('search', 15)}<input class="in sm" placeholder="Search" value="${esc(IBX.q)}" oninput="IBX.q=this.value;debouncedInbox()"></div><span class="sp"></span>
    <button class="btn sm" onclick="inboxSourcesInfo()">${ic('alert', 14)}Sources</button><button class="btn pri sm" onclick="inboxSync(this)">${ic('refresh', 14)}Check now</button></div>
    ${errs.map(s => `<div class="alert a small">${nb(s.network)}<div><b>${esc(s.name)}:</b> ${esc(s.error)}</div></div>`).join('')}
    <div class="card" style="display:grid;grid-template-columns:minmax(0,380px) minmax(0,1fr);min-height:62vh;overflow:hidden">
      <div style="border-right:1px solid var(--line);overflow-y:auto;max-height:72vh" id="ibxList">${d.items.length ? d.items.map(ibxRow).join('')
        : `<div class="empty small">${ic('chat', 26).replace('class="i"', 'class="i" style="margin:0 auto 8px"')}${IBX.status === 'needs' ? 'All caught up — nothing waiting for a reply. 🎉' : 'Nothing here yet.'}<br><span class="muted">PostPilot checks your networks every few minutes.</span></div>`}</div>
      <div id="ibxThread" style="display:flex;flex-direction:column;min-height:0"></div></div>
    ${unsupported.length ? `<div class="tiny muted">Not available in the inbox: ${unsupported.map(s => `${esc(s.network_name)} (${esc(s.note || 'no API')})`).join(' · ')}</div>` : ''}`;
  if (IBX.sel && d.items.some(i => i.id === IBX.sel)) openIbx(IBX.sel); else ibxEmpty();
  S.live = debounce(async () => { if (S.page !== 'inbox' || document.activeElement.id === 'ibxReply') return; PAGES.inbox.render(el); }, 100);
}};
const debouncedInbox = debounce(() => PAGES.inbox.render($('#content')), 300);
const TAGS = {question: ['b', '❓ Question'], complaint: ['r', '⚠ Complaint'], positive: ['g', '😊 Positive']};
const KINDS = {comment: 'Comment', reply: 'Reply', mention: 'Mention', dm: 'Message'};
function ibxRow(i) {
  const t = TAGS[i.tag];
  return `<div class="accrow" style="cursor:pointer;padding:12px 14px;${IBX.sel === i.id ? 'background:var(--brand-l)' : ''}" onclick="openIbx(${i.id})">
    <div class="av sm">${i.author_avatar ? `<img src="${esc(i.author_avatar)}" referrerpolicy="no-referrer">` : `<div class="ini">${esc(initials(i.author_name))}</div>`}${nb(i.network)}</div>
    <div style="min-width:0;flex:1"><div class="row nw" style="gap:6px"><b class="ell" style="font-size:13px">${esc(i.author_name)}</b>${i.status === 'new' ? '<span class="pill b" style="font-size:10px">NEW</span>' : ''}<span class="tiny muted" style="margin-left:auto;white-space:nowrap">${rel(i.created_at)}</span></div>
      <div class="small ell" style="color:var(--ink-2)">${esc(i.text)}</div>
      <div class="row tiny muted" style="gap:6px;margin-top:2px">${KINDS[i.kind] || i.kind} · ${esc(i.account_name)}${t ? ` <span class="pill ${t[0]}" style="font-size:10px">${t[1]}</span>` : ''}${i.status === 'replied' ? ' <span class="pill g" style="font-size:10px">✓ replied</span>' : ''}</div></div></div>`;
}
function ibxEmpty() { $('#ibxThread').innerHTML = `<div class="empty" style="margin:auto">${ic('chat', 30).replace('class="i"', 'class="i" style="margin:0 auto 10px;color:var(--brand)"')}<h3>Your unified inbox</h3><p>Pick a conversation on the left. Reply with one click — or let the AI suggest an answer.</p></div>`; }
async function openIbx(id) {
  IBX.sel = id;
  $$('#ibxList .accrow').forEach(r => r.style.background = r.getAttribute('onclick') === `openIbx(${id})` ? 'var(--brand-l)' : '');
  const d = await api('/api/inbox/' + id);
  const it = d.item;
  const thread = d.thread.length ? d.thread : [it];
  const bubble = m => {
    const mine = m.status === 'mine';
    return `<div style="align-self:${mine ? 'flex-end' : 'flex-start'};max-width:78%;margin:4px 0"><div class="tiny muted" style="margin:0 4px 2px;${mine ? 'text-align:right' : ''}">${esc(mine ? 'You' : m.author_name)} · ${fdate(m.created_at)}</div>
      <div style="background:${mine ? 'var(--brand)' : 'var(--surface-3)'};color:${mine ? '#fff' : 'var(--ink)'};padding:9px 13px;border-radius:14px;white-space:pre-wrap;${m.id === it.id ? 'box-shadow:0 0 0 2px var(--brand-ring)' : ''}">${esc(m.text)}</div>
      ${m.reply_text ? `<div style="margin-top:6px;margin-left:24px;background:var(--brand);color:#fff;padding:9px 13px;border-radius:14px;white-space:pre-wrap">${esc(m.reply_text)}<div class="tiny" style="opacity:.75">You · ${fdate(m.replied_at)}</div></div>` : ''}</div>`;
  };
  const canReply = S.nets[it.network] && (S.nets[it.network].inbox || []).length;
  $('#ibxThread').innerHTML = `<div class="row nw" style="padding:12px 16px;border-bottom:1px solid var(--line);gap:10px">${nb(it.network)}<div style="min-width:0"><b>${esc(it.author_name)}</b> <span class="muted small">${esc(it.author_handle || '')}</span>
      <div class="tiny muted">${KINDS[it.kind] || it.kind} on ${esc(it.network_name)} · ${esc(it.account_name)}</div></div><span class="sp"></span>
      ${it.url ? `<a class="btn sm" href="${esc(it.url)}" target="_blank">${ic('ext', 14)}Open</a>` : ''}
      <button class="btn sm ghost" title="Mark unread" onclick="ibxSet(${it.id},'new')">${ic('bell', 14)}</button><button class="btn sm ghost" title="Archive" onclick="ibxSet(${it.id},'archived')">${ic('check', 14)}Done</button></div>
    ${it.post_text ? `<div style="padding:10px 16px;background:var(--surface-2);border-bottom:1px solid var(--line)" class="small"><span class="muted">On your post:</span> ${esc(it.post_text.slice(0, 160))}${it.post_url ? ` <a href="${esc(it.post_url)}" target="_blank">view</a>` : ''}</div>` : ''}
    <div style="flex:1;overflow-y:auto;padding:14px 16px;display:flex;flex-direction:column">${thread.map(bubble).join('')}</div>
    <div style="border-top:1px solid var(--line);padding:12px 14px">${canReply ? `<textarea class="in" id="ibxReply" rows="3" placeholder="Write a reply…">${it.status === 'replied' ? '' : ''}</textarea>
      <div class="row mt-s"><button class="btn soft sm" onclick="ibxSuggest(this,${it.id})">${ic('spark', 14)}Suggest reply</button><button class="btn ghost sm" onclick="ibxTemplates(this)">${ic('file', 14)}Templates</button>
      <span class="sp"></span><span class="tiny muted">Ctrl+Enter to send</span><button class="btn pri" id="ibxSend" onclick="ibxSend(${it.id})">${ic('send', 15)}Reply</button></div>`
      : '<div class="small muted">Replying from PostPilot is not available for this network.</div>'}</div>`;
  const ta = $('#ibxReply');
  if (ta) ta.onkeydown = e => { if (e.key === 'Enter' && e.ctrlKey) ibxSend(it.id); };
  IBX.cur = it;
  pollStatus();
}
async function ibxSet(id, status) { await post(`/api/inbox/${id}`, {status}); toast(status === 'archived' ? 'Marked as done' : 'Marked as unread', 'ok'); IBX.sel = null; PAGES.inbox.render($('#content')); pollStatus(); }
async function ibxSend(id) {
  const text = ($('#ibxReply').value || '').trim();
  if (!text) return toast('Write a reply first', 'warn');
  await run($('#ibxSend'), async () => { await post(`/api/inbox/${id}/reply`, {text}); toast('Reply sent ✅', 'ok'); PAGES.inbox.render($('#content')); pollStatus(); });
}
async function ibxSuggest(btn, id) {
  const it = IBX.cur;
  await run(btn, async () => { const r = await post('/api/ai', {task: 'reply', text: it.text, extra: it.post_text, network: it.network}); $('#ibxReply').value = r.text; $('#ibxReply').focus(); });
}
async function ibxTemplates(btn) {
  const d = await api('/api/templates');
  popMenu(btn, d.items.length ? d.items.map(t => ({label: t.name, icon: 'file', run: () => { $('#ibxReply').value = t.text; $('#ibxReply').focus(); }})) : [{label: 'No templates yet', run: () => {}}]);
}
async function inboxSync(btn) {
  await run(btn, async () => {
    const r = await post('/api/inbox/sync');
    toast(r.new ? `${r.new} new message(s)` : 'No new messages', r.new ? 'ok' : '');
    r.errors.forEach(e => toast(e, 'warn'));
    PAGES.inbox.render($('#content')); pollStatus();
  });
}
async function inboxSourcesInfo() {
  const d = await api('/api/inbox?status=all');
  const m = modal({title: 'Inbox sources', size: 'mid', body: d.sources.length ? d.sources.map(s => `<div class="act-row">${nb(s.network)}<div style="flex:1;min-width:0"><b>${esc(s.name)}</b> <span class="muted small">${esc(s.network_name)}</span>
      <div class="tiny muted">${esc(s.note)}</div>${s.error ? `<div class="tiny" style="color:var(--amber-d)">${esc(s.error)}</div>` : ''}</div>
      <div>${s.supported ? `<span class="pill ${s.error ? 'a' : 'g'}">${s.synced_at ? 'checked ' + rel(s.synced_at) : 'waiting'}</span>` : '<span class="pill">not available</span>'}</div></div>`).join('') : '<div class="muted">Connect accounts first.</div>',
    foot: '<button class="btn" data-c>Close</button>'});
  m.el.querySelector('[data-c]').onclick = m.close;
}

// ================================================================== AI CONTENT CALENDAR
async function planWizard() {
  const plans = (await api('/api/plans')).plans;
  const last = plans[0] ? plans[0].brief : {};
  const tomorrow = new Date(Date.now() + 86400000);
  const iso = new Date(tomorrow - tomorrow.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
  const m = modal({title: `${icInline('spark', 17)} AI content calendar`, size: 'wide', body: `<div id="plStep">
    <div class="grid g2" style="gap:14px">
      <div class="field"><label class="f">Your business — what you sell and what makes it special</label><textarea class="in" id="plBiz" rows="3" placeholder="e.g. Café Atlas — specialty coffee and homemade pastries in Rabat, cozy terrace, fast Wi-Fi">${esc(last.business || '')}</textarea></div>
      <div><div class="field"><label class="f">Audience</label><input class="in" id="plAud" value="${esc(last.audience || '')}" placeholder="e.g. students, remote workers, families"></div>
        <div class="field"><label class="f">Goals</label><input class="in" id="plGoal" value="${esc(last.goals || '')}" placeholder="e.g. more visits on weekdays, promote brunch"></div></div>
    </div>
    <div class="field"><label class="f">Special dates, offers or news this period (optional)</label><input class="in" id="plEv" value="" placeholder="e.g. Oct 15 launch of pumpkin latte · weekend brunch -20% · new opening hours"></div>
    <div class="grid g4" style="gap:12px">
      <div class="field"><label class="f">Start</label><input type="date" class="in" id="plStart" value="${iso}"></div>
      <div class="field"><label class="f">Length</label><select class="in" id="plWeeks">${[1, 2, 3, 4].map(w => `<option value="${w}" ${w === (last.weeks || 4) ? 'selected' : ''}>${w} week${w > 1 ? 's' : ''}</option>`).join('')}</select></div>
      <div class="field"><label class="f">Posts per week</label><select class="in" id="plPer">${[2, 3, 4, 5, 7, 10, 14].map(n => `<option ${n === (last.per_week || 4) ? 'selected' : ''}>${n}</option>`).join('')}</select></div>
      <div class="field"><label class="f">Language</label><select class="in" id="plLang">${['English', 'French', 'Arabic', 'Spanish', 'German', 'Moroccan Darija', 'Portuguese', 'Italian', 'Turkish'].map(l => `<option ${l === (last.language || 'English') ? 'selected' : ''}>${l}</option>`).join('')}</select></div>
    </div>
    <div class="grid g2" style="gap:12px"><div class="field"><label class="f">Tone</label><select class="in" id="plTone">${['friendly and professional', 'fun and casual', 'luxury and elegant', 'expert and educational', 'bold and energetic'].map(t => `<option ${t === last.tone ? 'selected' : ''}>${t}</option>`).join('')}</select></div>
      <div class="field"><label class="f">Plan for these accounts</label><div>${S.accounts.filter(a => a.network !== 'reminder' || true).map(a => `<label class="accpick" style="display:inline-flex;margin:2px;padding:4px 8px"><input type="checkbox" value="${a.id}" checked style="accent-color:var(--brand)">${nb(a.network)} <span class="small">${esc(a.name)}</span></label>`).join('') || '<span class="small muted">No accounts yet — you can still plan and save to Ideas.</span>'}</div></div></div>
    <div class="small muted">${{anthropic: '✨ Claude will write your plan (about 30–60 seconds).', ollama: '✨ Your local AI will write the plan (can take a few minutes).', basic: 'No AI connected — you get a ready structure with fill-in templates. Add a Claude key in Settings for complete captions.'}[S.status.ai] || ''}</div>
    ${plans.length ? `<hr class="sep"><div class="small b mb-s">Recent plans</div><div class="chips">${plans.map(p => `<button class="chip" data-plan="${p.id}">${esc(p.title.slice(0, 30))} · ${p.items.length} posts · ${rel(p.created_at)}</button>`).join('')}</div>` : ''}
  </div>`, foot: `<button class="btn" data-c>Close</button><button class="btn pri lg" id="plGo">${ic('spark', 16)}Create my plan</button>`});
  m.el.querySelector('[data-c]').onclick = m.close;
  $$('[data-plan]', m.el).forEach(b => b.onclick = () => { const p = plans.find(x => x.id === +b.dataset.plan); showPlan(m, p.items.map(it => ({...it, ts: Math.max(it.ts || 0, now() + 1800)})), (p.brief.accounts || []).map(Number)); });
  $('#plGo').onclick = () => run($('#plGo'), async () => {
    const accounts = $$('#plStep .accpick input', m.el).filter(x => x.checked).map(x => +x.value);
    $('#plGo').innerHTML = '<span class="spin"></span>Planning…';
    const r = await post('/api/plan/generate', {business: $('#plBiz').value, audience: $('#plAud').value, goals: $('#plGoal').value, events: $('#plEv').value,
      start: Math.floor(new Date($('#plStart').value + 'T00:00') / 1000), weeks: +$('#plWeeks').value, per_week: +$('#plPer').value,
      language: $('#plLang').value, tone: $('#plTone').value, accounts});
    showPlan(m, r.items, accounts);
  });
}
function showPlan(m, items, accounts) {
  const toLocal = ts => new Date(ts * 1000 - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  m.el.querySelector('.modal-b').innerHTML = `<div class="row mb"><b>${items.length} posts planned</b><span class="muted small">— edit anything, untick what you don't want</span><span class="sp"></span>
      <label class="switch small"><input type="checkbox" id="plTags" checked><span class="tr"></span>Add the hashtags</label></div>
    <div style="max-height:62vh;overflow-y:auto;padding-right:4px">${items.map((it, i) => `<div class="card" style="box-shadow:none;margin-bottom:10px" data-i="${i}"><div class="card-b" style="padding:12px 14px">
      <div class="row" style="gap:8px;margin-bottom:6px"><input type="checkbox" class="plOn" checked style="accent-color:var(--brand);width:17px;height:17px"><input type="datetime-local" class="in sm plTs" value="${toLocal(it.ts)}" style="width:auto">
        <span class="pill b">${esc(it.pillar || 'Post')}</span><b class="small">${esc(it.topic)}</b><span class="pill">${esc(it.format)}</span></div>
      <textarea class="in plCap" rows="4">${esc(it.caption)}</textarea>
      <div class="grid g2 mt-s" style="gap:8px"><input class="in sm plHash" value="${esc(it.hashtags)}" placeholder="#hashtags"><div class="small muted" style="align-self:center">📷 ${esc(it.media_idea || '—')}</div></div></div></div>`).join('')}</div>`;
  const foot = m.el.querySelector('.modal-f');
  foot.innerHTML = `<button class="btn" id="plBack">← Back</button><span class="sp"></span><button class="btn" id="plIdeas">${ic('bulb', 15)}Save to Ideas</button><button class="btn" id="plDraft">Save as drafts</button><button class="btn pri" id="plSched">${ic('cal', 15)}Schedule selected</button>`;
  $('#plBack').onclick = () => { m.close(); planWizard(); };
  const collect = () => $$('[data-i]', m.el).filter(cd => $('.plOn', cd).checked).map(cd => {
    const it = items[+cd.dataset.i];
    return {...it, caption: $('.plCap', cd).value, hashtags: $('.plHash', cd).value, ts: Math.floor(new Date($('.plTs', cd).value) / 1000)};
  });
  const apply = (mode, btn) => run(btn, async () => {
    const sel = collect();
    if (!sel.length) throw new Error('Tick at least one post.');
    if (mode === 'schedule' && !accounts.length) throw new Error('Go back and choose the accounts to post to.');
    const r = await post('/api/plan/apply', {items: sel, accounts, mode, hashtags: $('#plTags').checked});
    m.close();
    toast(mode === 'ideas' ? `${r.created} ideas saved` : mode === 'draft' ? `${r.created} drafts saved` : `${r.created} posts scheduled 🎉`, 'ok');
    if (mode === 'ideas') go('ideas'); else if (S.page === 'calendar') route(); else go('calendar');
    pollStatus();
  });
  $('#plIdeas').onclick = () => apply('ideas', $('#plIdeas'));
  $('#plDraft').onclick = () => apply('draft', $('#plDraft'));
  $('#plSched').onclick = () => apply('schedule', $('#plSched'));
}

// ================================================================== LOGINS, HEALTH & LIMITS
function loginLine(a) {
  if (a.expires_at == null) return a.auto_renew ? '<span class="pill g">auto-renews</span>' : '<span class="tiny muted">login doesn\'t expire</span>';
  const d = Math.floor((a.expires_at - now()) / 86400);
  const cls = d < 0 ? 'r' : d <= 7 ? 'a' : 'g';
  return `<span class="pill ${cls}">${d < 0 ? 'login expired' : d === 0 ? 'expires today' : `expires in ${d} day${d > 1 ? 's' : ''}`}</span>${a.auto_renew && d >= 0 ? ' <span class="tiny muted">auto-renews</span>' : ''}`;
}
function usageBars(usage) {
  return (usage || []).map(u => { const p = Math.min(100, u.used / u.limit * 100); return `<div class="tiny muted" title="${esc(u.label)}" style="min-width:120px">${u.used}/${u.limit} per ${u.window_label}
    <div style="height:5px;background:var(--surface-3);border-radius:9px;overflow:hidden;margin-top:2px"><div style="height:100%;width:${p}%;background:${p >= 100 ? 'var(--red)' : p > 75 ? 'var(--amber)' : 'var(--green)'}"></div></div></div>`; }).join('');
}
async function checkAllAccounts(btn) {
  await run(btn, async () => {
    const r = await post('/api/accounts/check');
    toast(`Checked: ${r.healthy} OK${r.warn ? `, ${r.warn} warning(s)` : ''}${r.dead ? `, ${r.dead} need reconnecting` : ''}`, r.dead ? 'err' : 'ok');
    await loadAccounts(); route(); pollStatus();
  });
}
async function dashLoginsCard() {
  const d = await api('/api/logins');
  const rows = d.logins.filter(a => a.status !== 'ok' || a.check_error || (a.expires_at != null && a.expires_at - now() < 21 * 86400));
  const box = $('#dashLogins');
  if (!box) return;
  box.innerHTML = `<div class="card"><div class="card-h"><h2>${ic('key')}Logins & health</h2><div class="act"><button class="btn sm" onclick="checkAllAccounts(this)">${ic('refresh', 14)}Check now</button></div></div>
    <div class="card-b" style="padding-top:4px;padding-bottom:4px">${rows.length ? rows.map(a => `<div class="act-row">${nb(a.network)}<div style="flex:1;min-width:0"><b class="small">${esc(a.name)}</b>
      <div class="tiny" style="color:${a.status === 'expired' ? 'var(--red-d)' : 'var(--muted)'}">${esc(a.status === 'expired' ? (a.error || 'Login expired') : (a.check_error || ''))}</div></div>${loginLine(a)}
      ${a.status === 'expired' || (a.days_left != null && a.days_left <= 7) ? `<button class="btn sm" onclick="connectDialog('${a.network}')">Reconnect</button>` : ''}</div>`).join('')
      : `<div class="small muted" style="padding:12px 0">✅ All ${d.logins.length} account login(s) are healthy. Checked daily at ${String(S.healthHour ?? 8).padStart(2, '0')}:00.</div>`}</div></div>`;
}
async function limitsCard() {
  const d = await api('/api/limits');
  const nets = S.netList.filter(n => d.networks[n.key] && d.networks[n.key].length);
  const winOpts = [[60, 'minute'], [3600, 'hour'], [10800, '3 hours'], [86400, '24 hours'], [604800, 'week'], [2592000, 'month']];
  return `<div class="card"><div class="card-h"><h2>${ic('shield')}Posting limits (rate-limit guard)</h2></div><div class="card-b">
    <p class="small muted" style="margin-top:0">PostPilot never sends more than each network allows. Extra posts wait automatically and go out as soon as the window frees up — no failed posts, no blocked accounts.</p>
    <div class="field"><label class="f">Your X (Twitter) API plan</label><select class="in sm" id="limX" style="width:auto">${[['free', 'Free — 17 posts / 24h, 500 / month'], ['basic', 'Basic — 100 / 24h, 3,000 / month'], ['pro', 'Pro — 10,000 / 24h']].map(([k, l]) => `<option value="${k}" ${d.x_plan === k ? 'selected' : ''}>${l}</option>`).join('')}</select></div>
    <table class="tbl"><thead><tr><th>Network</th><th>Limit</th><th></th></tr></thead><tbody>${nets.filter(n => n.key !== 'x').map(n => {
      const r = (d.overrides[n.key] || d.defaults[n.key] || [])[0] || {limit: d.networks[n.key][0].limit, window: d.networks[n.key][0].window};
      const lim = Array.isArray(r) ? r[0] : r.limit, win = Array.isArray(r) ? r[1] : r.window;
      return `<tr data-net="${n.key}"><td><div class="row nw">${nb(n.key)}<span class="small">${esc(n.name)}</span></div></td>
        <td><div class="row nw"><input type="number" class="in sm limN" min="1" value="${lim}" style="width:90px"><span class="small muted">per</span><select class="in sm limW" style="width:auto">${winOpts.map(([s, l]) => `<option value="${s}" ${s === win ? 'selected' : ''}>${l}</option>`).join('')}</select></div></td>
        <td class="tiny muted">${esc(d.networks[n.key][0].label)}</td></tr>`; }).join('')}</tbody></table>
    <div class="row mt"><button class="btn pri" onclick="saveLimits(this)">Save limits</button><button class="btn ghost" onclick="post('/api/limits',{overrides:{}}).then(()=>{toast('Back to safe defaults','ok');route()})">Reset to defaults</button></div></div></div>`;
}
async function saveLimits(btn) {
  await run(btn, async () => {
    const d = await api('/api/limits');
    const overrides = {};
    $$('tr[data-net]').forEach(tr => {
      const k = tr.dataset.net, n = +$('.limN', tr).value, w = +$('.limW', tr).value;
      const def = (d.defaults[k] || [])[0];
      if (!def || def.limit !== n || def.window !== w) overrides[k] = [[n, w]];
    });
    await post('/api/limits', {x_plan: $('#limX').value, overrides});
    toast('Limits saved', 'ok');
  });
}
