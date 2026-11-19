/* PostPilot — the post composer: accounts, per-network text, media, AI, scheduling and live previews. */
'use strict';

const THREADABLE = ['x', 'threads', 'bluesky', 'mastodon'];
const EMOJI = ['😀', '😍', '🔥', '🚀', '✨', '🎉', '💡', '✅', '👉', '📣', '💥', '❤️', '🙏', '👏', '💪', '🌟', '📅', '📍', '🛒', '💰', '🎁', '⏰', '📸', '🎥', '👇', '⚡', '🤝', '🌍', '😂', '🤩'];
let C = null;

function segLen(t) {
  try { return [...new Intl.Segmenter(undefined, {granularity: 'grapheme'}).segment(t)].length; } catch (e) { return [...t].length; }
}
function netLen(text, network) {
  if (network === 'x') return [...text.replace(/https?:\/\/[^\s<>"')\]]+/gi, 'x'.repeat(23))].reduce((n, ch) => n + (ch.codePointAt(0) > 0x2FFF ? 2 : 1), 0);
  if (network === 'bluesky' || network === 'threads') return segLen(text);
  return text.length;
}
function textFor(acc) {
  let t = (C.custom[acc.id] ?? '') || C.text;
  const link = C.link.trim();
  if (link && !t.includes(link)) t = (t + '\n\n' + link).trim();
  return t;
}
function selAccounts() { return S.accounts.filter(a => C.accounts.has(a.id)); }

function openComposer(opts = {}) {
  const p = opts.post;
  C = {
    id: p ? p.id : null, ideaId: opts.ideaId || null,
    accounts: new Set(p ? p.targets.map(t => t.account_id).filter(id => S.accounts.some(a => a.id === id)) : (S.lastAccounts || []).filter(id => S.accounts.some(a => a.id === id))),
    text: p ? p.text : (opts.text || ''), custom: {}, options: {},
    media: p ? p.media_items : (opts.media || []), first_comment: p ? p.first_comment : '', link: p ? p.link : '', utm: p ? !!p.utm : false,
    mode: p && p.status === 'scheduled' ? 'schedule' : (opts.when ? 'schedule' : 'queue'),
    when: p && p.scheduled_at ? p.scheduled_at : (opts.when || Math.floor(Date.now() / 1000 / 900) * 900 + 3600),
    recurring: p && p.recurring && p.recurring.every_days ? {...p.recurring} : null, tab: 'all',
  };
  if (p) p.targets.forEach(t => { if (t.text) C.custom[t.account_id] = t.text; C.options[t.account_id] = t.options || {}; });
  const m = modal({title: p ? 'Edit post' : 'Create post', size: 'full', body: `<div class="comp" id="comp">
      <div class="comp-acc" id="cAcc"></div>
      <div class="comp-ed" id="cEd"></div>
      <div class="comp-pv" id="cPv"></div></div>`,
    foot: `<span class="small muted" id="cFootNote" style="margin-right:auto"></span><button class="btn" data-c>Cancel</button><button class="btn" id="cDraft">Save draft</button><button class="btn pri lg" id="cGo"></button>`,
    onClose: () => { C = null; }});
  m.el.querySelector('.modal-b').style.padding = '0';
  m.el.querySelector('[data-c]').onclick = m.close;
  C.modal = m;
  $('#cDraft').onclick = () => saveComposer('draft', $('#cDraft'));
  $('#cGo').onclick = () => saveComposer(C.mode, $('#cGo'));
  drawAccounts(); drawEditor(); drawPreviews(); updateFooter(); validateLater();
  setTimeout(() => { const ta = $('#cText'); if (ta) { ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length); } }, 60);
}

// ------------------------------------------------------------------ accounts column
function drawAccounts() {
  const el = $('#cAcc');
  if (!S.accounts.length) {
    el.innerHTML = `<div class="small muted" style="padding:6px">No accounts connected yet.</div><button class="btn pri sm mt-s" onclick="C.modal.close();go('accounts')">${ic('plus', 14)}Connect accounts</button>`;
    return;
  }
  el.innerHTML = `<div class="row mb-s" style="justify-content:space-between"><b class="small">Post to</b><a href="#" class="small" onclick="toggleAllAcc();return false">${C.accounts.size === S.accounts.length ? 'None' : 'All'}</a></div>
    ${S.accounts.map(a => `<div class="accpick ${C.accounts.has(a.id) ? 'on' : ''} ${a.status === 'expired' || a.status === 'paused' ? 'off' : ''}" onclick="toggleAcc(${a.id})" title="${esc(a.network_name)}${a.status !== 'ok' ? ' — ' + a.status : ''}">
      ${avatar(a, true)}<div style="min-width:0"><div class="nm ell">${esc(a.name)}</div><div class="hd ell">${esc(a.network_name)}${a.status === 'expired' ? ' · reconnect!' : ''}</div></div></div>`).join('')}`;
}
function toggleAcc(id) { C.accounts.has(id) ? C.accounts.delete(id) : C.accounts.add(id); if (!C.accounts.has(+C.tab)) C.tab = 'all'; drawAccounts(); drawEditor(); drawPreviews(); updateFooter(); validateLater(); }
function toggleAllAcc() { if (C.accounts.size === S.accounts.length) C.accounts.clear(); else S.accounts.forEach(a => C.accounts.add(a.id)); C.tab = 'all'; drawAccounts(); drawEditor(); drawPreviews(); updateFooter(); validateLater(); }

// ------------------------------------------------------------------ editor column
function drawEditor() {
  const el = $('#cEd');
  const accs = selAccounts();
  const tabAcc = C.tab !== 'all' ? accs.find(a => a.id === +C.tab) : null;
  const nets = [...new Set(accs.map(a => a.network))];
  const wantsComment = accs.some(a => (netMeta(a.network).supports || []).includes('first_comment'));
  const cur = tabAcc ? (C.custom[tabAcc.id] ?? '') : C.text;
  el.innerHTML = `
    <div class="vtabs"><button class="vtab ${C.tab === 'all' ? 'on' : ''}" onclick="C.tab='all';drawEditor();drawPreviews()">${icInline('globe', 13)} Main text</button>
      ${accs.map(a => `<button class="vtab ${C.tab == a.id ? 'on' : ''}" onclick="C.tab='${a.id}';drawEditor();drawPreviews()" title="Customize for ${esc(a.name)}">${nb(a.network)}${esc(a.name.slice(0, 14))}${C.custom[a.id] ? ' ✎' : ''}</button>`).join('')}</div>
    ${tabAcc ? `<div class="small muted mb-s">Customize the text for <b>${esc(tabAcc.name)}</b> only. Leave empty to use the main text. ${C.custom[tabAcc.id] ? '' : `<a href="#" onclick="C.custom[${tabAcc.id}]=C.text;drawEditor();drawPreviews();return false">Start from the main text</a>`}</div>` : ''}
    <div class="edbox"><textarea id="cText" placeholder="${tabAcc ? 'Using the main text — type here to customize for this account…' : 'What do you want to share? ✨'}">${esc(cur)}</textarea>
      <div class="edtools">
        <button class="btn ghost sm" title="Emoji" onclick="emojiMenu(this)">${ic('smile', 16)}</button>
        <button class="btn ghost sm" title="Hashtag groups" onclick="hashMenu(this)">${ic('hash', 16)}</button>
        <button class="btn ghost sm" title="Templates" onclick="tplMenu(this)">${ic('file', 16)}</button>
        <button class="btn ghost sm" title="Add photos or videos" onclick="$('#cFile').click()">${ic('image', 16)}</button>
        <span class="sp"></span><button class="btn soft sm" onclick="aiMenu(this)">${ic('spark', 15)}AI assist</button>
      </div></div>
    <div class="counters">${accs.length ? nets.map(n => {
      const meta = netMeta(n);
      const longest = Math.max(...accs.filter(a => a.network === n).map(a => netLen(textFor(a), n)));
      const over = longest > meta.text_limit;
      const thread = over && THREADABLE.includes(n);
      return `<span class="cnt-chip ${thread ? 'thread' : over ? 'over' : ''}" title="${esc(meta.name)}">${nb(n)}${longest}/${meta.text_limit}${thread ? ` · thread of ${Math.ceil(longest / (meta.text_limit - 8))}` : ''}</span>`;
    }).join('') : '<span class="small muted">← Pick the accounts to post to</span>'}</div>
    <input type="file" id="cFile" multiple hidden accept="image/*,video/*">
    <div class="mstrip" id="cMedia"></div>
    ${tabAcc ? accountOptions(tabAcc) : ''}
    <div class="grid g2 mt" style="gap:12px">
      <div><label class="f">${icInline('link', 13)} Link (optional)</label><input class="in sm" id="cLink" placeholder="https://your-site.com/offer" value="${esc(C.link)}">
        <label class="switch small mt-s"><input type="checkbox" id="cUtm" ${C.utm ? 'checked' : ''}><span class="tr"></span>Add UTM tracking (see visits per network in Google Analytics)</label></div>
      ${wantsComment ? `<div><label class="f">${icInline('chat', 13)} First comment (Facebook, Instagram)</label><input class="in sm" id="cComment" placeholder="e.g. extra hashtags or a link" value="${esc(C.first_comment)}"></div>` : '<div></div>'}
    </div>
    <hr class="sep">
    <label class="f">When</label>
    <div class="chips mb-s">${[['now', 'Post now', 'send'], ['queue', 'Add to queue', 'list'], ['schedule', 'Pick a time', 'cal']].map(([k, l, i]) =>
      `<button class="chip ${C.mode === k ? 'on' : ''}" onclick="C.mode='${k}';drawEditor();updateFooter()">${icInline(i, 13)} ${l}</button>`).join('')}</div>
    <div id="cWhen"></div>
    <div class="row mt-s"><label class="switch small"><input type="checkbox" id="cRec" ${C.recurring ? 'checked' : ''}><span class="tr"></span>${icInline('repeat', 13)} Repeat (evergreen)</label>
      ${C.recurring ? `<span class="small muted">every</span><input class="in sm" type="number" min="1" id="cRecDays" value="${C.recurring.every_days || 7}" style="width:70px"><span class="small muted">days,</span>
      <input class="in sm" type="number" min="0" id="cRecTimes" value="${C.recurring.times || 0}" style="width:70px"><span class="small muted">times (0 = forever)</span>` : ''}</div>
    <div class="problems" id="cProblems"></div>`;
  const ta = $('#cText');
  ta.oninput = () => {
    if (tabAcc) C.custom[tabAcc.id] = ta.value; else C.text = ta.value;
    drawCounters(); drawPreviewsLater(); validateLater();
  };
  $('#cLink').oninput = e => { C.link = e.target.value; drawPreviewsLater(); validateLater(); };
  $('#cUtm').onchange = e => { C.utm = e.target.checked; };
  if ($('#cComment')) $('#cComment').oninput = e => { C.first_comment = e.target.value; };
  $('#cRec').onchange = e => { C.recurring = e.target.checked ? {every_days: 7, times: 0} : null; drawEditor(); };
  if ($('#cRecDays')) { $('#cRecDays').oninput = e => { C.recurring.every_days = +e.target.value; }; $('#cRecTimes').oninput = e => { C.recurring.times = +e.target.value; }; }
  $('#cFile').onchange = async e => { const added = await uploadFiles(e.target.files); C.media.push(...added); drawMedia(); drawPreviews(); validateLater(); e.target.value = ''; };
  if (tabAcc) bindAccountOptions(tabAcc);
  drawMedia(); drawWhen();
}
function drawCounters() {
  // cheap partial redraw of the counter chips only
  const accs = selAccounts();
  const nets = [...new Set(accs.map(a => a.network))];
  const box = $('.counters');
  if (!box || !accs.length) return;
  box.innerHTML = nets.map(n => {
    const meta = netMeta(n);
    const longest = Math.max(...accs.filter(a => a.network === n).map(a => netLen(textFor(a), n)));
    const over = longest > meta.text_limit, thread = over && THREADABLE.includes(n);
    return `<span class="cnt-chip ${thread ? 'thread' : over ? 'over' : ''}" title="${esc(meta.name)}">${nb(n)}${longest}/${meta.text_limit}${thread ? ` · thread of ${Math.ceil(longest / (meta.text_limit - 8))}` : ''}</span>`;
  }).join('');
}
function accountOptions(a) {
  const meta = netMeta(a.network), o = C.options[a.id] || (C.options[a.id] = {}), sup = meta.supports || [];
  const rows = [];
  if (sup.includes('board') && a.boards) rows.push(`<div><label class="f">Board</label><select class="in sm" data-opt="board_id">${a.boards.map(b => `<option value="${b.id}" ${(o.board_id || a.board_id) === b.id ? 'selected' : ''}>${esc(b.name)}</option>`).join('')}</select></div>`);
  if (sup.includes('title')) rows.push(`<div><label class="f">${a.network === 'youtube' ? 'Video title' : 'Title'}</label><input class="in sm" data-opt="title" maxlength="100" value="${esc(o.title || '')}" placeholder="${a.network === 'pinterest' ? 'Pin title (max 100)' : 'Max 100 characters'}"></div>`);
  if (sup.includes('privacy')) rows.push(`<div><label class="f">Visibility</label><select class="in sm" data-opt="privacy">${['public', 'unlisted', 'private'].map(v => `<option ${o.privacy === v ? 'selected' : ''}>${v}</option>`).join('')}</select></div>`);
  if (a.network === 'x') rows.push(`<div><label class="switch small" style="margin-top:22px"><input type="checkbox" data-opt="thread" ${o.thread !== false ? 'checked' : ''}><span class="tr"></span>Split long text into a thread</label></div>`);
  if (sup.includes('first_comment')) rows.push(`<div><label class="f">First comment for this account</label><input class="in sm" data-opt="first_comment" value="${esc(o.first_comment || '')}" placeholder="(uses the general first comment)"></div>`);
  return rows.length ? `<div class="card mt" style="box-shadow:none;margin-bottom:0"><div class="card-b" style="padding:12px 14px"><div class="small b mb-s">${nb(a.network)} ${esc(meta.name)} options</div><div class="grid g2" style="gap:10px">${rows.join('')}</div></div></div>` : '';
}
function bindAccountOptions(a) {
  $$('[data-opt]', $('#cEd')).forEach(inp => {
    const save = () => { C.options[a.id][inp.dataset.opt] = inp.type === 'checkbox' ? inp.checked : inp.value; drawPreviewsLater(); validateLater(); };
    inp.oninput = save; inp.onchange = save;
  });
}

// ------------------------------------------------------------------ media strip
function drawMedia() {
  const box = $('#cMedia');
  if (!box) return;
  box.innerHTML = C.media.map((m, i) => `<div class="mitem" draggable="true" data-i="${i}">${mthumb(m)}${m.kind === 'video' ? '<span class="vid">VIDEO</span>' : ''}
      <button class="alt ${m.alt ? 'has' : ''}" title="Alt text" onclick="editAlt(${i})">ALT</button><button class="rm" title="Remove" onclick="C.media.splice(${i},1);drawMedia();drawPreviews();validateLater()">✕</button></div>`).join('')
    + `<label class="madd" id="cDrop" title="Upload or drop files">${ic('upload', 20)}<span>Upload<br>or drop</span><input type="file" multiple hidden accept="image/*,video/*" onchange="addMediaFiles(this.files);this.value=''"></label>
       <div class="madd" onclick="pickFromLibrary()">${ic('image', 20)}<span>From<br>library</span></div>`;
  let dragI = null;
  $$('.mitem', box).forEach(it => {
    it.ondragstart = () => { dragI = +it.dataset.i; };
    it.ondragover = e => e.preventDefault();
    it.ondrop = e => { e.preventDefault(); const to = +it.dataset.i; if (dragI === null || dragI === to) return; const [x] = C.media.splice(dragI, 1); C.media.splice(to, 0, x); drawMedia(); drawPreviews(); };
  });
  const dz = $('#cDrop');
  const zone = $('#comp');
  zone.ondragover = e => { if (e.dataTransfer.types.includes('Files')) { e.preventDefault(); dz.classList.add('on'); } };
  zone.ondragleave = () => dz.classList.remove('on');
  zone.ondrop = e => { if (e.dataTransfer.files.length) { e.preventDefault(); dz.classList.remove('on'); addMediaFiles(e.dataTransfer.files); } };
}
async function addMediaFiles(files) { const added = await uploadFiles(files); C.media.push(...added); drawMedia(); drawPreviews(); validateLater(); }
function editAlt(i) {
  const m = C.media[i];
  const md = modal({title: 'Alt text', body: `<img src="${m.thumb}" style="max-width:100%;max-height:220px;border-radius:10px;display:block;margin:0 auto 12px">
    <div class="field"><label class="f">Describe the image for people who can't see it</label><textarea class="in" id="altT" rows="3">${esc(m.alt || '')}</textarea></div>`,
    foot: `<button class="btn" data-c>Cancel</button><button class="btn pri" id="altS">Save</button>`});
  md.el.querySelector('[data-c]').onclick = md.close;
  $('#altS').onclick = async () => { m.alt = $('#altT').value; await post(`/api/media/${m.id}`, {alt: m.alt}); md.close(); drawMedia(); };
}
async function pickFromLibrary() {
  const d = await api('/api/media');
  if (!d.media.length) return toast('Your library is empty — upload files first.');
  const sel = new Set();
  const md = modal({title: 'Media library', size: 'wide', body: `<div class="mgrid">${d.media.map(m => `<div class="mcard" data-id="${m.id}"><span class="ck">${ic('check', 14)}</span><div class="img">${mthumb(m)}</div><div class="cap ell">${esc(m.name)}</div></div>`).join('')}</div>`,
    foot: `<button class="btn" data-c>Cancel</button><button class="btn pri" id="libAdd">Add selected</button>`});
  md.el.querySelector('[data-c]').onclick = md.close;
  $$('.mcard', md.el).forEach(c => c.onclick = () => { const id = +c.dataset.id; sel.has(id) ? sel.delete(id) : sel.add(id); c.classList.toggle('sel'); });
  $('#libAdd').onclick = () => { d.media.filter(m => sel.has(m.id)).forEach(m => C.media.push(m)); md.close(); drawMedia(); drawPreviews(); validateLater(); };
}

// ------------------------------------------------------------------ insert menus
function insertText(t, replace = false) {
  const ta = $('#cText');
  if (replace) ta.value = t;
  else { const s = ta.selectionStart ?? ta.value.length; ta.setRangeText(t, s, ta.selectionEnd ?? s, 'end'); }
  ta.dispatchEvent(new Event('input')); ta.focus();
}
function emojiMenu(btn) {
  $$('.menu').forEach(m => m.remove());
  const m = document.createElement('div');
  m.className = 'menu';
  m.style.cssText += ';display:grid;grid-template-columns:repeat(6,1fr);min-width:0;width:240px';
  m.innerHTML = EMOJI.map(e => `<button style="justify-content:center;font-size:20px;padding:6px">${e}</button>`).join('');
  document.body.appendChild(m);
  const r = btn.getBoundingClientRect();
  m.style.top = (r.bottom + 6 + scrollY) + 'px'; m.style.left = r.left + 'px';
  m.onclick = e => { const b = e.target.closest('button'); if (b) insertText(b.textContent); };
  setTimeout(() => document.addEventListener('click', function h(e) { if (!m.contains(e.target) && e.target !== btn) { m.remove(); document.removeEventListener('click', h); } }), 0);
}
async function hashMenu(btn) {
  const d = await api('/api/hashtag_groups');
  popMenu(btn, [...d.items.map(g => ({label: `${g.name} — ${g.tags.slice(0, 40)}`, icon: 'hash', run: () => insertText((/\s$/.test($('#cText').value) || !$('#cText').value ? '' : '\n\n') + g.tags)})),
    '-', {label: 'Suggest hashtags with AI', icon: 'spark', run: () => aiRun('hashtags')}, {label: 'Manage hashtag groups…', icon: 'gear', run: () => { C.modal.close(); go('settings'); }}]);
}
async function tplMenu(btn) {
  const d = await api('/api/templates');
  popMenu(btn, [...d.items.map(t => ({label: t.name, icon: 'file', run: () => insertText(t.text, !$('#cText').value.trim())})), '-', {label: 'Manage templates…', icon: 'gear', run: () => { C.modal.close(); go('settings'); }}]);
}
function aiMenu(btn) {
  const tabAcc = C.tab !== 'all' ? selAccounts().find(a => a.id === +C.tab) : null;
  const items = [
    {label: 'Write a post from my idea', icon: 'spark', run: () => aiRun('write')},
    {label: 'Improve (more engaging)', icon: 'bolt', run: () => aiRun('rewrite', {tone: 'more engaging'})},
    {label: 'More professional', icon: 'users', run: () => aiRun('rewrite', {tone: 'professional'})},
    {label: 'More fun & casual', icon: 'smile', run: () => aiRun('rewrite', {tone: 'fun and casual'})},
    {label: 'Make it shorter', icon: 'x', run: () => aiRun('shorten')},
    {label: 'Suggest hashtags', icon: 'hash', run: () => aiRun('hashtags')},
    {label: 'Translate…', icon: 'globe', run: () => translateDialog()},
  ];
  if (tabAcc) items.push('-', {label: `Adapt for ${netMeta(tabAcc.network).name}`, icon: 'refresh', run: () => aiRun('adapt', {network: tabAcc.network})});
  else if (selAccounts().length > 1) items.push('-', {label: 'Adapt for every network (custom texts)', icon: 'refresh', run: () => adaptAll()});
  popMenu(btn, items);
}
async function aiRun(task, extra = {}) {
  const ta = $('#cText');
  const src = ta.value.trim() || C.text.trim();
  if (!src) return toast(task === 'write' ? 'Type your idea first (a few words), then click Write.' : 'Write something first.', 'warn');
  const tabAcc = C.tab !== 'all' ? selAccounts().find(a => a.id === +C.tab) : null;
  const net = extra.network || (tabAcc ? tabAcc.network : (selAccounts()[0] || {}).network || '');
  const t = document.createElement('div'); t.className = 'toast'; t.innerHTML = '<span class="spin"></span><div>AI is writing…</div>'; $('#toasts').appendChild(t);
  try {
    const r = await post('/api/ai', {task, text: src, network: net, ...extra});
    aiResult(task, r.text, r.provider);
  } catch (e) { toast(e.message, 'err'); }
  finally { t.remove(); }
}
function aiResult(task, text, provider) {
  const md = modal({title: `${icInline('spark', 16)} AI suggestion`, size: 'mid', body: `<textarea class="in" id="aiOut" rows="10">${esc(text)}</textarea>
    <div class="tiny muted mt-s">${provider === 'anthropic' ? 'Written by Claude' : provider === 'ollama' ? 'Written by your local AI (Ollama)' : 'Basic helper (connect an AI in Settings for better results)'} — edit before using.</div>`,
    foot: `<button class="btn" data-c>Cancel</button><button class="btn" id="aiCopy">${ic('copy', 15)}Copy</button><button class="btn" id="aiIns">Insert</button><button class="btn pri" id="aiRep">${task === 'hashtags' ? 'Add to post' : 'Replace text'}</button>`});
  md.el.querySelector('[data-c]').onclick = md.close;
  $('#aiCopy').onclick = () => copyText($('#aiOut').value);
  $('#aiIns').onclick = () => { md.close(); insertText($('#aiOut').value); };
  $('#aiRep').onclick = () => { const v = $('#aiOut').value; md.close(); if (task === 'hashtags') insertText('\n\n' + v); else insertText(v, true); };
}
function translateDialog() {
  const langs = ['English', 'French', 'Arabic', 'Spanish', 'German', 'Italian', 'Portuguese', 'Turkish', 'Dutch', 'Moroccan Darija'];
  const md = modal({title: 'Translate', body: `<div class="field"><label class="f">Into</label><select class="in" id="trL">${langs.map(l => `<option>${l}</option>`).join('')}</select></div>`,
    foot: `<button class="btn" data-c>Cancel</button><button class="btn pri" id="trGo">Translate</button>`});
  md.el.querySelector('[data-c]').onclick = md.close;
  $('#trGo').onclick = () => { const l = $('#trL').value; md.close(); aiRun('translate', {lang: l}); };
}
async function adaptAll() {
  const src = C.text.trim();
  if (!src) return toast('Write the main text first.', 'warn');
  const accs = selAccounts();
  const t = document.createElement('div'); t.className = 'toast'; $('#toasts').appendChild(t);
  let done = 0;
  try {
    for (const n of [...new Set(accs.map(a => a.network))]) {
      t.innerHTML = `<span class="spin"></span><div>Adapting for ${esc(netMeta(n).name)}… (${++done})</div>`;
      const r = await post('/api/ai', {task: 'adapt', text: src, network: n});
      accs.filter(a => a.network === n).forEach(a => { C.custom[a.id] = r.text; });
    }
    toast('Each network now has its own version — check the tabs ✎', 'ok');
    drawEditor(); drawPreviews(); validateLater();
  } catch (e) { toast(e.message, 'err'); }
  finally { t.remove(); }
}

// ------------------------------------------------------------------ when
async function drawWhen() {
  const box = $('#cWhen');
  if (!box) return;
  const ids = [...C.accounts];
  if (C.mode === 'now') { box.innerHTML = '<div class="small muted">Publishes right after you click — usually within seconds.</div>'; return; }
  if (C.mode === 'queue') {
    box.innerHTML = '<div class="small muted">Finding the next free slot…</div>';
    const r = await api('/api/queue/next?accounts=' + ids.join(','));
    C.queueTs = r.ts;
    if ($('#cWhen') === box) box.innerHTML = `<div class="small">Next free slot: <b>${fdate(r.ts, {wd: true})}</b> <span class="muted">(${rel(r.ts)}) · change your time slots in Accounts</span></div>`;
    updateFooter();
    return;
  }
  const local = new Date(C.when * 1000 - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  box.innerHTML = `<div class="row"><input type="datetime-local" class="in sm" id="cDt" value="${local}" style="width:auto"><span class="small muted" id="cRel">${rel(C.when)}</span></div><div class="row mt-s" id="cBest"></div>`;
  $('#cDt').onchange = e => { C.when = Math.floor(new Date(e.target.value) / 1000); $('#cRel').textContent = rel(C.when); updateFooter(); };
  const nets = [...new Set(selAccounts().map(a => a.network))];
  if (!nets.length) return;
  const r = await api('/api/best-times?networks=' + nets.join(','));
  const hours = {};
  r.best.forEach(b => b.hours.forEach((h, i) => { hours[h] = (hours[h] || 0) + (3 - i); }));
  const top = Object.entries(hours).sort((a, b) => b[1] - a[1]).slice(0, 4).map(x => +x[0]);
  const chips = top.map(h => { const d = new Date(); d.setHours(h, 0, 0, 0); if (d < new Date(Date.now() + 600000)) d.setDate(d.getDate() + 1); return d; }).sort((a, b) => a - b);
  const learned = r.best.some(b => b.learned);
  const bb = $('#cBest');
  if (bb) bb.innerHTML = `<span class="tiny muted">${learned ? 'Your best times:' : 'Good times:'}</span>` + chips.map(d => `<button class="bt" data-ts="${Math.floor(d / 1000)}">${icInline('clock', 12)} ${d.toDateString() === new Date().toDateString() ? 'Today' : d.toLocaleDateString(undefined, {weekday: 'short'})} ${d.toTimeString().slice(0, 5)}</button>`).join('');
  $$('.bt', bb || document.createElement('div')).forEach(b => b.onclick = () => { C.when = +b.dataset.ts; drawWhen(); updateFooter(); });
}
function updateFooter() {
  if (!C) return;
  validateLater();
  const n = C.accounts.size;
  const lbl = {now: `Post now`, queue: 'Add to queue', schedule: 'Schedule'}[C.mode];
  $('#cGo').innerHTML = `${ic(C.mode === 'now' ? 'send' : C.mode === 'queue' ? 'list' : 'cal', 16)}${lbl}${n ? ` · ${n} account${n > 1 ? 's' : ''}` : ''}`;
  $('#cFootNote').textContent = C.mode === 'schedule' ? `Publishes ${fdate(C.when, {wd: true})}` : C.mode === 'queue' && C.queueTs ? `Next slot ${fdate(C.queueTs, {wd: true})}` : '';
}

// ------------------------------------------------------------------ validation + save
const validateLater = debounce(async () => {
  if (!C) return;
  const box = $('#cProblems');
  if (!box) return;
  if (!C.accounts.size) { box.innerHTML = ''; return; }
  try {
    const r = await post('/api/posts/validate', {...payload('draft'), mode: C.mode});
    const names = Object.fromEntries(S.accounts.map(a => [a.id, a.name]));
    const lines = Object.entries(r.problems).flatMap(([id, ps]) => ps.map(p => `<div>⚠ <b>${esc(names[id] || id)}:</b> ${esc(p)}</div>`));
    const warns = Object.entries(r.warnings || {}).flatMap(([id, ws]) => ws.map(w => `<div style="color:var(--amber-d)">⏸ <b>${esc(names[id] || id)}:</b> ${esc(w)}</div>`));
    if ($('#cProblems')) $('#cProblems').innerHTML = lines.join('') + warns.join('');
    C.problems = lines.length;
  } catch (e) { /* ignore */ }
}, 400);
function payload(action) {
  return {id: C.id, idea_id: C.ideaId, text: C.text, media: C.media.map(m => m.id), first_comment: C.first_comment, link: C.link.trim(), utm: C.utm,
    targets: [...C.accounts].map(id => ({account_id: id, text: C.custom[id] || '', options: C.options[id] || {}})),
    action, scheduled_at: C.when, recurring: C.recurring};
}
async function saveComposer(action, btn) {
  if (action !== 'draft' && !C.accounts.size) return toast('Pick at least one account on the left.', 'warn');
  if (action === 'schedule' && C.when < Date.now() / 1000 - 60) return toast('That time is in the past.', 'warn');
  await run(btn, async () => {
    const r = await post('/api/posts', payload(action));
    S.lastAccounts = [...C.accounts];
    const p = r.post;
    toast(action === 'draft' ? 'Draft saved' : action === 'now' ? 'Publishing now… 🚀' : `Scheduled for ${fdate(p.scheduled_at, {wd: true})} ✅`, 'ok');
    if (action === 'now' && 'Notification' in window && Notification.permission === 'default' && !S.notifyAsked) { S.notifyAsked = true; Notification.requestPermission(); }
    C.modal.close();
    pollStatus(); refreshPage();
  });
}

// ------------------------------------------------------------------ previews
const drawPreviewsLater = debounce(() => drawPreviews(), 150);
function richText(t) {
  return esc(t).replace(/(https?:\/\/[^\s<]+)/g, '<a>$1</a>').replace(/(^|\s)([#@][\p{L}\p{N}_.]+)/gu, '$1<span class="hl">$2</span>');
}
function splitPreview(text, limit, network) {
  if (netLen(text, network) <= limit) return [text];
  const parts = []; let cur = '';
  for (const w of text.split(/(\s+)/)) { if (netLen(cur + w, network) > limit - 6) { parts.push(cur.trim()); cur = w.trimStart(); } else cur += w; }
  if (cur.trim()) parts.push(cur.trim());
  return parts.map((p, i) => `${p} ${i + 1}/${parts.length}`);
}
function mediaGrid(items, cls = '') {
  if (!items.length) return '';
  const n = Math.min(items.length, 4);
  return `<div class="pv-m n${n} ${cls}">${items.slice(0, 4).map(m => mthumb(m)).join('')}</div>`;
}
function drawPreviews() {
  const el = $('#cPv');
  if (!el || !C) return;
  let accs = selAccounts();
  if (C.tab !== 'all') accs = accs.filter(a => a.id === +C.tab).concat(accs.filter(a => a.id !== +C.tab));
  if (!accs.length) { el.innerHTML = `<div class="empty small">${ic('eye', 26).replace('class="i"', 'class="i" style="margin:0 auto 8px"')}Pick accounts to see live previews.</div>`; return; }
  el.innerHTML = accs.map(a => `<div class="pv-label">${nb(a.network)} ${esc(netMeta(a.network).name)} · ${esc(a.name)}</div>${preview(a)}`).join('');
}
function preview(a) {
  const n = a.network, meta = netMeta(n), text = textFor(a), media = C.media, o = C.options[a.id] || {};
  const head = (sub) => `<div class="pv-h">${avatar(a, true)}<div><div class="nm">${esc(a.name)}</div><div class="meta">${sub}</div></div></div>`;
  const imgs = media.filter(m => m.kind !== 'video');
  if (n === 'instagram') {
    return `<div class="pv ig">${head('Sponsored by you · now')}${media.length ? mediaGrid(media.slice(0, 1)) : '<div class="pv-t" style="color:var(--red)">Instagram needs a photo or video</div>'}
      <div class="pv-a" style="justify-content:flex-start;gap:16px;border:0">♡ 💬 ➤${media.length > 1 ? `<span style="margin-left:auto">● ${'○ '.repeat(Math.min(media.length, 10) - 1)}</span>` : ''}</div>
      <div class="pv-t"><b>${esc((a.handle || '').replace('@', '') || a.name)}</b> ${richText(text)}</div></div>`;
  }
  if (['x', 'bluesky', 'threads', 'mastodon'].includes(n)) {
    const parts = splitPreview(text, meta.text_limit, n);
    return `<div class="pv x">${parts.map((p, i) => `<div class="${i ? 'thr' : ''}"><div class="row1">${avatar(a, true)}<div style="min-width:0;flex:1"><div><b>${esc(a.name)}</b> <span class="meta muted small">${esc(a.handle || '')} · now</span></div>
      <div class="pv-t">${richText(p)}</div>${i === 0 ? mediaGrid(media) : ''}</div></div></div>`).join('')}
      ${parts.length > 1 ? `<div class="vcount">🧵 Thread of ${parts.length} posts</div>` : ''}</div>`;
  }
  if (n === 'telegram') {
    return `<div class="pv tg"><div class="bub">${mediaGrid(media)}<div class="pv-h" style="padding-bottom:0"><div class="nm" style="color:#168acd">${esc(a.name)}</div></div><div class="pv-t">${richText(text)}</div><div class="vcount">👁 1 · now</div></div></div>`;
  }
  if (n === 'pinterest') {
    return `<div class="pv pin" style="border:0;background:none">${imgs[0] ? `<img src="${imgs[0].thumb}">` : '<div class="pv-t" style="color:var(--red)">Pinterest needs an image</div>'}
      <div class="b small mt-s">${esc(o.title || '(add a title in the Pinterest tab)')}</div><div class="tiny muted">${esc(text.slice(0, 120))}</div></div>`;
  }
  if (n === 'youtube') {
    const v = media.find(m => m.kind === 'video');
    return `<div class="pv yt">${v ? `<video src="${v.url}#t=3" ${v.poster ? `poster="${v.poster}"` : ''} preload="metadata" muted style="width:100%;aspect-ratio:16/9;object-fit:cover;background:#000"></video>` : '<div class="thumb">YouTube needs a video</div>'}
      <div class="pv-h">${avatar(a, true)}<div><div class="nm">${esc(o.title || text.split('\n')[0].slice(0, 100) || 'Video title')}</div><div class="meta">${esc(a.name)} · ${esc(o.privacy || 'public')}</div></div></div></div>`;
  }
  if (n === 'discord') {
    return `<div class="pv" style="background:#313338;color:#dbdee1;border:0"><div class="pv-h">${avatar(a, true)}<div><div class="nm" style="color:#fff">${esc(a.name)} <span class="pill" style="background:#5865f2;color:#fff;font-size:9px">BOT</span></div></div></div><div class="pv-t">${richText(text)}</div>${mediaGrid(media)}</div>`;
  }
  if (n === 'reminder') {
    return `<div class="pv"><div class="pv-h"><span class="nb" style="background:#F59E0B;width:30px;height:30px">⏰</span><div><div class="nm">Reminder · ${esc(a.app || a.handle)}</div><div class="meta">You'll get a notification with the caption ready to copy</div></div></div><div class="pv-t">${richText(text)}</div>${mediaGrid(media)}</div>`;
  }
  // facebook, linkedin, demo
  const sub = n === 'linkedin' ? 'now · 🌐' : 'Just now · 🌍';
  const linkCard = C.link && !media.length ? `<div style="border-top:1px solid var(--line);padding:10px 14px;background:var(--surface-2)"><div class="tiny muted">${esc((C.link.match(/\/\/([^/]+)/) || [])[1] || '')}</div><div class="b small">Link preview</div></div>` : '';
  return `<div class="pv">${head(sub)}<div class="pv-t">${richText(text)}</div>${mediaGrid(media)}${linkCard}
    <div class="pv-a">${n === 'linkedin' ? '<span>👍 Like</span><span>💬 Comment</span><span>🔁 Repost</span><span>➤ Send</span>' : '<span>👍 Like</span><span>💬 Comment</span><span>↗ Share</span>'}</div></div>`;
}
