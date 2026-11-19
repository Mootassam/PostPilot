/* PostPilot — Accounts, Automation and Settings pages. */
'use strict';

// ================================================================== ACCOUNTS
PAGES.accounts = {title: 'Accounts', sub: 'Connect your social profiles and set your posting schedule', render: async el => {
  await loadAccounts();
  const accs = S.accounts;
  el.innerHTML = `<div class="grid g-side"><div>
    <div class="card"><div class="card-h"><h2>${ic('users')}Connected accounts</h2><div class="sub">${accs.length} account(s) in this brand</div>
      <div class="act">${accs.length ? `<button class="btn sm" onclick="checkAllAccounts(this)">${ic('refresh', 14)}Check all logins</button>` : ''}</div></div>
      ${accs.length ? accs.map(a => `<div class="accrow">${avatar(a)}<div style="flex:1;min-width:0"><div class="row nw" style="gap:8px"><b class="ell">${esc(a.name)}</b>${statusPill(a.status)}</div>
        <div class="small muted ell">${esc(a.network_name)}${a.handle ? ' · ' + esc(a.handle) : ''} · ${a.posts} post(s)</div>
        ${a.error ? `<div class="tiny" style="color:${a.status === 'ok' ? 'var(--amber-d)' : 'var(--red-d)'}">${esc(a.error)}</div>` : ''}
        ${a.check_error ? `<div class="tiny" style="color:var(--amber-d)">⚠ ${esc(a.check_error)}</div>` : ''}
        <div class="row" style="gap:8px;margin-top:4px">${loginLine(a)}<span class="tiny muted">${a.last_check_at ? 'checked ' + rel(a.last_check_at) : 'not checked yet'}</span></div>
        <div class="row" style="gap:12px;margin-top:6px">${usageBars(a.usage)}</div>
        <div class="tiny muted">${a.slots ? `Own schedule: ${a.slots.map(s => s.time).join(', ')}` : 'Uses the default schedule'}</div></div>
        <div class="row" style="gap:4px">${a.network === 'pinterest' && a.boards ? `<select class="in sm" style="width:150px" title="Default board" onchange="post('/api/accounts/${a.id}',{board_id:this.value}).then(()=>toast('Default board saved','ok'))">${a.boards.map(b => `<option value="${b.id}" ${a.board_id === b.id ? 'selected' : ''}>${esc(b.name)}</option>`).join('')}</select>` : ''}
          <button class="btn sm" onclick="editSlots(${a.id})">${ic('clock', 14)}Schedule</button>
          <button class="btn sm ghost" title="More" onclick="accMenu(this,${a.id})">⋯</button></div></div>`).join('')
        : emptyState('users', 'No accounts yet', 'Pick a network below to connect your first account. Each one takes 1–3 minutes and the steps are shown.')}</div>
    <div class="card"><div class="card-h"><h2>${ic('plus')}Add an account</h2></div><div class="card-b"><div class="netgrid">${S.netList.map(n => `<div class="netcard" onclick="connectDialog('${n.key}')">
      <span class="nb" style="background:${n.color}">${esc(n.abbr)}</span><div style="min-width:0"><div class="b ell">${esc(n.name)}</div><div class="tiny muted">${n.connect === 'oauth' ? 'Log in' : n.connect === 'both' ? 'Log in or token' : n.key === 'reminder' ? 'Notify me' : 'Token / key'}</div></div></div>`).join('')}</div></div></div>
  </div><div>
    <div class="card"><div class="card-h"><h2>${ic('clock')}Default posting schedule</h2></div><div class="card-b">
      <p class="small muted" style="margin-top:0">“Add to queue” puts posts in the next free time slot. Accounts can have their own slots (click Schedule).</p>
      <div id="defSlots">${slotChips(S.defaultSlots, 'def')}</div><button class="btn sm mt-s" onclick="editSlots(null)">${ic('edit', 14)}Edit default schedule</button></div></div>
    <div class="card"><div class="card-b small" style="color:var(--ink-2)"><b>🔒 Safe & official.</b> PostPilot only uses each network's <b>official API</b> with keys that you create — your passwords are never asked for (except Bluesky app passwords). All logins are encrypted on this PC.</div></div>
  </div></div>`;
}};
function slotChips(slots, key) {
  if (!slots || !slots.length) return '<span class="small muted">No time slots.</span>';
  return slots.map(s => `<span class="slot">${s.time}<span class="tiny" style="opacity:.7">${s.days === '0123456' ? 'every day' : s.days.split('').map(d => DAYS[+d].slice(0, 2)).join(' ')}</span></span>`).join('');
}
function accMenu(btn, id) {
  const a = S.accounts.find(x => x.id === id);
  popMenu(btn, [{label: 'Reconnect / refresh login', icon: 'refresh', run: () => connectDialog(a.network)},
    {label: a.status === 'paused' ? 'Resume posting' : 'Pause posting', icon: 'pause', run: async () => { await post(`/api/accounts/${id}`, {paused: a.status !== 'paused'}); route(); }},
    {label: 'Rename', icon: 'edit', run: () => renameAcc(a)},
    {label: 'View posts', icon: 'list', run: () => { CAL.account = String(id); go('calendar'); }}, '-',
    {label: 'Remove account', icon: 'trash', run: async () => {
      if (!await confirmBox(`Remove ${esc(a.name)}?`, 'Its scheduled posts will skip this account. You can connect it again anytime.', 'Remove', true)) return;
      await del('/api/accounts/' + id); toast('Account removed', 'ok'); route(); pollStatus(); }}]);
}
function renameAcc(a) {
  const m = modal({title: 'Rename account', body: `<input class="in" id="rnN" value="${esc(a.name)}">`, foot: `<button class="btn" data-c>Cancel</button><button class="btn pri" id="rnS">Save</button>`});
  m.el.querySelector('[data-c]').onclick = m.close;
  $('#rnS').onclick = async () => { await post(`/api/accounts/${a.id}`, {name: $('#rnN').value}); m.close(); route(); };
}
function editSlots(accId) {
  const a = accId ? S.accounts.find(x => x.id === accId) : null;
  let slots = JSON.parse(JSON.stringify(a ? (a.slots || S.defaultSlots) : S.defaultSlots));
  const m = modal({title: a ? `Posting schedule — ${esc(a.name)}` : 'Default posting schedule', body: `<div id="slList"></div><hr class="sep">
    <div class="small b mb-s">Add a time slot</div><div class="row"><input type="time" class="in sm" id="slT" value="12:00" style="width:auto">
    <div class="days" style="flex:1">${DAYS.map((d, i) => `<label><input type="checkbox" value="${i}" checked><span>${d.slice(0, 2)}</span></label>`).join('')}</div><button class="btn sm" id="slAdd">${ic('plus', 14)}Add</button></div>
    ${a ? `<label class="switch small mt"><input type="checkbox" id="slDef" ${a.slots ? '' : 'checked'}><span class="tr"></span>Use the default schedule instead</label>` : ''}`,
    foot: `<button class="btn" data-c>Cancel</button><button class="btn pri" id="slSave">Save schedule</button>`});
  m.el.querySelector('[data-c]').onclick = m.close;
  const draw = () => { $('#slList').innerHTML = slots.length ? slots.sort((x, y) => x.time.localeCompare(y.time)).map((s, i) => `<span class="slot">${s.time} <span class="tiny" style="opacity:.7">${s.days === '0123456' ? 'every day' : s.days.split('').map(d => DAYS[+d].slice(0, 2)).join(' ')}</span><button data-i="${i}">✕</button></span>`).join('') : '<span class="small muted">No slots — add at least one.</span>';
    $$('#slList button').forEach(b => b.onclick = () => { slots.splice(+b.dataset.i, 1); draw(); }); };
  draw();
  $('#slAdd').onclick = () => { const days = $$('.days input', m.el).filter(x => x.checked).map(x => x.value).join(''); if (!days) return toast('Pick at least one day', 'warn'); slots.push({time: $('#slT').value, days}); draw(); };
  $('#slSave').onclick = () => run($('#slSave'), async () => {
    if (a) await post(`/api/accounts/${a.id}`, {slots: $('#slDef').checked ? null : slots});
    else { if (!slots.length) throw new Error('Add at least one time slot.'); await post('/api/settings/slots', {slots}); }
    m.close(); toast('Schedule saved', 'ok'); route();
  });
}

// ---- connect wizard
function connectDialog(key) {
  const n = S.nets[key];
  const appSection = n.app_fields.length ? `<div class="small b mb-s">${n.connect === 'both' ? 'Option 1 — ' : ''}Log in (recommended)</div>
    ${n.app_fields.map(f => `<div class="field"><label class="f">${esc(f.label)}</label><input class="in" data-app="${f.key}" type="${f.secret ? 'password' : 'text'}" placeholder="${f.secret && n.app_saved[f.key] ? '•••••• saved — leave empty to keep' : ''}" value="${!f.secret ? esc(n.app_saved[f.key] || '') : ''}">${f.help ? `<div class="hint">${esc(f.help)}</div>` : ''}</div>`).join('')}
    <button class="btn pri" id="cnOauth">${ic('key', 15)}Log in with ${esc(n.name.split(' ')[0])}</button><div id="cnWait" class="small muted mt-s"></div>` : '';
  const formSection = n.form_fields.length ? `${n.app_fields.length ? `<hr class="sep"><div class="small b mb-s">Option 2 — paste a token</div>` : ''}
    ${n.form_fields.map(f => `<div class="field"><label class="f">${esc(f.label)}</label>${f.type === 'select' ? `<select class="in" data-form="${f.key}">${f.options.map(o => `<option>${esc(o)}</option>`).join('')}</select>` : `<input class="in" data-form="${f.key}" type="${f.secret ? 'password' : 'text'}" autocomplete="off">`}${f.help ? `<div class="hint">${esc(f.help)}</div>` : ''}</div>`).join('')}
    <button class="btn ${n.app_fields.length ? '' : 'pri'}" id="cnForm">${ic('check', 15)}Connect</button>` : '';
  const m = modal({title: `<span class="row nw" style="gap:10px">${nb(key)} Connect ${esc(n.name)}</span>`, size: 'mid', body: `<div class="grid g2" style="gap:22px">
    <div><div class="small b mb-s">How to</div><ol class="guide">${n.guide.map(g => `<li>${g}</li>`).join('')}</ol>${n.note ? `<div class="alert b small">${ic('alert', 16)}<div>${esc(n.note)}</div></div>` : ''}</div>
    <div>${appSection}${formSection}</div></div>`, foot: '<button class="btn" data-c>Close</button>'});
  m.el.querySelector('[data-c]').onclick = m.close;
  if ($('#cnOauth')) $('#cnOauth').onclick = () => run($('#cnOauth'), async () => {
    const appVals = {};
    $$('[data-app]', m.el).forEach(i => { if (i.value.trim()) appVals[i.dataset.app] = i.value.trim(); });
    await post('/api/apps/' + key, appVals);
    const r = await post('/api/oauth/start', {network: key});
    const w = window.open(r.url, 'pp_oauth', 'width=620,height=760');
    if (!w) toast('Allow pop-ups for PostPilot, then try again.', 'warn');
    $('#cnWait').innerHTML = '<span class="spin"></span> Waiting for you to finish logging in the other window…';
    const res = await pollOauth(r.state);
    $('#cnWait').innerHTML = '';
    if (res.status === 'error') throw new Error(res.error);
    if (res.status === 'expired') throw new Error('The login window was closed or timed out — try again.');
    m.close();
    connected(res);
  });
  if ($('#cnForm')) $('#cnForm').onclick = () => run($('#cnForm'), async () => {
    const data = {};
    $$('[data-form]', m.el).forEach(i => { data[i.dataset.form] = i.value; });
    const r = await post('/api/accounts/connect', {network: key, data});
    m.close();
    connected(r);
  });
}
async function pollOauth(state) {
  const end = Date.now() + 10 * 60 * 1000;
  while (Date.now() < end) {
    await new Promise(r => setTimeout(r, 1500));
    const r = await api('/api/oauth/result?state=' + state);
    if (r.status !== 'waiting') return r;
  }
  return {status: 'expired'};
}
function connected(r) {
  if (r.added) { toast(`Connected ${r.added.length} account(s) ✅`, 'ok'); loadAccounts().then(() => { route(); pollStatus(); }); return; }
  const m = modal({title: 'Choose the accounts to add', body: `<p class="small muted" style="margin-top:0">We found these profiles. Pick the ones PostPilot should post to:</p>
    ${r.choices.map(c => `<label class="accpick on" style="cursor:pointer"><input type="checkbox" value="${c.i}" checked style="accent-color:var(--brand)">${avatar(c, true)}<div style="min-width:0"><div class="nm ell">${esc(c.name)}</div><div class="hd">${esc(netMeta(c.network).name)} ${esc(c.handle || '')}${c.exists ? ' · already connected (will refresh)' : ''}</div></div></label>`).join('')}`,
    foot: `<button class="btn" data-c>Cancel</button><button class="btn pri" id="pkGo">Add selected</button>`});
  m.el.querySelector('[data-c]').onclick = m.close;
  $('#pkGo').onclick = () => run($('#pkGo'), async () => {
    const picks = $$('input[type=checkbox]', m.el).filter(x => x.checked).map(x => +x.value);
    const res = await post('/api/accounts/pick', {pending: r.pending, picks});
    m.close(); toast(`Connected ${res.added.length} account(s) ✅`, 'ok'); await loadAccounts(); route(); pollStatus();
  });
}

// ================================================================== AUTOMATION
PAGES.automation = {title: 'Automation', sub: 'RSS auto-posting, bulk CSV scheduling and evergreen posts', render: async el => {
  const [f, sch] = await Promise.all([api('/api/feeds'), api('/api/posts?status=scheduled')]);
  const rec = sch.posts.filter(p => p.recurring && p.recurring.every_days);
  const accName = id => { const a = S.accounts.find(x => x.id === id); return a ? `${nb(a.network)} ${esc(a.name)}` : '(removed)'; };
  el.innerHTML = `<div class="grid g2">
    <div class="card"><div class="card-h"><h2>${ic('rss')}RSS / blog auto-post</h2><div class="act"><button class="btn sm pri" onclick="feedDialog()">${ic('plus', 14)}Add feed</button></div></div>
      ${f.feeds.length ? f.feeds.map(x => `<div class="accrow"><div class="ic b" style="width:38px;height:38px;border-radius:11px;display:grid;place-items:center">${ic('rss', 18)}</div><div style="flex:1;min-width:0">
        <div class="b ell">${esc(x.name)}</div><div class="tiny muted ell">${esc(x.url)}</div><div class="tiny">${x.accounts.map(accName).join(' · ')}</div>
        <div class="tiny muted">${x.mode === 'queue' ? 'Adds new items to the queue' : x.mode === 'now' ? 'Posts new items immediately' : 'Creates drafts'} · checks every ${x.every_min} min${x.last_checked ? ' · checked ' + rel(x.last_checked) : ''}</div>
        ${x.last_error ? `<div class="tiny" style="color:var(--red-d)">${esc(x.last_error)}</div>` : ''}</div>
        <label class="switch" title="On/off"><input type="checkbox" ${x.enabled ? 'checked' : ''} onchange="post('/api/feeds',{...${esc(JSON.stringify(x))},enabled:this.checked}).then(()=>toast('Saved','ok')).catch(e=>toast(e.message,'err'))"><span class="tr"></span></label>
        <button class="btn sm ghost" title="Check now" onclick="run(this,()=>post('/api/feeds/${x.id}/check').then(()=>{toast('Checked','ok');route()}))">${ic('refresh', 14)}</button>
        <button class="btn sm ghost" onclick="del('/api/feeds/${x.id}').then(route)">${ic('trash', 14)}</button></div>`).join('')
        : `<div class="card-b small muted">Paste your blog, YouTube or news RSS feed — every new article becomes a post automatically. Existing items are never flooded out.</div>`}</div>
    <div class="card"><div class="card-h"><h2>${ic('upload')}Bulk schedule from CSV / Excel</h2></div><div class="card-b">
      <p class="small muted" style="margin-top:0">Plan a whole month in a spreadsheet: one row per post with <b>text, date, accounts, media, link</b>. Save as CSV and upload.</p>
      <div class="row"><a class="btn" href="/api/import/template">${ic('download', 15)}Download template</a><label class="btn pri">${ic('upload', 15)}Upload CSV<input type="file" accept=".csv" hidden onchange="importCsv(this)"></label></div>
      <div id="csvOut" class="mt"></div>
      <div class="tiny muted mt">accounts = network names (facebook, x…), account names, or <b>all</b> · media = image URLs or file paths separated by | · empty date = next free queue slot.</div></div></div>
    <div class="card"><div class="card-h"><h2>${ic('repeat')}Evergreen / recurring posts</h2><div class="sub">re-post your best content automatically</div></div>
      ${rec.length ? rec.map(postRow).join('') : `<div class="card-b small muted">Turn on <b>Repeat</b> in the composer to republish a post every X days (e.g. your best offer every 2 weeks).</div>`}</div>
  </div>`;
}};
function feedDialog() {
  const m = modal({title: 'Add an RSS feed', size: 'mid', body: `<div class="field"><label class="f">Feed address</label><input class="in" id="fdU" placeholder="https://yourblog.com/feed"><div class="hint">WordPress: /feed · YouTube: https://www.youtube.com/feeds/videos.xml?channel_id=… · most news sites have one.</div></div>
    <div class="field"><label class="f">Name</label><input class="in" id="fdN" placeholder="My blog"></div>
    <div class="field"><label class="f">Post to</label><div>${S.accounts.map(a => `<label class="accpick" style="display:inline-flex;margin:2px"><input type="checkbox" value="${a.id}" style="accent-color:var(--brand)">${nb(a.network)} ${esc(a.name)}</label>`).join('') || '<span class="small muted">Connect accounts first.</span>'}</div></div>
    <div class="grid g2" style="gap:12px"><div class="field"><label class="f">Post text template</label><textarea class="in" id="fdT" rows="3">{title}\n\n{link}</textarea><div class="hint">{title} {link} {summary}</div></div>
    <div><div class="field"><label class="f">When a new item appears</label><select class="in" id="fdM"><option value="queue">Add to my queue</option><option value="now">Post immediately</option><option value="draft">Create a draft (I review first)</option></select></div>
    <div class="field"><label class="f">Check every</label><select class="in" id="fdE"><option value="30">30 minutes</option><option value="60" selected>hour</option><option value="360">6 hours</option><option value="1440">day</option></select></div></div></div>`,
    foot: `<button class="btn" data-c>Cancel</button><button class="btn pri" id="fdS">Add feed</button>`});
  m.el.querySelector('[data-c]').onclick = m.close;
  $('#fdS').onclick = () => run($('#fdS'), async () => {
    const r = await post('/api/feeds', {url: $('#fdU').value, name: $('#fdN').value, template: $('#fdT').value, mode: $('#fdM').value, every_min: +$('#fdE').value,
      accounts: $$('.accpick input', m.el).filter(x => x.checked).map(x => +x.value)});
    m.close(); toast(`Feed added — found ${r.items} existing item(s). New ones will be posted automatically.`, 'ok'); route();
  });
}
async function importCsv(inp) {
  const f = inp.files[0]; inp.value = '';
  if (!f) return;
  const fd = new FormData(); fd.append('file', f);
  await run(null, async () => {
    const r = await api('/api/import/csv', {form: fd});
    $('#csvOut').innerHTML = `<div class="alert ${r.created ? 'g' : 'a'}">${ic(r.created ? 'check' : 'alert')}<div><b>${r.created} post(s) scheduled.</b>${r.errors.length ? `<div class="tiny">${r.errors.map(esc).join('<br>')}</div>` : ''}</div></div>`;
    pollStatus();
  });
}

// ================================================================== SETTINGS
PAGES.settings = {title: 'Settings', sub: 'Brands, AI, limits, inbox, media hosting, hashtags, templates and backup', render: async el => {
  const [s, b, hg, tp] = await Promise.all([api('/api/settings'), api('/api/brands'), api('/api/hashtag_groups'), api('/api/templates')]);
  const limitsHtml = await limitsCard();
  S.healthHour = s.health_hour;
  const tun = s.tunnel;
  el.innerHTML = `<div class="grid g2">
    <div class="card"><div class="card-h"><h2>${ic('spark')}AI assistant</h2><div class="act"><span class="pill ${s.ai_active === 'off' || s.ai_active === 'basic' ? 'a' : 'g'}">${{anthropic: 'Claude active', ollama: 'Ollama active', basic: 'Basic helpers', off: 'Off'}[s.ai_active]}</span></div></div><div class="card-b">
      <div class="field"><label class="f">Use</label><select class="in" id="aiP"><option value="auto">Automatic (Claude if a key is set, else local Ollama)</option><option value="anthropic">Claude (Anthropic API key)</option><option value="ollama">Local AI — Ollama (free, offline)</option><option value="off">Off</option></select></div>
      <div class="field"><label class="f">Claude API key</label><input class="in" id="aiK" type="password" placeholder="${s.anthropic_key_set ? '•••••••• saved — paste a new key to replace' : 'sk-ant-…'}" autocomplete="off"><div class="hint">Get one at <a href="https://console.anthropic.com/settings/keys" target="_blank">console.anthropic.com</a> — pay-as-you-go, a few cents per hundred posts. Uses Claude Opus 5.5 with automatic fallback.</div></div>
      <div class="field"><label class="f">Ollama model</label><input class="in" id="aiO" value="${esc(s.ollama_model)}"><div class="hint">${s.ollama ? '✅ Ollama is running on this PC.' : 'Install free from <a href="https://ollama.com" target="_blank">ollama.com</a>, then run <code>ollama pull llama3.1</code>.'}</div></div>
      <div class="row"><button class="btn pri" onclick="saveAi(this)">Save</button><button class="btn" onclick="testAi(this)">${ic('spark', 15)}Test</button>${s.anthropic_key_set ? `<button class="btn ghost" onclick="post('/api/settings',{anthropic_key:''}).then(route)">Remove key</button>` : ''}</div></div></div>
    <div class="card"><div class="card-h"><h2>${ic('globe')}Media hosting for Instagram & Threads</h2><div class="act"><span class="pill ${tun.status === 'on' ? 'g' : ''}">${tun.status === 'on' ? 'Link open' : 'Idle'}</span></div></div><div class="card-b">
      <p class="small muted" style="margin-top:0">Instagram and Threads download your photos/videos from a public link instead of accepting uploads. PostPilot opens a <b>temporary secure Cloudflare link</b> only while publishing, then closes it.</p>
      <div class="field"><select class="in" id="mhM"><option value="auto">Automatic temporary link (recommended)</option><option value="custom">My own public address (advanced)</option><option value="off">Off (Instagram/Threads won't work)</option></select></div>
      <div class="field" id="mhCustom"><label class="f">Public https address that forwards to this PC</label><input class="in" id="mhU" value="${esc(s.public_base)}" placeholder="https://media.mydomain.com"></div>
      <button class="btn pri" onclick="saveMh(this)">Save</button>${tun.error ? `<div class="tiny" style="color:var(--red-d);margin-top:8px">${esc(tun.error)}</div>` : ''}</div></div>
    ${limitsHtml}
    <div class="card"><div class="card-h"><h2>${ic('chat')}Inbox & account checks</h2></div><div class="card-b">
      <div class="field"><label class="f">Check for new comments & messages every</label><select class="in sm" id="ibEvery" style="width:auto">${[5, 10, 15, 30, 60].map(n => `<option value="${n}" ${s.inbox_every_min === n ? 'selected' : ''}>${n} minutes</option>`).join('')}</select>
        <div class="hint">Each account is checked in turn, gently, so no network gets too many requests.</div></div>
      <div class="field"><label class="f">Daily login health check at</label><select class="in sm" id="hHour" style="width:auto">${Array.from({length: 24}, (_, h) => `<option value="${h}" ${s.health_hour === h ? 'selected' : ''}>${String(h).padStart(2, '0')}:00</option>`).join('')}</select>
        <div class="hint">Tests every login quietly once a day and alerts you if one died, lost a permission, or expires soon.</div></div>
      <button class="btn pri" onclick="run(this,async()=>{await post('/api/settings',{inbox_every_min:+$('#ibEvery').value,health_hour:+$('#hHour').value});toast('Saved','ok')})">Save</button></div></div>
    <div class="card"><div class="card-h"><h2>${ic('hash')}Hashtag groups</h2><div class="act"><button class="btn sm" onclick="crudDialog('hashtag_groups')">${ic('plus', 14)}Add</button></div></div><div class="card-b" style="padding-top:4px;padding-bottom:4px">
      ${hg.items.length ? hg.items.map(x => `<div class="act-row"><div style="flex:1;min-width:0"><div class="b">${esc(x.name)}</div><div class="small muted">${esc(x.tags)}</div></div><button class="btn sm ghost" onclick='crudDialog("hashtag_groups",${JSON.stringify(x).replace(/'/g, "&#39;")})'>${ic('edit', 14)}</button><button class="btn sm ghost" onclick="del('/api/hashtag_groups/${x.id}').then(route)">${ic('trash', 14)}</button></div>`).join('') : '<div class="small muted" style="padding:12px 0">No groups.</div>'}</div></div>
    <div class="card"><div class="card-h"><h2>${ic('file')}Templates</h2><div class="act"><button class="btn sm" onclick="crudDialog('templates')">${ic('plus', 14)}Add</button></div></div><div class="card-b" style="padding-top:4px;padding-bottom:4px">
      ${tp.items.length ? tp.items.map(x => `<div class="act-row"><div style="flex:1;min-width:0"><div class="b">${esc(x.name)}</div><div class="small muted ell">${esc(x.text)}</div></div><button class="btn sm ghost" onclick='crudDialog("templates",${JSON.stringify(x).replace(/'/g, "&#39;")})'>${ic('edit', 14)}</button><button class="btn sm ghost" onclick="del('/api/templates/${x.id}').then(route)">${ic('trash', 14)}</button></div>`).join('') : '<div class="small muted" style="padding:12px 0">No templates.</div>'}</div></div>
    <div class="card"><div class="card-h"><h2>${ic('users')}Brands / workspaces</h2><div class="act"><button class="btn sm" onclick="switchBrand('new')">${ic('plus', 14)}New brand</button></div></div><div class="card-b" style="padding-top:4px;padding-bottom:4px">
      ${b.brands.map(x => `<div class="act-row"><span style="width:14px;height:14px;border-radius:50%;background:${esc(x.color)};margin-top:4px"></span><div style="flex:1"><div class="b">${esc(x.name)} ${x.id === b.current ? '<span class="pill b">current</span>' : ''}</div><div class="tiny muted">${x.accounts} account(s)</div></div>
        <button class="btn sm ghost" onclick="brandEdit(${x.id},'${esc(x.name).replace(/'/g, '&#39;')}','${esc(x.color)}')">${ic('edit', 14)}</button>${b.brands.length > 1 ? `<button class="btn sm ghost" onclick="brandDel(${x.id},'${esc(x.name).replace(/'/g, '&#39;')}')">${ic('trash', 14)}</button>` : ''}</div>`).join('')}</div></div>
    <div class="card"><div class="card-h"><h2>${ic('link')}Link tracking (UTM) & publishing</h2></div><div class="card-b">
      <div class="grid g3" style="gap:10px"><div class="field"><label class="f">utm_source</label><input class="in sm" id="u1" value="${esc(s.utm_source)}"></div><div class="field"><label class="f">utm_medium</label><input class="in sm" id="u2" value="${esc(s.utm_medium)}"></div><div class="field"><label class="f">utm_campaign</label><input class="in sm" id="u3" value="${esc(s.utm_campaign)}"></div></div>
      <div class="hint" style="margin-top:-6px">{network} becomes facebook, x, linkedin…</div>
      <div class="field mt"><label class="f">Retry a failed post up to</label><select class="in sm" id="rMax" style="width:auto">${[1, 2, 3, 4, 5, 6].map(n => `<option ${s.retry_max === n ? 'selected' : ''}>${n}</option>`).join('')}</select> <span class="small muted">times (network errors only — with growing waits)</span></div>
      <label class="row mt" style="gap:8px;align-items:flex-start;cursor:pointer"><input type="checkbox" id="nSch" ${s.native_schedule ? 'checked' : ''} style="margin-top:3px">
        <span><b>Let YouTube & Facebook publish by themselves</b><br><span class="small muted">Scheduled posts are uploaded early and the network makes them public at the exact time — even if your PC is off. Other networks don't allow this, so for them PostPilot must be running at the time.</span></span></label>
      <button class="btn pri mt" onclick="saveUtm(this)">Save</button></div></div>
    <div class="card"><div class="card-h"><h2>${ic('db')}Backup & app</h2></div><div class="card-b">
      <div class="row mb"><a class="btn pri" href="/api/backup">${ic('download', 15)}Download backup</a><label class="btn">${ic('upload', 15)}Restore<input type="file" accept=".db" hidden onchange="restoreDb(this)"></label></div>
      <div class="row mb"><button class="btn" onclick="toggleTheme()">${ic('moon', 15)}Light / dark</button><button class="btn danger" onclick="quitApp()">${ic('power', 15)}Quit PostPilot</button></div>
      <div class="small muted">Version ${esc(s.version)} · data: <span style="word-break:break-all">${esc(s.data_dir)}</span><br>OAuth redirect address: <code>${esc(s.redirect)}</code></div></div></div>
  </div>`;
  $('#aiP').value = s.ai_provider; $('#mhM').value = s.media_host;
  const mh = () => { $('#mhCustom').style.display = $('#mhM').value === 'custom' ? '' : 'none'; };
  $('#mhM').onchange = mh; mh();
}};
async function saveAi(btn) { await run(btn, async () => { const body = {ai_provider: $('#aiP').value, ollama_model: $('#aiO').value}; if ($('#aiK').value.trim()) body.anthropic_key = $('#aiK').value.trim(); await post('/api/settings', body); toast('AI settings saved', 'ok'); await pollStatus(); route(); }); }
async function testAi(btn) { await run(btn, async () => { const r = await post('/api/ai', {task: 'hashtags', text: 'Fresh croissants every morning at our little bakery in Rabat'}); toast(`AI works (${r.provider}): ${r.text.slice(0, 80)}`, 'ok'); }); }
async function saveMh(btn) { await run(btn, async () => { await post('/api/settings', {media_host: $('#mhM').value, public_base: $('#mhU').value}); toast('Saved', 'ok'); }); }
async function saveUtm(btn) { await run(btn, async () => { await post('/api/settings', {utm_source: $('#u1').value, utm_medium: $('#u2').value, utm_campaign: $('#u3').value, retry_max: +$('#rMax').value, native_schedule: $('#nSch').checked ? 1 : 0}); toast('Saved', 'ok'); }); }
function crudDialog(kind, item) {
  const isTags = kind === 'hashtag_groups', f2 = isTags ? 'tags' : 'text';
  const m = modal({title: `${item ? 'Edit' : 'New'} ${isTags ? 'hashtag group' : 'template'}`, body: `<div class="field"><label class="f">Name</label><input class="in" id="cdN" value="${esc(item ? item.name : '')}"></div>
    <div class="field"><label class="f">${isTags ? 'Hashtags' : 'Text'}</label><textarea class="in" id="cdT" rows="${isTags ? 3 : 6}" placeholder="${isTags ? '#coffee #morning #rabat' : 'Use {placeholders} you fill in when posting'}">${esc(item ? item[f2] : '')}</textarea></div>`,
    foot: `<button class="btn" data-c>Cancel</button><button class="btn pri" id="cdS">Save</button>`});
  m.el.querySelector('[data-c]').onclick = m.close;
  $('#cdS').onclick = () => run($('#cdS'), async () => { await post('/api/' + kind, {id: item && item.id, name: $('#cdN').value, [f2]: $('#cdT').value}); m.close(); route(); });
}
function brandEdit(id, name, color) {
  const m = modal({title: 'Edit brand', body: `<div class="field"><label class="f">Name</label><input class="in" id="brN" value="${name}"></div><div class="field"><label class="f">Color</label><input type="color" id="brC" value="${color}"></div>`,
    foot: `<button class="btn" data-c>Cancel</button><button class="btn pri" id="brS">Save</button>`});
  m.el.querySelector('[data-c]').onclick = m.close;
  $('#brS').onclick = () => run($('#brS'), async () => { await post('/api/brands', {id, name: $('#brN').value, color: $('#brC').value}); m.close(); await pollStatus(); route(); });
}
async function brandDel(id, name) {
  if (!await confirmBox(`Delete brand “${name}”?`, 'All its accounts, posts, feeds and ideas are deleted from PostPilot. This can\'t be undone.', 'Delete', true)) return;
  await run(null, async () => { await del('/api/brands/' + id); await afterBrandChange(); });
}
async function restoreDb(inp) {
  const f = inp.files[0]; inp.value = '';
  if (!f || !await confirmBox('Restore this backup?', 'Current data is replaced (a safety copy is kept in the data folder).', 'Restore', true)) return;
  const fd = new FormData(); fd.append('file', f);
  await run(null, async () => { await api('/api/restore', {form: fd}); toast('Backup restored', 'ok'); setTimeout(() => location.reload(), 600); });
}
async function quitApp() {
  if (!await confirmBox('Quit PostPilot?', 'Scheduled posts are NOT published while PostPilot is closed — they go out as soon as you open it again.', 'Quit', true)) return;
  await post('/api/quit').catch(() => {});
  document.body.innerHTML = '<div style="display:grid;place-items:center;height:100vh;font-family:Segoe UI,sans-serif;color:#6b6a86;text-align:center"><div><h2 style="color:#14122b">PostPilot is closed</h2><p>Open it again from the Start menu or desktop icon.</p></div></div>';
}
