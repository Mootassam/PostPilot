/* PostPilot 1.3 — bulk scheduling (hundreds of videos at once), brand settings (each brand: its own apps, IDs and settings),
   publishing speed. */
'use strict';

// ================================================================== BULK SCHEDULE
async function bulkDialog(preselected) {
  if (!S.accounts.length) { toast('Connect an account in this brand first.', 'warn'); return go('accounts'); }
  const B = {sel: new Set(preselected || []), lib: [], kind: 'video'};
  const loadLib = async () => {
    const d = await api(`/api/media?limit=1000${B.kind ? '&kind=' + B.kind : ''}`);
    B.lib = d.media;
  };
  await loadLib();
  if (!preselected || !preselected.length) B.lib.filter(m => !m.used).forEach(m => B.sel.add(m.id));   // default: the videos no post uses yet
  const yt = () => S.accounts.some(a => B.acc.has(a.id) && a.network === 'youtube');
  B.acc = new Set(S.accounts.filter(a => a.status !== 'expired').map(a => a.id));
  const tomorrow = new Date(Date.now() + 86400000); tomorrow.setHours(9, 0, 0, 0);
  const isoLocal = d => new Date(d - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  const m = modal({title: `${icInline('upload', 17)} Bulk schedule`, size: 'wide', body: `
    <p class="small muted" style="margin-top:0">One post per file — for hundreds of videos or photos at once. Each brand schedules only to its own accounts.</p>
    <div class="grid g2" style="gap:18px">
      <div>
        <div class="row mb-s"><b class="small">1 · Files</b><span class="sp"></span>
          <div class="chips">${[['video', 'Videos'], ['image', 'Photos'], ['', 'All']].map(([k, l]) => `<button class="chip ${B.kind === k ? 'on' : ''}" data-kind="${k}">${l}</button>`).join('')}</div></div>
        <div class="row mb-s"><button class="btn sm" id="bkAll">Select all</button><button class="btn sm" id="bkNew">Only not used yet</button><button class="btn sm ghost" id="bkNone">None</button><span class="sp"></span><span class="small b" id="bkCount"></span></div>
        <div class="bkgrid" id="bkGrid"></div>
        <label class="dropzone mini mt-s" id="bkDz">${ic('upload', 16)} Drop more files here or click — they are added and selected<input type="file" multiple hidden id="bkFile" accept="image/*,video/*"></label>
      </div>
      <div>
        <div class="small b mb-s">2 · Post to</div>
        <div id="bkAcc">${S.accounts.map(a => `<label class="accpick ${B.acc.has(a.id) ? 'on' : ''}" style="display:inline-flex;margin:2px;padding:5px 9px"><input type="checkbox" value="${a.id}" ${B.acc.has(a.id) ? 'checked' : ''} style="accent-color:var(--brand)">${nb(a.network)} <span class="small">${esc(a.name)}</span>${a.status === 'expired' ? ' <span class="pill r">reconnect</span>' : ''}</label>`).join('')}</div>
        <div class="small b mt mb-s">3 · Text</div>
        <div class="field"><label class="f">Caption / description</label><textarea class="in" id="bkCap" rows="3">{name}</textarea>
          <div class="hint"><code>{name}</code> = file name (“how_to-use.mp4” → “how to use”) · <code>{n}</code> = number · <code>{total}</code></div></div>
        <div class="grid g2" id="bkYt" style="gap:10px">
          <div class="field"><label class="f">YouTube title</label><input class="in" id="bkTitle" value="{name}" maxlength="200"></div>
          <div class="field"><label class="f">YouTube visibility</label><select class="in" id="bkPriv"><option value="public">Public</option><option value="unlisted">Unlisted</option><option value="private">Private</option></select></div>
        </div>
        <div class="small b mt mb-s">4 · When</div>
        <div class="chips mb-s">${[['queue', 'Into my posting times'], ['spread', 'Evenly from a start time'], ['draft', 'Save as drafts']].map(([k, l], i) => `<button class="chip ${i === 0 ? 'on' : ''}" data-mode="${k}">${l}</button>`).join('')}</div>
        <div id="bkWhen"></div>
        <div id="bkPrev" class="mt-s"></div>
      </div>
    </div>`, foot: `<span class="small muted" id="bkSum" style="margin-right:auto"></span><button class="btn" data-c>Cancel</button><button class="btn pri lg" id="bkGo">${ic('cal', 16)}Schedule</button>`});
  m.el.querySelector('[data-c]').onclick = m.close;
  B.mode = 'queue';
  const drawWhen = () => {
    const youtube = yt();
    $('#bkYt').style.display = youtube ? '' : 'none';
    $('#bkWhen').innerHTML = B.mode === 'queue' ? `<div class="grid g2" style="gap:10px"><div class="field"><label class="f">Not before</label><input type="datetime-local" class="in" id="bkFrom" value="${isoLocal(new Date())}"></div>
        <div class="field"><label class="f">Max per day</label><select class="in" id="bkPer">${[0, 1, 2, 3, 4, 5, 6, 8, 10, 12, 20].map(n => `<option value="${n}" ${n === (youtube ? 4 : 0) ? 'selected' : ''}>${n || 'No limit'}</option>`).join('')}</select></div></div>
        <div class="hint">Uses this brand's posting times (Accounts → Schedule) and skips times that are already taken.${youtube ? ' <b>YouTube allows about 6 uploads a day per project</b> — 4 a day is safe.' : ''}</div>`
      : B.mode === 'spread' ? `<div class="grid g3" style="gap:10px"><div class="field"><label class="f">First post</label><input type="datetime-local" class="in" id="bkStart" value="${isoLocal(tomorrow)}"></div>
        <div class="field"><label class="f">Then every</label><select class="in" id="bkEvery">${[[15, '15 min'], [30, '30 min'], [60, 'hour'], [120, '2 hours'], [180, '3 hours'], [240, '4 hours'], [360, '6 hours'], [720, '12 hours'], [1440, 'day']].map(([v, l]) => `<option value="${v}" ${v === (youtube ? 360 : 60) ? 'selected' : ''}>${l}</option>`).join('')}</select></div>
        <div class="field"><label class="f">Max per day</label><select class="in" id="bkPer">${[0, 1, 2, 3, 4, 5, 6, 8, 10, 12, 20].map(n => `<option value="${n}" ${n === (youtube ? 4 : 0) ? 'selected' : ''}>${n || 'No limit'}</option>`).join('')}</select></div></div>
        <div class="hint">When a day is full, the next post starts the next day at the first post's time.</div>`
      : '<div class="hint">Drafts get no time — open them later, or select them in Posts and schedule.</div>';
    drawPrev();
  };
  const fill = (tpl, f, i, n) => tpl.split('{name}').join(f.name.replace(/\.[^.]+$/, '').replace(/[_-]+/g, ' ').replace(/\s+/g, ' ').trim() || 'video').split('{n}').join(i + 1).split('{total}').join(n);
  const chosen = () => B.lib.filter(x => B.sel.has(x.id)).concat([...B.sel].filter(id => !B.lib.some(x => x.id === id)).map(id => ({id, name: '#' + id})));
  const drawPrev = () => {
    const files = chosen();
    $('#bkCount').textContent = `${B.sel.size} selected`;
    $('#bkSum').textContent = `${B.sel.size} file(s) × ${B.acc.size} account(s) = ${B.sel.size * B.acc.size} upload(s)`;
    $('#bkPrev').innerHTML = files.length ? `<div class="tiny muted mb-s">Preview</div>${files.slice(0, 3).map((f, i) => `<div class="bkprev">${yt() ? `<b>${esc(fill($('#bkTitle').value, f, i, files.length).slice(0, 100))}</b><br>` : ''}<span class="small">${esc(fill($('#bkCap').value, f, i, files.length).slice(0, 160))}</span></div>`).join('')}${files.length > 3 ? `<div class="tiny muted">…and ${files.length - 3} more</div>` : ''}` : '';
  };
  const drawGrid = () => {
    $('#bkGrid').innerHTML = B.lib.length ? B.lib.map(x => `<label class="bkitem ${B.sel.has(x.id) ? 'on' : ''}" title="${esc(x.name)}"><input type="checkbox" value="${x.id}" ${B.sel.has(x.id) ? 'checked' : ''}>${mthumb(x)}<span class="ell">${esc(x.name)}</span>${x.used ? `<i class="used">used ${x.used}×</i>` : ''}</label>`).join('')
      : `<div class="empty small">No ${B.kind === 'image' ? 'photos' : B.kind === 'video' ? 'videos' : 'files'} in this brand's library yet — drop some below.</div>`;
    drawPrev();
  };
  drawGrid(); drawWhen();
  $('#bkGrid').onchange = e => { const id = +e.target.value; e.target.checked ? B.sel.add(id) : B.sel.delete(id); e.target.closest('.bkitem').classList.toggle('on', e.target.checked); drawPrev(); };
  $('#bkAcc').onchange = e => { const id = +e.target.value; e.target.checked ? B.acc.add(id) : B.acc.delete(id); e.target.closest('.accpick').classList.toggle('on', e.target.checked); drawWhen(); };
  $('#bkAll').onclick = () => { B.lib.forEach(x => B.sel.add(x.id)); drawGrid(); };
  $('#bkNone').onclick = () => { B.sel.clear(); drawGrid(); };
  $('#bkNew').onclick = () => { B.sel = new Set(B.lib.filter(x => !x.used).map(x => x.id)); drawGrid(); };
  $$('[data-kind]', m.el).forEach(b => b.onclick = async () => { B.kind = b.dataset.kind; $$('[data-kind]', m.el).forEach(x => x.classList.toggle('on', x === b)); await loadLib(); drawGrid(); });
  $$('[data-mode]', m.el).forEach(b => b.onclick = () => { B.mode = b.dataset.mode; $$('[data-mode]', m.el).forEach(x => x.classList.toggle('on', x === b)); drawWhen(); $('#bkGo').innerHTML = ic(B.mode === 'draft' ? 'file' : 'cal', 16) + (B.mode === 'draft' ? 'Save drafts' : 'Schedule'); });
  ['bkCap', 'bkTitle'].forEach(id => $('#' + id).oninput = debounce(drawPrev, 150));
  const addFiles = async files => { const added = await uploadFiles(files); added.forEach(x => B.sel.add(x.id)); await loadLib(); drawGrid(); };
  $('#bkFile').onchange = e => addFiles(e.target.files);
  const dz = $('#bkDz');
  dz.ondragover = e => { e.preventDefault(); dz.classList.add('on'); };
  dz.ondragleave = () => dz.classList.remove('on');
  dz.ondrop = e => { e.preventDefault(); dz.classList.remove('on'); addFiles(e.dataTransfer.files); };
  $('#bkGo').onclick = () => run($('#bkGo'), async () => {
    if (!B.sel.size) throw new Error('Select at least one file.');
    if (!B.acc.size) throw new Error('Choose at least one account.');
    const body = {media: chosen().map(x => x.id), accounts: [...B.acc], mode: B.mode, caption: $('#bkCap').value,
      title: yt() ? $('#bkTitle').value : '', privacy: yt() ? $('#bkPriv').value : '', per_day: $('#bkPer') ? +$('#bkPer').value : 0};
    if (B.mode === 'spread') { body.start_at = Math.floor(new Date($('#bkStart').value) / 1000); body.every_min = +$('#bkEvery').value; }
    if (B.mode === 'queue' && $('#bkFrom').value) body.start_at = Math.floor(new Date($('#bkFrom').value) / 1000);
    const r = await post('/api/posts/bulk', body);
    m.close();
    const res = modal({title: r.created ? `✅ ${r.created} post(s) ${B.mode === 'draft' ? 'saved as drafts' : 'scheduled'}` : 'Nothing scheduled', size: 'mid', body: `
      ${r.first ? `<p style="margin-top:0">From <b>${fdate(r.first, {wd: true})}</b> to <b>${fdate(r.last, {wd: true})}</b>.</p>` : ''}
      ${r.warnings.map(w => `<div class="alert a small">${ic('clock', 16)}<div>${esc(w)}</div></div>`).join('')}
      ${r.problems.length ? `<div class="alert r small">${ic('alert', 16)}<div><b>${r.problems.length} file(s) skipped:</b><div class="tiny">${r.problems.slice(0, 20).map(esc).join('<br>')}</div></div></div>` : ''}
      <p class="small muted">Everything goes out by itself at its time — one upload at a time per account, posting limits respected.</p>`,
      foot: `<button class="btn" data-c>Close</button><button class="btn pri" id="bkCal">${ic('cal', 15)}Open calendar</button>`});
    res.el.querySelector('[data-c]').onclick = res.close;
    $('#bkCal').onclick = () => { res.close(); if (r.first) CAL.anchor = new Date(r.first * 1000); go('calendar'); };
    MD.sel.clear();
    pollStatus(); if (['posts', 'media', 'dashboard', 'calendar'].includes(S.page)) route();
  });
}

// ================================================================== BRAND SETTINGS (each brand: its own apps, IDs, times, links)
async function brandCard() {
  const d = await api('/api/brand');
  const b = d.brand;
  return `<div class="card brandcard" style="grid-column:1/-1;border-top:4px solid ${esc(b.color)}"><div class="card-h"><h2><span class="bdot" style="background:${esc(b.color)}"></span>Brand: ${esc(b.name)}</h2>
      <div class="sub">${d.counts.accounts} account(s) · ${d.counts.scheduled} scheduled · ${d.counts.media} file(s)</div>
      <div class="act"><button class="btn sm" onclick="brandEdit(${b.id},'${esc(b.name).replace(/'/g, '&#39;')}','${esc(b.color)}')">${ic('edit', 14)}Rename / color</button>
      ${d.others.length ? `<select class="in sm" style="width:auto" onchange="if(this.value)brandCopyApps(+this.value,this)"><option value="">Copy app keys from…</option>${d.others.map(o => `<option value="${o.id}">${esc(o.name)} (${o.apps.length} app${o.apps.length === 1 ? '' : 's'})</option>`).join('')}</select>` : ''}</div></div>
    <div class="card-b">
      <p class="small muted" style="margin-top:0">Everything here belongs <b>only to ${esc(b.name)}</b>: its own developer apps (IDs & secrets), accounts, posting times and link tracking. Other brands never see or use them.
      Each account keeps the app it was connected with, so changing keys here never breaks another brand.</p>
      <div class="appgrid">${d.apps.map(a => `<div class="appcard ${a.complete ? 'ok' : ''}" data-app="${a.key}">
        <div class="row nw" style="gap:8px"><span class="nb" style="background:${esc(a.color)}">${esc(a.abbr)}</span><b class="ell">${esc(a.networks.join(' + '))}</b><span class="sp"></span>
          <span class="pill ${a.complete ? 'g' : ''}">${a.complete ? 'Saved' : 'Not set'}</span></div>
        <div class="tiny muted mb-s">${a.accounts} account(s) in this brand use it</div>
        ${a.fields.map(f => `<div class="field" style="margin-bottom:8px"><label class="f">${esc(f.label)}</label><input class="in sm" data-f="${f.key}" type="${f.secret ? 'password' : 'text'}" autocomplete="off"
          value="${!f.secret ? esc(a.saved[f.key] || '') : ''}" placeholder="${f.secret && a.saved[f.key] ? '•••••• saved — leave empty to keep' : ''}"></div>`).join('')}
        <div class="row"><button class="btn sm pri" onclick="brandAppSave('${a.key}',this)">Save</button>${a.complete || Object.values(a.saved).some(Boolean) ? `<button class="btn sm ghost" onclick="brandAppClear('${a.key}',this)">Clear</button>` : ''}
          <span class="sp"></span><button class="btn sm ghost" onclick="brandAppGuide('${a.key}')">${ic('alert', 13)}How to get these</button></div></div>`).join('')}</div>
      <div class="tiny muted mt-s">OAuth redirect address to register in every developer app: <code>${esc(d.redirect)}</code></div>
    </div></div>`;
}
let BRAND_APPS = null;
async function brandAppSave(key, btn) {
  const box = btn.closest('.appcard');
  const vals = {};
  $$('[data-f]', box).forEach(i => { if (i.value.trim()) vals[i.dataset.f] = i.value.trim(); });
  await run(btn, async () => { await post('/api/brand/apps/' + key, vals); toast('Saved for this brand', 'ok'); await loadAccounts(); route(); });
}
async function brandAppClear(key, btn) {
  if (!await confirmBox('Clear these keys?', 'Only this brand loses them. Accounts already connected keep working (they keep the app they logged in with).', 'Clear', true)) return;
  await run(btn, async () => { await post('/api/brand/apps/' + key, {clear: true}); toast('Cleared', 'ok'); await loadAccounts(); route(); });
}
async function brandCopyApps(from, sel) {
  const name = sel.options[sel.selectedIndex].text;
  sel.value = '';
  if (!await confirmBox('Copy app keys?', `All developer app keys of “${esc(name)}” are copied into this brand (this brand's own keys for the same apps are replaced).`, 'Copy')) return;
  await run(null, async () => { const r = await post('/api/brand/apps-copy', {from}); toast(`${r.copied} app(s) copied`, 'ok'); await loadAccounts(); route(); });
}
async function brandAppGuide(key) {
  if (!BRAND_APPS) BRAND_APPS = (await api('/api/brand')).apps;
  const a = BRAND_APPS.find(x => x.key === key);
  const m = modal({title: `How to get the ${esc(a.networks.join(' + '))} keys`, size: 'mid', body: `<ol class="guide">${a.guide.map(g => `<li>${g}</li>`).join('')}</ol>`, foot: '<button class="btn" data-c>Close</button>'});
  m.el.querySelector('[data-c]').onclick = m.close;
}
function speedCard(s) {
  return `<div class="card"><div class="card-h"><h2>${ic('bolt')}Publishing speed</h2><div class="sub">all brands</div></div><div class="card-b">
    <div class="grid g2" style="gap:12px"><div class="field"><label class="f">Posts at the same time</label><select class="in" id="spPar">${[1, 2, 3, 4, 6, 8, 10, 12].map(n => `<option ${s.parallel === n ? 'selected' : ''}>${n}</option>`).join('')}</select>
      <div class="hint">Always different accounts — one account never posts twice at once.</div></div>
    <div class="field"><label class="f">Video uploads at the same time</label><select class="in" id="spVid">${[1, 2, 3, 4, 6].map(n => `<option ${s.video_parallel === n ? 'selected' : ''}>${n}</option>`).join('')}</select>
      <div class="hint">Big videos share your internet: 2 is right for most connections, more only on fast fiber.</div></div></div>
    <button class="btn pri" onclick="run(this,async()=>{await post('/api/settings',{parallel:+$('#spPar').value,video_parallel:+$('#spVid').value});toast('Saved','ok')})">Save</button></div></div>`;
}

// ---- Settings page: the brand card on top, speed card, brand-labelled UTM / publishing
(function () {
  const orig = PAGES.settings.render;
  PAGES.settings.render = async (el, arg) => {
    await orig(el, arg);
    BRAND_APPS = null;
    const s = await api('/api/settings');
    const grid = $('.grid.g2', el);
    if (!grid) return;
    const holder = document.createElement('div');
    holder.style.display = 'contents';
    holder.innerHTML = await brandCard() + speedCard(s);
    grid.prepend(holder);
    // these settings belong to the current brand: say so
    $$('.card-h h2', el).forEach(h => { if (/Link tracking/.test(h.textContent)) h.insertAdjacentHTML('afterend', '<div class="sub">this brand</div>'); });

  };
  // Accounts page: make clear the accounts + schedule are this brand's
  const accOrig = PAGES.accounts.render;
  PAGES.accounts.render = async (el, arg) => {
    await accOrig(el, arg);
    const br = S.status && S.status.brands.find(b => b.id === S.status.brand_id);
    if (!br) return;
    el.insertAdjacentHTML('afterbegin', `<div class="alert b small" style="border-left:4px solid ${esc(br.color)}">${ic('users', 16)}<div>You are in brand <b>${esc(br.name)}</b>. Accounts you connect here — and the developer app keys they use — belong only to this brand.
      <a href="#/settings">Brand app keys & settings</a></div></div>`);
    $$('.card-h h2', el).forEach(h => { if (/Default posting schedule/.test(h.textContent)) h.innerHTML = `${ic('clock')}Posting times · ${esc(br.name)}`; });
  };
  // Automation page: bulk scheduling card
  const autoOrig = PAGES.automation.render;
  PAGES.automation.render = async (el, arg) => {
    await autoOrig(el, arg);
    const grid = $('.grid.g2', el);
    if (grid) grid.insertAdjacentHTML('afterbegin', `<div class="card"><div class="card-h"><h2>${ic('upload')}Bulk schedule videos & photos</h2><div class="sub">hundreds at once</div></div><div class="card-b">
      <p class="small muted" style="margin-top:0">Pick many files from the library (or drop new ones), choose the accounts, write the caption once with <code>{name}</code>, and PostPilot spreads them over your posting times — respecting each network's daily limits.</p>
      <button class="btn pri" onclick="bulkDialog()">${ic('upload', 15)}Bulk schedule</button></div></div>`);
  };
})();

// ---- connect dialog: show which brand the keys are saved for
(function () {
  const orig = window.connectDialog;
  window.connectDialog = function (key) {
    orig(key);
    const br = S.status && S.status.brands.find(b => b.id === S.status.brand_id);
    const n = S.nets[key];
    const body = $('#modalRoot .modal:last-child .modal-b');
    if (br && body && n && n.app_fields.length) {
      body.insertAdjacentHTML('afterbegin', `<div class="alert b small" style="border-left:4px solid ${esc(br.color)}">${ic('key', 16)}<div>The app keys and this login are saved for brand <b>${esc(br.name)}</b> only.</div></div>`);
    }
  };
})();
