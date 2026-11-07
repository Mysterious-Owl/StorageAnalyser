'use strict';
/* Storage Explorer front end. Talks only to the local server (same origin). */

const TOKEN = document.querySelector('meta[name="token"]').content;
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g,
  c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

async function api(path, body) {
  const opt = body === undefined ? { headers: { 'X-Token': TOKEN } } : {
    method: 'POST', headers: { 'X-Token': TOKEN, 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  };
  const r = await fetch(path, opt);
  const j = await r.json();
  if (!r.ok || j.error) throw new Error(j.error || ('HTTP ' + r.status));
  return j;
}

// ---------------------------------------------------------------- format
function hsize(n) {
  n = +n || 0;
  const u = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  while (Math.abs(n) >= 1024 && i < 4) { n /= 1024; i++; }
  return i === 0 ? `${n} B` : `${n >= 100 ? n.toFixed(0) : n.toFixed(1)} ${u[i]}`;
}
const fmt = n => (+n || 0).toLocaleString();
const fdate = ts => ts ? new Date(ts * 1000).toLocaleDateString(undefined,
  { year: 'numeric', month: 'short', day: 'numeric' }) : '—';
const pct = x => !x ? '0%' : (x < 0.001 ? '<0.1%' : (x * 100).toFixed(x < 0.1 ? 1 : 0) + '%');
const plural = (n, w) => `${fmt(n)} ${w}${n === 1 ? '' : 's'}`;

const IC = {
  folder: '<svg viewBox="0 0 24 24"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>',
  file: '<svg viewBox="0 0 24 24"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/></svg>',
  files: '<svg viewBox="0 0 24 24"><path d="M15 3H8a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h9a2 2 0 0 0 2-2V7z"/><path d="M3 8v11a2 2 0 0 0 2 2h9"/></svg>',
  open: '<svg viewBox="0 0 24 24"><path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/></svg>',
  dup: '<svg viewBox="0 0 24 24"><rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/></svg>',
  copy: '<svg viewBox="0 0 24 24"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a1 1 0 0 1 1-1h10"/></svg>',
  drive: '<svg viewBox="0 0 24 24"><rect x="3" y="13" width="18" height="7" rx="2"/><path d="M5 13l2-8h10l2 8M17 16.5h.01"/></svg>',
  tree: '<svg viewBox="0 0 24 24"><rect x="3" y="3" width="10" height="18" rx="1.5"/><rect x="15" y="3" width="6" height="8" rx="1.5"/><rect x="15" y="13" width="6" height="8" rx="1.5"/></svg>',
  scan: '<svg viewBox="0 0 24 24"><path d="M3 12a9 9 0 1 0 3-6.7M3 4v5h5"/></svg>',
  down: '<svg viewBox="0 0 24 24"><path d="M12 3v12M7 10l5 5 5-5M5 21h14"/></svg>',
};

// ---------------------------------------------------------------- state
const st = {
  init: null, status: null, page: 'scan',
  sel: new Set(), user: true, extra: [],
  dupOpts: { minKb: 1, skipDev: true, skipSystem: true },
  folder: null, levelRoot: 'all', level: null,
  gq: { name: '', path: '', match: 'all', drives: 'all', minLevels: 0, sort: 'reclaimable', offset: 0, limit: 200 },
  pairsMin: 10, scanStamp: undefined, dupStamp: undefined, idStamp: undefined,
  idOpts: { minKb: 1, skipDev: true, skipSystem: true, verify: false, path: '' },
  // extension filters: mode all | only | except, list of extensions (no dot, '' = no extension)
  ext: { dups: { mode: 'all', list: [] }, id: { mode: 'all', list: [] } },
  extStats: null,
  iq: { name: '', path: '', drives: 'all', minMb: '', sort: 'reclaimable', offset: 0, limit: 200 },
  keep: { rule: 'newest', text: '' },
  wasBusy: false, wantStorage: false, renderSeq: 0, pollTimer: null,
};
const GQ_DEFAULT = { ...st.gq };

// ---------------------------------------------------------------- ui helpers
const tip = $('#tip');
function showTip(html, e) {
  tip.innerHTML = html;
  tip.classList.add('on');
  const x = Math.min(e.clientX + 14, innerWidth - tip.offsetWidth - 8);
  const y = Math.min(e.clientY + 14, innerHeight - tip.offsetHeight - 8);
  tip.style.left = x + 'px';
  tip.style.top = y + 'px';
}
const hideTip = () => tip.classList.remove('on');
let toastT;
function toast(msg) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.add('on');
  clearTimeout(toastT);
  toastT = setTimeout(() => t.classList.remove('on'), 2600);
}
const debounce = (fn, ms = 300) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
const safe = fn => async (...a) => { try { await fn(...a); } catch (e) { toast(e.message); } };

function emptyHtml(title, text, page, btn) {
  return `<div class="card empty">${IC.tree}<h2>${esc(title)}</h2><p>${esc(text)}</p>
    ${page ? `<button class="btn primary" data-act="go" data-page="${page}">${esc(btn || 'Go to Scan')}</button>` : ''}</div>`;
}
function sizeBar(frac, label) {
  return `<div class="sizebar"><span class="num">${label}</span><div class="meter thin"><span style="width:${Math.max(0, Math.min(100, frac * 100))}%"></span></div></div>`;
}
function catGroup(cat) {
  if (['System', 'Program', 'Games'].includes(cat)) return 's1';
  if (['App data', 'Cache/Temp', 'Developer'].includes(cat)) return 's2';
  if (['User data', 'Cloud sync', 'Backup', 'Media'].includes(cat)) return 's3';
  return 'other';
}
const GROUP_LABEL = { s1: 'System & programs', s2: 'App data, caches & dev', s3: 'Your files, cloud & backups', other: 'Other / not identified' };
function catChips(n) {
  if (!n.cat || n.inherited) return '';
  return `<span class="chip">${esc(n.cat)}</span> ${n.advice ? `<span class="chip">${esc(n.advice)}</span>` : ''}`;
}
function descLine(n) {
  if (!n.title) return '';
  return n.inherited ? `inside ${esc(n.title)}` : `${esc(n.title)} — ${esc(n.desc)}`;
}

// ---------------------------------------------------------------- theme / nav
const THEMES = ['system', 'light', 'dark'];
function applyTheme(t) {
  if (t === 'system') document.documentElement.removeAttribute('data-theme');
  else document.documentElement.setAttribute('data-theme', t);
  $('#theme').textContent = 'Theme: ' + t[0].toUpperCase() + t.slice(1);
  try { localStorage.setItem('theme', t); } catch (e) { /* storage unavailable */ }
}
$('#theme').addEventListener('click', () => {
  const cur = document.documentElement.getAttribute('data-theme') || 'system';
  applyTheme(THEMES[(THEMES.indexOf(cur) + 1) % 3]);
  if (st.page === 'storage') renderStorage();
});

const TITLES = { scan: 'Scan', storage: 'Storage', levels: 'Storage by level', dups: 'Same-name files', identical: 'Identical files', pairs: 'Backup roots', export: 'Export' };
const RENDER = {};
function go(page) {
  st.page = page;
  $$('#nav a').forEach(a => a.classList.toggle('active', a.dataset.page === page));
  $$('.page').forEach(p => p.classList.toggle('active', p.id === 'page-' + page));
  $('#title').textContent = TITLES[page];
  $('main').scrollTop = 0;
  safe(RENDER[page])();
}
$$('#nav a').forEach(a => a.addEventListener('click', () => go(a.dataset.page)));

function updateNav() {
  const d = st.status?.dups;
  for (const [page, n] of [['dups', d?.groups], ['identical', st.status?.identical?.openGroups], ['pairs', d?.pairs]]) {
    const a = $(`#nav a[data-page="${page}"]`);
    let c = $('.count', a);
    if (!c) { c = document.createElement('span'); c.className = 'count'; a.appendChild(c); }
    c.textContent = n ? fmt(n) : '';
  }
}

// ---------------------------------------------------------------- polling
// One poll loop only: kick() polls now, or - if a request is already in
// flight - asks that loop to poll again as soon as it finishes.
function kick() {
  clearTimeout(st.pollTimer);
  if (st.polling) { st.pollAgain = true; return; }
  poll();
}
async function poll() {
  st.polling = true;
  try {
    const s = await api('/api/status');
    st.status = s;
    const busy = s.job.busy;
    const pill = $('#jobpill');
    pill.classList.toggle('hidden', !busy);
    if (busy) {
      const p = s.progress, ip = s.idProgress;
      $('#jobmsg').textContent = p ? `Scanning · ${fmt(p.files)} files · ${hsize(p.bytes)}`
        : ip ? `${ip.stage} · ${fmt(ip.doneFiles)} / ${fmt(ip.totalFiles)} files` : s.job.msg;
    }
    const idStamp = s.identical ? `${s.identical.finished}:${s.identical.moved}` : null;
    if (idStamp !== st.idStamp) {
      st.idStamp = idStamp;
      st.iq.offset = 0;
      if (st.page === 'identical') safe(RENDER.identical)();
    }
    if (st.page === 'identical' && busy && s.idProgress) renderIdProgress(s);
    const scanStamp = s.scan ? `${s.scan.finished}:${s.scan.files}` : null;
    if (scanStamp !== st.scanStamp) {
      const first = st.scanStamp === undefined;
      st.scanStamp = scanStamp;
      st.folder = null; st.level = null; st.levelRoot = 'all'; st.extStats = null;
      const hash = location.hash.slice(1);
      if (s.scan && (st.wantStorage || first)) {
        st.wantStorage = false;
        go(TITLES[hash] && hash !== 'scan' ? hash : 'storage');
        history.replaceState(null, '', location.pathname);   // hash is used once
      }
      else if (!first) safe(RENDER[st.page])();
    }
    const d = s.dups;
    const dupStamp = d ? `${d.groups}:${d.files}:${d.excluded}` : null;
    if (dupStamp !== st.dupStamp) {
      st.dupStamp = dupStamp;
      st.gq.offset = 0;
      if (['dups', 'pairs'].includes(st.page)) safe(RENDER[st.page])();
    }
    updateNav();
    if (st.page === 'scan' && busy) renderScanProgress(s);
    if (st.wasBusy && !busy) {
      if (s.job.error) toast('Error: ' + s.job.error);
      if (s.job.msg && ['quarantine', 'undo'].includes(s.job.kind)) toast(s.job.msg);
      if (['scan', 'export', 'dups', 'identical'].includes(st.page)) safe(RENDER[st.page])();
    }
    st.wasBusy = busy;
  } catch (e) {
    $('#jobpill').classList.remove('hidden');
    $('#jobmsg').textContent = 'Lost connection to the app — restart it';
  }
  st.polling = false;
  clearTimeout(st.pollTimer);
  if (st.pollAgain) { st.pollAgain = false; poll(); return; }
  st.pollTimer = setTimeout(poll, st.status?.job?.busy ? 700 : 2500);
}

// ---------------------------------------------------------------- actions
document.addEventListener('click', e => {
  const a = e.target.closest('[data-act]');
  if (a) { e.preventDefault(); e.stopPropagation(); safe(doAct)(a.dataset); return; }
  const r = e.target.closest('[data-row]');
  if (r) safe(doRow)(r.dataset);
});
async function doAct(d) {
  switch (d.act) {
    case 'go': go(d.page); break;
    case 'open-folder': await api('/api/open', { kind: 'folder', id: +d.id }); break;
    case 'open-file': await api('/api/open', { kind: 'file', id: +d.id }); break;
    case 'open-root': await api('/api/open', { kind: 'root', path: d.path }); break;
    case 'open-out': await api('/api/open', { kind: 'out' }); break;
    case 'copy': await navigator.clipboard.writeText(d.path); toast('Path copied'); break;
    case 'dupes': dupesUnder(d.path); break;
    case 'goto-folder': closeDrawer(); st.folder = d.id === '' ? null : +d.id; go('storage'); break;
    case 'sort': st[d.q].sort = d.sort; st[d.q].offset = 0; GROUP_TABLES[d.q](); break;
    case 'page': st[d.q].offset = Math.max(0, st[d.q].offset + (+d.dir) * st[d.q].limit); GROUP_TABLES[d.q](); break;
    case 'export': await runExport(d.keys.split(',')); break;
    case 'q-bulk': await quarantineBulk(); break;
    case 'q-group': await quarantineGroup(+d.gid); break;
    case 'undo': await undoRun(d.run); break;
  }
}
async function doRow(d) {
  if (d.row === 'folder') { st.folder = d.id === '' ? null : +d.id; hideTip(); go('storage'); }
  else if (d.row === 'group') openGroup(+d.id);
  else if (d.row === 'idgroup') openIdGroup(+d.id);
  else if (d.row === 'level') { st.level = +d.id; renderLevels(); }
  else if (d.row === 'dlevel') {
    st.gq.minLevels = st.gq.minLevels === +d.id ? 0 : +d.id; st.gq.offset = 0;
    const inp = $('#f-lvl'); if (inp) inp.value = st.gq.minLevels;
    markDupLevel(); renderGroups();
  }
}
function dupesUnder(path) {
  st.gq = { ...GQ_DEFAULT, path: path.endsWith('\\') ? path : path + '\\' };
  closeDrawer();
  go('dups');
}

// ---------------------------------------------------------------- shared UI pieces
// Paged, sortable group tables: qk = 'gq' (same-name) or 'iq' (identical).
const GROUP_TABLES = { gq: () => renderGroups(), iq: () => renderIdGroups() };
const sortTh = (qk, key, label, cls = '') => {
  const on = st[qk].sort === key;
  return `<th class="sortable ${cls} ${on ? 'sorted' : ''}" data-act="sort" data-q="${qk}" data-sort="${key}">${label}${on ? ' ↓' : ''}</th>`;
};
function pagerHtml(qk, r) {
  const q = st[qk], end = Math.min(r.total, q.offset + r.rows.length);
  return `<div class="pager">${r.total ? `${fmt(q.offset + 1)}–${fmt(end)} of ${fmt(r.total)}` : ''}
      <button class="btn sm" data-act="page" data-q="${qk}" data-dir="-1" ${q.offset ? '' : 'disabled'}>Previous</button>
      <button class="btn sm" data-act="page" data-q="${qk}" data-dir="1" ${end < r.total ? '' : 'disabled'}>Next</button></div>`;
}
// Filter bar: segmented buttons + name/path boxes that write into st[qk].
const DRIVE_SEG = [['all', 'All drives'], ['multi', '2+ drives'], ['single', 'One drive']];
const segHtml = (qk, key, vals) => `<div class="seg">${vals.map(([v, t]) =>
  `<button data-seg="${key}" data-q="${qk}" data-v="${v}" class="${st[qk][key] === v ? 'on' : ''}">${t}</button>`).join('')}</div>`;
const nameFilterHtml = (p, q) => `<input class="input" id="${p}-name" placeholder="Name contains…" value="${esc(q.name)}" style="width:190px">
        <input class="input mono" id="${p}-path" placeholder="Path contains…" value="${esc(q.path)}" style="flex:1;min-width:220px">`;
function wireFilters(el, qk, inputs) {   // inputs: {field: '#selector'}
  const q = st[qk], rerender = GROUP_TABLES[qk];
  const upd = debounce(() => { Object.entries(inputs).forEach(([k, sel]) => { q[k] = $(sel).value; }); q.offset = 0; rerender(); });
  Object.values(inputs).forEach(sel => { $(sel).oninput = upd; });
  $$(`[data-seg][data-q="${qk}"]`, el).forEach(b => b.onclick = () => {
    q[b.dataset.seg] = b.dataset.v; q.offset = 0;
    $$(`[data-seg="${b.dataset.seg}"][data-q="${qk}"]`, el).forEach(x => x.classList.toggle('on', x === b));
    rerender();
  });
}
const statHtml = (k, v, d, hero = false) => `<div class="stat ${hero ? 'hero' : ''}"><div class="k">${k}</div><div class="v">${v}</div><div class="d">${d}</div></div>`;
const wireBarTips = sel => $$(sel).forEach(b => { b.onmousemove = e => showTip(b.dataset.tip, e); b.onmouseleave = hideTip; });
const cancelJob = safe(async ev => { ev?.stopPropagation(); await api('/api/cancel', {}); toast('Cancelling…'); });
// Folder rows shared by Storage and By level (only the name cell differs).
const FOLDER_THEAD = first => `<thead><tr><th>${first}</th><th class="w-size">Size</th><th class="num w-share opt2">Share</th><th class="num w-files opt">Files</th>
          <th class="num w-date opt">Newest change</th><th class="w-acts"></th></tr></thead>`;
const folderRowHtml = (c, nameCell) => `<tr class="click" data-row="folder" data-id="${c.id}">
      <td><div class="name">${IC.folder}<div style="min-width:0">${nameCell}</div></div></td>
      <td>${sizeBar(c.pct, hsize(c.size))}</td><td class="num opt2">${pct(c.pct)}</td>
      <td class="num opt">${fmt(c.files)}</td><td class="num opt">${fdate(c.latest)}</td>
      <td class="acts"><button class="ibtn" title="Open in Explorer" data-act="open-folder" data-id="${c.id}">${IC.open}</button>
        <button class="ibtn" title="Same-name files under this folder" data-act="dupes" data-path="${esc(c.path)}">${IC.dup}</button></td></tr>`;

// ================================================================= EXTENSION PICKER
// Shared by Scan ('s') + Same-name ('d')  -> st.ext.dups, and Identical ('i') -> st.ext.id
const EXT_PRESETS = {
  Photos: 'jpg jpeg png gif webp heic heif bmp tif tiff raw cr2 nef arw dng orf mrw pef sr2 svg apng',
  Videos: 'mp4 mov avi mkv wmv m4v 3gp 3g2 mts m2ts webm flv f4v mpg mpeg vob ogv rmvb hevc mxf dav tod mod avchd',
  Audio: 'mp3 wav flac aac m4a ogg oga opus wma aiff au m4b m4p spx',
  Documents: 'pdf doc docx xls xlsx xlsm ppt pptx txt csv odt ods odp rtf md',
  Archives: 'zip rar 7z tar gz tgz bz2 xz iso',
};
const EXT_KEY = { s: 'dups', d: 'dups', i: 'id' };
const extLabel = e => e === '' ? '(no extension)' : '.' + e;
const normExt = e => { e = String(e).trim().toLowerCase().replace(/^\.+/, ''); return e === '(none)' ? '' : e; };

function extParams(key) {
  const s = st.ext[key];
  if (s.mode === 'only' && s.list.length) return { includeExt: s.list };
  if (s.mode === 'except' && s.list.length) return { excludeExt: s.list };
  return {};
}
function extSummary(key) {
  const s = st.ext[key];
  if (s.mode === 'all' || !s.list.length) return 'all file types';
  const shown = s.list.slice(0, 6).map(extLabel).join(' ');
  return (s.mode === 'only' ? 'only ' : 'all except ') + shown + (s.list.length > 6 ? ` +${s.list.length - 6}` : '');
}
async function renderExtPicker(p) {
  const box = $('#ext-' + p);
  if (!box) return;
  const key = EXT_KEY[p];
  if (st.extStats === null && st.status?.scan) {
    st.extStats = [];
    try { st.extStats = (await api('/api/extensions')).exts; } catch (e) { /* picker works without stats */ }
  }
  const s = st.ext[key];
  const on = new Set(s.list);
  const modes = [['all', 'All types'], ['only', 'Only these'], ['except', 'All except these']];
  box.innerHTML = `<div class="row" style="gap:10px"><span class="field">File types</span>
      <div class="seg">${modes.map(([v, t]) => `<button data-xmode="${v}" class="${s.mode === v ? 'on' : ''}">${t}</button>`).join('')}</div>
      <span class="muted">${s.mode === 'all' ? 'Every extension is included.' : s.list.length ? `${plural(s.list.length, 'type')} ${s.mode === 'only' ? 'included' : 'left out'}` : 'Pick types below.'}</span></div>
    ${s.mode === 'all' ? '' : `
    <div class="row" style="margin-top:10px;gap:6px">${Object.keys(EXT_PRESETS).map(k => `<button class="btn sm" data-xpreset="${k}">+ ${k}</button>`).join('')}
      <input class="input mono" data-xadd placeholder="add: jpg, png" style="width:170px">
      ${s.list.length ? '<button class="btn ghost sm" data-xclear>Clear</button>' : ''}</div>
    ${s.list.length ? `<div class="row" style="margin-top:8px;gap:6px">${s.list.map(e => `<span class="chip accent">${esc(extLabel(e))}<span class="x" data-xdel="${esc(e)}">✕</span></span>`).join('')}</div>` : ''}
    ${st.extStats?.length ? `<div class="sub" style="margin:12px 0 6px">Types in this scan, biggest first. Click to ${s.mode === 'only' ? 'include' : 'leave out'}:</div>
      <div class="row" style="gap:6px">${st.extStats.slice(0, 40).map(x => `<button class="chip pick ${on.has(x.ext) ? 'accent' : ''}" data-xtoggle="${esc(x.ext)}" title="${fmt(x.files)} files · ${hsize(x.bytes)}">${esc(extLabel(x.ext))} <span class="muted">${hsize(x.bytes)}</span></button>`).join('')}</div>` : ''}`}`;
  const set = list => { s.list = [...new Set(list.map(normExt))]; renderExtPicker(p); };
  $$('[data-xmode]', box).forEach(b => b.onclick = () => { s.mode = b.dataset.xmode; renderExtPicker(p); });
  $$('[data-xpreset]', box).forEach(b => b.onclick = () => set([...s.list, ...EXT_PRESETS[b.dataset.xpreset].split(' ')]));
  $$('[data-xdel]', box).forEach(b => b.onclick = () => set(s.list.filter(e => e !== b.dataset.xdel)));
  $$('[data-xtoggle]', box).forEach(b => b.onclick = () => {
    const e = b.dataset.xtoggle;
    set(on.has(e) ? s.list.filter(x => x !== e) : [...s.list, e]);
  });
  const clr = $('[data-xclear]', box);
  if (clr) clr.onclick = () => set([]);
  const add = $('[data-xadd]', box);
  if (add) add.onkeydown = e => {
    if (e.key === 'Enter' && add.value.trim()) set([...s.list, ...add.value.split(/[\s,;]+/).filter(Boolean)]);
  };
}

// ================================================================= SCAN
function driveCard(d) {
  const used = d.total ? d.used / d.total : 0;
  return `<div class="drive ${st.sel.has(d.root) ? 'on' : ''}" data-drive="${esc(d.root)}">
    <div class="check">✓</div>
    <div class="row" style="gap:8px"><span class="letter">${esc(d.root.slice(0, 2))}</span>
      <span class="lbl ellipsis">${esc(d.label || 'Local disk')}</span></div>
    <div class="row" style="margin:4px 0 10px"><span class="chip">${esc(d.type)}</span>
      ${d.root === st.init.systemDrive ? '<span class="chip">Windows</span>' : ''}</div>
    <div class="meter"><span style="width:${used * 100}%"></span></div>
    <div class="row between" style="margin-top:6px;font-size:12.5px">
      <span class="ink2">${hsize(d.used)} used of ${hsize(d.total)}</span><span class="muted">${hsize(d.free)} free</span></div>
  </div>`;
}
function dupOptsHtml(p) {
  const o = st.dupOpts;
  return `<div class="row" style="gap:22px">
    <label class="field">Ignore files under <input class="input" id="${p}-minkb" type="number" min="0" value="${o.minKb}" style="width:80px"> KB</label>
    <label class="switch"><input type="checkbox" id="${p}-dev" ${o.skipDev ? 'checked' : ''}><i></i>Skip developer folders (.git, node_modules, venv…)</label>
    <label class="switch"><input type="checkbox" id="${p}-sys" ${o.skipSystem ? 'checked' : ''}><i></i>Skip Windows, Program Files, ProgramData, AppData</label>
  </div><div class="extpick" id="ext-${p}"></div>`;
}
function readDupOpts(p) {
  if ($(`#${p}-minkb`)) {
    st.dupOpts = { minKb: +($(`#${p}-minkb`).value || 0), skipDev: !!$(`#${p}-dev`)?.checked, skipSystem: !!$(`#${p}-sys`)?.checked };
  }
  return { ...st.dupOpts, ...extParams('dups') };
}
function scanTargets() {
  const t = [...st.sel].sort();
  if (st.user) t.push('user folders');
  if (st.extra.length) t.push(plural(st.extra.length, 'extra folder'));
  return t;
}
function runHtml() {
  const t = scanTargets();
  const cache = st.init.cache;
  return `<div class="row between"><div><h2>Run</h2>
      <p class="sub" style="margin:0">${t.length ? 'Will scan: ' + esc(t.join(', ')) : 'Nothing selected yet.'}
      ${[...st.sel].includes(st.init.systemDrive) ? ' · The Windows drive can take several minutes.' : ''}</p></div>
    <div class="row">${cache ? `<button class="btn" id="loadlast">Load last scan <span class="muted">(${fdate(cache)})</span></button>` : ''}
      <button class="btn primary" id="startscan" ${t.length ? '' : 'disabled'}>${IC.scan} Start scan</button></div></div>`;
}
function renderScanProgress(s) {
  const box = $('#scanrun');
  if (!box) return;
  const p = s.progress;
  const scanning = s.job.kind === 'scan' && p;
  box.innerHTML = `<div class="row between"><h2>${scanning ? 'Scanning…' : esc(s.job.msg || 'Working…')}</h2>
      ${scanning ? '<button class="btn" id="cancelscan">Cancel</button>' : ''}</div>
    ${scanning ? `<div class="progress-big">
      <div><div class="muted">Files</div><div class="v">${fmt(p.files)}</div></div>
      <div><div class="muted">Folders</div><div class="v">${fmt(p.dirs)}</div></div>
      <div><div class="muted">Size found</div><div class="v">${hsize(p.bytes)}</div></div></div>
      <div class="mono muted ellipsis" style="margin-bottom:10px">${esc(p.current)}</div>` : ''}
    <div class="indet"></div>`;
  const c = $('#cancelscan');
  if (c) c.onclick = cancelJob;
}
function lastScanHtml(S) {
  return `<div class="card"><div class="card-head"><div><h2>Current scan</h2>
    <p class="sub">${fmt(S.files)} files · ${fmt(S.folders)} folders · ${hsize(S.size)} · took ${fmt(S.seconds)}s
    ${S.cancelled ? ' · <b>cancelled (partial)</b>' : ''}</p></div>
    <button class="btn" data-act="go" data-page="storage">${IC.tree} View storage</button></div>
    <div class="tbl-wrap"><table class="tbl"><thead><tr><th>Location</th><th class="num">Files</th><th class="num">Scanned</th>
    <th class="num">Drive used</th></tr></thead><tbody>
    ${S.roots.map(r => `<tr class="click" data-row="folder" data-id="${r.id}"><td><div class="name">${IC.folder}<span class="t">${esc(r.path)}</span>
      ${r.drive?.label ? `<span class="muted">${esc(r.drive.label)}</span>` : ''}</div></td>
      <td class="num">${fmt(r.files)}</td><td class="num">${hsize(r.size)}</td>
      <td class="num">${r.drive ? hsize(r.drive.used) : '—'}</td></tr>`).join('')}
    </tbody></table></div>
    <p class="sub" style="margin:10px 0 0">${fmt(S.errors)} folders could not be read (usually protected system folders) · ${fmt(S.links)} links/junctions skipped.</p></div>`;
}
RENDER.scan = function renderScan() {
  const el = $('#page-scan');
  const I = st.init;
  if (!I) return;
  const s = st.status || { job: {} };
  el.innerHTML = `
  <div class="card"><div class="card-head"><div><h2>Drives</h2><p class="sub">Click drives to include them. “Whole PC” selects every drive.</p></div>
    <div class="row"><button class="btn sm" id="wholepc">${IC.drive} Whole PC</button><button class="btn ghost sm" id="clearsel">Clear</button></div></div>
    <div class="drives">${I.drives.map(driveCard).join('')}</div></div>
  <div class="card"><h2>Folders</h2><p class="sub">Folders on a selected drive are already covered by that drive.</p>
    <label class="switch"><input type="checkbox" id="userf" ${st.user ? 'checked' : ''}><i></i>Include my user folders</label>
    <div class="folder-list">${I.userFolders.map(esc).join('<br>')}</div>
    <div class="section-gap"></div><h3>Extra folders</h3>
    <div class="row">${st.extra.map((p, i) => `<span class="chip mono">${esc(p)} <span class="x" data-x="${i}">✕</span></span>`).join('') || '<span class="muted">None added</span>'}</div>
    <div class="row" style="margin-top:10px"><input class="input mono" id="extraIn" placeholder="Paste a folder path, e.g. E:\\Photos" style="flex:1;max-width:520px">
      <button class="btn" id="extraAdd">Add</button><button class="btn" id="extraPick">Browse…</button></div></div>
  <div class="card"><h2>Same-name matching</h2><p class="sub">Runs automatically after the scan. You can re-run it later with different settings.</p>${dupOptsHtml('s')}</div>
  <div class="card" id="scanrun">${s.job.busy ? '' : runHtml()}</div>
  ${s.scan ? lastScanHtml(s.scan) : ''}`;

  $$('.drive', el).forEach(c => c.addEventListener('click', () => {
    const r = c.dataset.drive;
    st.sel.has(r) ? st.sel.delete(r) : st.sel.add(r);
    RENDER.scan();
  }));
  $('#wholepc').onclick = () => { I.drives.forEach(d => st.sel.add(d.root)); RENDER.scan(); };
  $('#clearsel').onclick = () => { st.sel.clear(); RENDER.scan(); };
  $('#userf').onchange = e => { st.user = e.target.checked; RENDER.scan(); };
  $$('[data-x]', el).forEach(x => x.onclick = () => { st.extra.splice(+x.dataset.x, 1); RENDER.scan(); });
  const addExtra = p => { p = (p || '').trim().replace(/^"|"$/g, ''); if (p && !st.extra.includes(p)) st.extra.push(p); RENDER.scan(); };
  $('#extraAdd').onclick = () => addExtra($('#extraIn').value);
  $('#extraIn').onkeydown = e => { if (e.key === 'Enter') addExtra(e.target.value); };
  $('#extraPick').onclick = safe(async () => { toast('Folder picker opened (it may be behind this window)'); const r = await api('/api/pick', {}); if (r.path) addExtra(r.path); });
  $$('#s-minkb, #s-dev, #s-sys', el).forEach(i => i.onchange = () => readDupOpts('s'));
  const start = $('#startscan');
  if (start) start.onclick = safe(async () => {
    await api('/api/scan', { drives: [...st.sel], userFolders: st.user, folders: st.extra, dupOptions: readDupOpts('s') });
    st.wantStorage = true; toast('Scan started'); kick();
  });
  const load = $('#loadlast');
  if (load) load.onclick = safe(async () => { await api('/api/load', { dupOptions: readDupOpts('s') }); st.wantStorage = true; kick(); });
  renderExtPicker('s');
  if (s.job.busy) renderScanProgress(s);
};

// ================================================================= STORAGE
function squarify(items, x, y, w, h) {
  const out = [];
  const total = items.reduce((s, i) => s + i.value, 0);
  if (!total || w <= 0 || h <= 0) return out;
  const scale = (w * h) / total;
  let rest = items.map(i => ({ ...i, area: i.value * scale }));
  const worst = (row, s) => {
    const sum = row.reduce((a, r) => a + r.area, 0);
    let mx = 0, mn = Infinity;
    for (const r of row) { mx = Math.max(mx, r.area); mn = Math.min(mn, r.area); }
    return Math.max((s * s * mx) / (sum * sum), (sum * sum) / (s * s * mn));
  };
  while (rest.length) {
    const short = Math.min(w, h);
    let row = [rest[0]], i = 1, wr = worst(row, short);
    while (i < rest.length) {
      const nr = row.concat(rest[i]);
      const nw = worst(nr, short);
      if (nw > wr) break;
      row = nr; wr = nw; i++;
    }
    const ra = row.reduce((a, r) => a + r.area, 0);
    if (w >= h) {
      const cw = ra / h; let cy = y;
      for (const r of row) { const rh = r.area / cw; out.push({ ...r, x, y: cy, w: cw, h: rh }); cy += rh; }
      x += cw; w -= cw;
    } else {
      const rh = ra / w; let cx = x;
      for (const r of row) { const rw = r.area / rh; out.push({ ...r, x: cx, y, w: rw, h: rh }); cx += rw; }
      y += rh; h -= rh;
    }
    rest = rest.slice(i);
  }
  return out;
}

function drawTreemap(svg, data) {
  const W = svg.clientWidth || 800, H = svg.clientHeight || 380;
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  const parent = data.node.size || 1;
  const kids = data.children.filter(c => c.size > 0);
  const top = kids.slice(0, 60);
  const items = top.map(c => ({ value: c.size, label: c.name, id: c.id, grp: catGroup(c.cat), n: c, kind: 'dir' }));
  const restSize = kids.slice(60).reduce((s, c) => s + c.size, 0) + (data.more?.size || 0);
  const restCount = kids.length - top.length + (data.more?.count || 0);
  if (restSize > 0) items.push({ value: restSize, label: `${fmt(restCount)} smaller folders`, grp: 'other', kind: 'more' });
  if (data.own?.size > 0) items.push({ value: data.own.size, label: `${fmt(data.own.files)} files here`, grp: 'other', kind: 'files' });
  items.sort((a, b) => b.value - a.value);
  const rects = squarify(items, 0, 0, W, H);
  svg.innerHTML = rects.map((r, i) => {
    const fill = r.grp === 'other' ? 'var(--s-other)' : `var(--${r.grp})`;
    const ink = r.grp === 'other' ? 'var(--s-other-ink)' : '#ffffff';
    const rw = Math.max(0, r.w), rh = Math.max(0, r.h);
    const chars = Math.floor((rw - 12) / 7);
    let label = '';
    if (rw > 46 && rh > 20 && chars > 2) {
      const name = r.label.length > chars ? r.label.slice(0, chars - 1) + '…' : r.label;
      label = `<text x="${r.x + 8}" y="${r.y + 18}" style="fill:${ink};font-weight:600">${esc(name)}</text>`;
      if (rh > 38) label += `<text x="${r.x + 8}" y="${r.y + 34}" style="fill:${ink};opacity:.85">${hsize(r.value)}</text>`;
    }
    const nav = r.kind === 'dir' ? `data-row="folder" data-id="${r.id}"` : '';
    return `<g><rect x="${r.x}" y="${r.y}" width="${rw}" height="${rh}" rx="4" style="fill:${fill}" data-i="${i}" ${nav}></rect>${label}</g>`;
  }).join('');
  svg.onmousemove = e => {
    const i = e.target.dataset?.i;
    if (i === undefined) return hideTip();
    const r = rects[+i];
    const n = r.n;
    showTip(`<b>${esc(r.label)}</b>${hsize(r.value)} · ${pct(r.value / parent)} of this folder
      ${n ? `<br>${fmt(n.files)} files · newest ${fdate(n.latest)}` : ''}
      ${n?.title ? `<br><span class="muted">${descLine(n)}</span>` : ''}
      ${r.kind === 'dir' ? '<br><span class="muted">Click to open</span>' : ''}`, e);
  };
  svg.onmouseleave = hideTip;
}

async function renderStorage() {
  const el = $('#page-storage');
  const S = st.status?.scan;
  if (!S) { el.innerHTML = emptyHtml('No scan yet', 'Scan your drives to see where the space goes.', 'scan'); return; }
  if (st.folder === null && S.roots.length === 1) st.folder = S.roots[0].id;
  const seq = ++st.renderSeq;
  let data;
  if (st.folder === null) {
    data = { all: true, node: { name: 'All scanned locations', size: S.size, files: S.files, dirs: S.folders, path: '' },
      chain: [], children: S.roots.map(r => ({ ...r, name: r.path, pct: r.size / (S.size || 1) })),
      own: { files: 0, size: 0 }, topFiles: [], drive: null };
  } else {
    data = await api('/api/folder?id=' + st.folder);
  }
  if (seq !== st.renderSeq) return;
  const n = data.node;
  const multi = S.roots.length > 1;
  const crumbs = [];
  if (multi) crumbs.push({ id: '', name: 'All locations' });
  data.chain.forEach(c => crumbs.push(c));
  const crumbHtml = crumbs.map((c, i) => i === crumbs.length - 1 && !data.all
    ? `<a class="cur">${esc(c.name)}</a>`
    : `<a data-act="goto-folder" data-id="${c.id}" class="${data.all ? 'cur' : ''}">${esc(c.name)}</a>`).join('<span class="sep">›</span>');

  const rows = data.children.map(c => folderRowHtml(c,
    `<div class="row" style="gap:6px;flex-wrap:nowrap"><span class="t ellipsis">${esc(c.name)}</span>${catChips(c)}</div>
        ${c.title ? `<div class="desc ellipsis">${descLine(c)}</div>` : ''}`)).join('');
  const extraRows = (data.own.files ? `<tr><td><div class="name">${IC.files}<span class="ink2 ellipsis">${plural(data.own.files, 'file')} directly in this folder</span></div></td>
      <td>${sizeBar(data.own.size / (n.size || 1), hsize(data.own.size))}</td><td class="num opt2">${pct(data.own.size / (n.size || 1))}</td><td class="num opt">${fmt(data.own.files)}</td><td class="opt"></td><td></td></tr>` : '')
    + (data.more ? `<tr><td colspan="6" class="muted">+ ${plural(data.more.count, 'smaller folder')} (${hsize(data.more.size)}) not listed</td></tr>` : '');

  const legend = Object.entries(GROUP_LABEL).map(([k, v]) =>
    `<span><i class="dot" style="background:var(--${k === 'other' ? 's-other' : k})"></i>${v}</span>`).join('');

  let about;
  if (data.all) {
    about = `<div class="card"><h2>All scanned locations</h2><p class="sub">${plural(S.roots.length, 'location')} in this scan. Click one to drill in.</p></div>`;
  } else {
    about = `<div class="card"><h3>What is this folder?</h3>
      ${n.title ? `<h2>${n.inherited ? 'Inside: ' : ''}${esc(n.title)}</h2><p class="desc-block">${esc(n.desc)}</p>
        ${n.cat ? `<div class="row"><span class="chip">${esc(n.cat)}</span>${n.advice ? `<span class="chip accent">${esc(n.advice)}</span>` : ''}</div>` : ''}`
        : '<p class="desc-block">Not a commonly known folder — likely your own data or an app-specific folder.</p>'}
      <div class="section-gap"></div>
      <dl class="kv"><dt>Size</dt><dd>${hsize(n.size)}</dd><dt>Files</dt><dd>${fmt(n.files)}</dd>
        <dt>Sub-folders</dt><dd>${fmt(n.dirs)}</dd><dt>Level</dt><dd>${n.level}</dd>
        <dt>Newest change</dt><dd>${fdate(n.latest)}</dd></dl>
      <div class="section-gap"></div><div class="path ink2">${esc(n.path)}</div>
      <div class="row" style="margin-top:12px">
        <button class="btn sm" data-act="open-folder" data-id="${n.id}">${IC.open} Open in Explorer</button>
        <button class="btn sm" data-act="dupes" data-path="${esc(n.path)}">${IC.dup} Same-name files here</button>
        <button class="btn ghost sm" data-act="copy" data-path="${esc(n.path)}">${IC.copy} Copy path</button></div></div>`;
  }
  let driveCardHtml = '';
  if (data.drive) {
    const D = data.drive;
    const scanned = n.size;
    const unseen = Math.max(0, D.used - scanned);
    const f = x => (x / (D.total || 1)) * 100;
    driveCardHtml = `<div class="card"><h3>Drive ${esc(D.root.slice(0, 2))} ${esc(D.label)}</h3>
      <div class="meter" style="height:12px"><span style="width:${f(Math.min(scanned, D.used))}%"></span><span class="o" style="width:${f(unseen)}%"></span></div>
      <div class="legend"><span><i class="dot" style="background:var(--accent)"></i>Scanned files ${hsize(Math.min(scanned, D.used))}</span>
        <span><i class="dot" style="background:var(--s-other)"></i>Used, not visible ${hsize(unseen)}</span>
        <span><i class="dot" style="background:var(--track)"></i>Free ${hsize(D.free)}</span></div>
      <p class="sub" style="margin:10px 0 0">Capacity ${hsize(D.total)}. “Not visible” is space the scan can’t see: protected system folders, restore points, page/hibernate files.</p></div>`;
  }
  const files = data.topFiles.length ? `<div class="card"><h3>Largest files directly here</h3><div class="tbl-wrap" style="max-height:420px">
      <table class="tbl"><tbody>${data.topFiles.slice(0, 40).map(f => `<tr>
        <td><div class="name">${IC.file}<div style="min-width:0"><div class="t ellipsis" title="${esc(f.name)}">${esc(f.name)}</div>
          <div class="desc">${fdate(f.modified)}${f.note ? ' · ' + esc(f.note) : ''}</div></div></div></td>
        <td class="num">${hsize(f.size)}</td>
        <td class="acts"><button class="ibtn" title="Show in Explorer" data-act="open-file" data-id="${f.id}">${IC.open}</button></td></tr>`).join('')}
      </tbody></table></div></div>` : '';

  el.innerHTML = `<div class="card" style="padding:10px 12px"><div class="crumbs">${crumbHtml}</div></div>
    <div class="section-gap"></div>
    <div class="grid2"><div class="stack">
      <div class="card"><div class="card-head"><div><h2>${esc(n.name)}</h2>
        <p class="sub">${hsize(n.size)} · ${plural(n.files, 'file')} · ${plural(n.dirs, 'folder')}</p></div></div>
        <svg class="treemap" id="tm" role="img" aria-label="Treemap of folder sizes"></svg>
        <div class="legend">${legend}</div></div>
      <div class="card cq"><div class="card-head"><div><h2>Contents</h2><p class="sub">Biggest first. Click a folder to open it.</p></div></div>
        <div class="tbl-wrap"><table class="tbl fixed">${FOLDER_THEAD('Name')}<tbody>${rows}${extraRows}
          ${!rows && !extraRows ? '<tr><td colspan="6" class="muted">Empty folder</td></tr>' : ''}</tbody></table></div></div>
    </div><div class="stack">${about}${driveCardHtml}${files}</div></div>`;
  drawTreemap($('#tm'), data);
}
RENDER.storage = renderStorage;
window.addEventListener('resize', debounce(() => { if (st.page === 'storage') renderStorage(); }, 250));

// ================================================================= LEVELS
async function renderLevels() {
  const el = $('#page-levels');
  const S = st.status?.scan;
  if (!S) { el.innerHTML = emptyHtml('No scan yet', 'Scan first to see storage at each folder level.', 'scan'); return; }
  const seq = ++st.renderSeq;
  const L = (await api('/api/levels?root=' + st.levelRoot)).levels;
  if (st.level === null || !L.find(l => l.level === st.level)) st.level = (L.find(l => l.level === 1) || L[0] || {}).level ?? 0;
  const rows = await api(`/api/level?level=${st.level}&root=${st.levelRoot}&limit=300`);
  if (seq !== st.renderSeq) return;
  const mx = Math.max(1, ...L.map(l => l.size));
  const bars = L.map(l => `<div class="bar-row ${l.level === st.level ? 'sel' : ''}" data-row="level" data-id="${l.level}"
      data-tip="${esc(`<b>Level ${l.level}</b>${fmt(l.folders)} folders · ${hsize(l.size)}<br>Largest folder ${hsize(l.largest)}`)}">
      <span class="lbl">Level ${l.level} <span class="muted">· ${plural(l.folders, 'folder')}</span></span>
      <div class="track"><div class="fill" style="width:${(l.size / mx) * 100}%"></div></div>
      <span class="val">${hsize(l.size)}</span></div>`).join('');
  el.innerHTML = `<div class="card"><div class="card-head"><div><h2>Size at each folder level</h2>
      <p class="sub">Levels count from the drive: level 0 is the drive itself (e.g. C:\\), level 1 the folders directly on it (C:\\Users), level 2 the next (C:\\Users\\Public), and so on. Each level's total is the size of all scanned folders at that depth. Click a level to list its biggest folders.</p></div>
      <select class="input" id="lvlroot"><option value="all">All scanned locations</option>
        ${S.roots.map(r => `<option value="${r.id}" ${String(r.id) === String(st.levelRoot) ? 'selected' : ''}>${esc(r.path)}</option>`).join('')}</select></div>
      <div class="bars" id="lvlbars">${bars}</div></div>
    <div class="card cq"><div class="card-head"><div><h2>Biggest folders at level ${st.level}</h2>
      <p class="sub">${plural(rows.total, 'folder')} at this level${rows.total > rows.rows.length ? ` · showing the biggest ${fmt(rows.rows.length)}` : ''}. Click to open in Storage.</p></div></div>
      <div class="tbl-wrap"><table class="tbl fixed">${FOLDER_THEAD('Folder')}
      <tbody>${rows.rows.map(c => folderRowHtml(c,
        `<div class="t path">${esc(c.path)}</div>${c.title ? `<div class="desc ellipsis">${descLine(c)}</div>` : ''}<div class="row" style="gap:6px;margin-top:3px">${catChips(c)}</div>`)).join('')}</tbody></table></div></div>`;
  $('#lvlroot').onchange = e => { st.levelRoot = e.target.value; st.level = null; renderLevels(); };
  wireBarTips('#lvlbars .bar-row');
}
RENDER.levels = renderLevels;

// ================================================================= DUPLICATES
const lvlLabel = n => n === 0 ? 'Name only' : n === 1 ? '+ same parent folder' : n === 2 ? '+ parent & grandparent' : `${n} folder levels match`;
function markDupLevel() {
  $$('#duplvls .bar-row').forEach(b => b.classList.toggle('sel', +b.dataset.id === st.gq.minLevels && st.gq.minLevels > 0));
}
async function renderDups() {
  const el = $('#page-dups');
  const s = st.status;
  if (!s?.scan) { el.innerHTML = emptyHtml('No scan yet', 'Scan first, then same-name files are matched automatically.', 'scan'); return; }
  const D = s.dups;
  const busy = s.job.busy && s.job.kind === 'dups';
  const opts = `<div class="card"><div class="row between"><div>${dupOptsHtml('d')}</div>
      <button class="btn primary" id="rerun" ${s.job.busy ? 'disabled' : ''}>${IC.scan} ${D ? 'Re-run matching' : 'Run matching'}</button></div></div>`;
  if (!D) {
    el.innerHTML = opts + `<div class="card empty">${busy ? '<div class="indet"></div><p>' + esc(s.job.msg) + '</p>' : '<p>Matching has not been run for this scan.</p>'}</div>`;
    wireDupOpts();
    return;
  }
  const mx = Math.max(1, ...D.levels.map(l => l.files));
  const lvls = D.levels.map(l => `<div class="bar-row" data-row="dlevel" data-id="${l.levels}"
      data-tip="${esc(`<b>${lvlLabel(l.levels)}</b>${fmt(l.files)} files · ${hsize(l.bytes)}<br>${fmt(l.all_same_size)} where every copy has the same size<br><span class='muted'>Click to show groups with at least this match</span>`)}">
      <span class="lbl">${lvlLabel(l.levels)}</span>
      <div class="track"><div class="fill" style="width:${(l.files / mx) * 100}%"></div></div>
      <span class="val">${fmt(l.files)} files · ${hsize(l.bytes)}</span></div>`).join('');
  const q = st.gq;
  el.innerHTML = opts + `<div class="section-gap"></div>
    <div class="stats">
      ${statHtml('Potentially reclaimable', hsize(D.reclaimable), 'extra copies with same name and size', true)}
      ${statHtml('Names found 2+ times', fmt(D.groups), `${fmt(D.files)} files involved`)}
      ${statHtml('Same name + same size', fmt(D.sameSize), `${fmt(D.groups - D.sameSize)} differ in size`)}
      ${statHtml('On 2+ drives', fmt(D.multiDrive), `${fmt(D.excluded + (D.extSkipped || 0))} files skipped by options`)}
    </div><div class="section-gap"></div>
    <div class="card"><h2>How much of the path matches</h2><p class="sub">For each copy: do its parent, grandparent… folders also match another copy? Deep matches mean whole folders were copied (backups).</p>
      <div class="bars" id="duplvls">${lvls}</div></div>
    <div class="card">
      <div class="row" style="margin-bottom:12px">
        ${nameFilterHtml('f', q)}
        ${segHtml('gq', 'match', [['all', 'All sizes'], ['same', 'Same size'], ['diff', 'Different size']])}
        ${segHtml('gq', 'drives', DRIVE_SEG)}
        <label class="field">Levels ≥ <input class="input" id="f-lvl" type="number" min="0" max="15" value="${q.minLevels}" style="width:62px"></label>
        <button class="btn ghost sm" id="f-clear">Reset</button>
      </div>
      <div id="groups"></div></div>`;
  wireDupOpts();
  wireBarTips('#duplvls .bar-row');
  markDupLevel();
  wireFilters(el, 'gq', { name: '#f-name', path: '#f-path' });
  $('#f-lvl').onchange = e => { q.minLevels = +e.target.value || 0; q.offset = 0; markDupLevel(); renderGroups(); };
  $('#f-clear').onclick = () => { st.gq = { ...GQ_DEFAULT }; renderDups(); };
  await renderGroups();
}
function wireDupOpts() {
  renderExtPicker('d');
  const b = $('#rerun');
  if (b) b.onclick = safe(async () => { await api('/api/dups', readDupOpts('d')); toast('Matching started'); kick(); });
}
async function renderGroups() {
  const box = $('#groups');
  if (!box) return;
  const q = st.gq;
  const qs = new URLSearchParams(Object.entries(q)).toString();
  const seq = ++st.renderSeq;
  const r = await api('/api/groups?' + qs);
  if (seq !== st.renderSeq) return;
  const th = (key, label, cls) => sortTh('gq', key, label, cls);
  box.innerHTML = `<div class="row between" style="margin-bottom:6px"><span class="ink2">${fmt(r.total)} groups match · ${hsize(r.reclaimable)} reclaimable</span></div>
    <div class="tbl-wrap"><table class="tbl"><thead><tr>${th('name', 'File name')}${th('copies', 'Copies', 'num')}<th>Drives</th><th>Sizes</th>
      ${th('total', 'Total', 'num')}${th('reclaimable', 'Reclaimable', 'num')}${th('levels', 'Path match')}${th('newest', 'Newest copy', 'num')}</tr></thead>
    <tbody>${r.rows.map(g => `<tr class="click" data-row="group" data-id="${g.gid}">
      <td><div class="name">${IC.file}<span class="t ellipsis" style="max-width:420px">${esc(g.name)}</span></div></td>
      <td class="num">${g.copies}</td><td>${esc(g.drives)}</td>
      <td>${g.sameSize ? '<span class="chip yes">same</span>' : `<span class="chip">${g.distinct} sizes</span>`}</td>
      <td class="num">${hsize(g.total)}</td><td class="num">${g.reclaimable ? hsize(g.reclaimable) : '—'}</td>
      <td><span class="chip">${esc(lvlLabel(g.maxLevels))}</span></td><td class="num">${fdate(g.newest)}</td></tr>`).join('')
      || '<tr><td colspan="8" class="muted">No groups match these filters.</td></tr>'}</tbody></table></div>
    ${pagerHtml('gq', r)}`;
}
RENDER.dups = renderDups;

// ---------------------------------------------------------------- drawer
function closeDrawer() { $('#drawer').classList.remove('on'); $('#scrim').classList.remove('on'); }
$('#dclose').onclick = closeDrawer;
$('#scrim').onclick = closeDrawer;
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeDrawer(); });
function openDrawer(title, sub, callout, body) {
  $('#dtitle').textContent = title;
  $('#dsub').textContent = sub;
  $('#dbody').innerHTML = `<p class="callout">${callout}</p><div class="section-gap"></div>${body}`;
  $('#drawer').classList.add('on');
  $('#scrim').classList.add('on');
  $('#dbody').scrollTop = 0;
}
const copyActsHtml = c => `<div class="acts"><button class="btn sm" data-act="open-file" data-id="${c.id}">${IC.open} Show in Explorer</button>
        <button class="btn sm" data-act="goto-folder" data-id="${c.folderId}">${IC.tree} Open in Storage</button>
        <button class="btn ghost sm" data-act="copy" data-path="${esc(c.path)}">${IC.copy} Copy path</button></div>`;
async function openGroup(gid) {
  const g = await api('/api/group?gid=' + gid);
  openDrawer(g.name,
    `${g.copies} copies · ${hsize(g.total)} total · ${g.sameSize ? 'all the same size' : g.distinct + ' different sizes'}`
      + (g.reclaimable ? ` · ${hsize(g.reclaimable)} reclaimable` : ''),
    'The <b>bold</b> part of each path is shared with another copy; the grey part (the “root”) is where they differ. Newest copy is outlined.',
    g.copiesList.map(c => `<div class="copy ${c.newest ? 'newest' : ''}">
      <div class="path"><span class="root">${esc(c.root)}</span><span class="rel">${esc(c.rel)}</span></div>
      <div class="badges">${c.newest ? '<span class="chip accent">Newest</span>' : ''}
        <span class="chip ${c.levels >= 1 ? 'yes' : 'no'}">Parent folder</span>
        <span class="chip ${c.levels >= 2 ? 'yes' : 'no'}">Grandparent folder</span>
        ${c.levels > 2 ? `<span class="chip">${c.levels} levels match</span>` : ''}
        ${c.sameRel > 1 ? `<span class="chip">${c.sameRel} copies share this path</span>` : ''}
        ${c.rootTitle ? `<span class="chip">root: ${esc(c.rootTitle)}</span>` : ''}</div>
      <div class="meta"><span><b>${hsize(c.size)}</b></span><span>Created ${fdate(c.created)}</span><span>Modified ${fdate(c.modified)}</span></div>
      ${copyActsHtml(c)}</div>`).join(''));
}

// ================================================================= IDENTICAL
const KEEP_RULES = [['newest', 'Newest modified'], ['oldest', 'Oldest modified'], ['shortest', 'Shortest path'],
  ['prefer', 'Path containing…'], ['avoid', 'Path NOT containing…']];

function idOptsHtml() {
  const o = st.idOpts;
  return `<div class="row" style="gap:22px">
    <label class="field">Ignore files under <input class="input" id="i-minkb" type="number" min="0" value="${o.minKb}" style="width:80px"> KB</label>
    <label class="switch"><input type="checkbox" id="i-dev" ${o.skipDev ? 'checked' : ''}><i></i>Skip developer folders</label>
    <label class="switch"><input type="checkbox" id="i-sys" ${o.skipSystem ? 'checked' : ''}><i></i>Skip Windows / Program Files / AppData</label>
    <label class="switch"><input type="checkbox" id="i-verify" ${o.verify ? 'checked' : ''}><i></i>Also compare byte-by-byte (slower, reads matches twice)</label>
  </div>
  <div class="row" style="margin-top:12px"><span class="field">Only under folder</span>
    <input class="input mono" id="i-path" value="${esc(o.path)}" placeholder="optional, e.g. D:\\ or Q:\\Photos" style="flex:1;max-width:520px"></div>
  <div class="extpick" id="ext-i"></div>`;
}
function readIdOpts() {
  st.idOpts = { minKb: +($('#i-minkb')?.value || 0), skipDev: !!$('#i-dev')?.checked,
    skipSystem: !!$('#i-sys')?.checked, verify: !!$('#i-verify')?.checked, path: ($('#i-path')?.value || '').trim() };
  return { ...st.idOpts, ...extParams('id') };
}
function renderIdProgress(s) {
  const box = $('#idrun');
  if (!box) return;
  const p = s.idProgress;
  const frac = p.totalBytes ? p.doneBytes / p.totalBytes : (p.totalFiles ? p.doneFiles / p.totalFiles : 0);
  box.innerHTML = `<div class="row between"><h2>${esc(p.stage || s.job.msg)}</h2><button class="btn" id="idcancel">Cancel</button></div>
    <div class="progress-big"><div><div class="muted">Files</div><div class="v">${fmt(p.doneFiles)} <span class="muted" style="font-size:15px">/ ${fmt(p.totalFiles)}</span></div></div>
      <div><div class="muted">Read</div><div class="v">${hsize(p.doneBytes)} <span class="muted" style="font-size:15px">/ ${hsize(p.totalBytes)}</span></div></div>
      <div><div class="muted">Progress</div><div class="v">${(frac * 100).toFixed(0)}%</div></div></div>
    <div class="meter"><span style="width:${frac * 100}%"></span></div>`;
  $('#idcancel').onclick = cancelJob;
}
RENDER.identical = async function renderIdentical() {
  const el = $('#page-identical');
  const s = st.status;
  if (!s?.scan) { el.innerHTML = emptyHtml('No scan yet', 'Scan first, then find files whose contents are byte-for-byte identical.', 'scan'); return; }
  const I = s.identical;
  const busy = s.job.busy;
  const opts = `<div class="card"><div class="card-head"><div><h2>Find byte-identical files</h2>
      <p class="sub">Files are compared by size, then a quick hash of the first and last 64 KB, then a full BLAKE2b-256 hash of the whole file, using several threads in parallel. File contents are read on this PC only to compute hashes; nothing leaves the PC. Unchanged files are cached, so re-runs are fast.</p></div>
      <button class="btn primary" id="idrunbtn" ${busy ? 'disabled' : ''}>${IC.scan} ${I ? 'Run again' : 'Find identical files'}</button></div>${idOptsHtml()}</div>`;
  const prog = busy && s.idProgress ? '<div class="card" id="idrun"></div>' : '';
  if (!I) {
    el.innerHTML = opts + prog + (busy ? '' : `<div class="card empty">${IC.dup}<h2>Not run yet</h2><p>This reads files that share a size with another file, so it can take a while on large drives. Results tell you exactly which files are true copies.</p></div>`);
    wireIdOpts();
    if (prog) renderIdProgress(s);
    return;
  }
  const q = st.iq;
  el.innerHTML = opts + prog + `<div class="section-gap"></div>
    <div class="stats">
      ${statHtml('Space you can free', hsize(I.openReclaimable), 'by keeping one copy of each', true)}
      ${statHtml('Identical groups', fmt(I.openGroups), `${fmt(I.files)} files in ${fmt(I.groups)} groups found`)}
      ${statHtml('Moved to quarantine', fmt(I.moved), 'this session · undo below')}
      ${statHtml('Hard-linked groups', fmt(I.hardlinkGroups), 'same file under two names — frees nothing')}
    </div>
    <p class="sub" style="margin:10px 2px 0">${fmt(I.considered)} files considered · ${fmt(I.sameSizeFiles)} shared a size · ${fmt(I.afterQuickCheck)} passed the quick check · ${fmt(I.fullyHashed)} fully hashed · ${fmt(I.cacheHits)} from cache · ${I.verified ? 'byte-by-byte verified · ' : ''}${I.skippedExt ? fmt(I.skippedExt) + ' left out by file type · ' : ''}${fmt(I.errors)} unreadable · ${I.threads ? I.threads + ' threads · ' : ''}${I.seconds}s</p>
    <div class="section-gap"></div>
    <div class="card"><div class="card-head"><div><h2>Clean up</h2>
      <p class="sub">Choose which copy to keep in each group; the others are <b>moved</b> (not deleted) to <span class="mono">X:\\_Duplicates (Storage Explorer)\\&lt;run&gt;\\…</span> on the same drive, keeping their original path. Every run can be undone. Open a group to pick the keeper by hand.</p></div></div>
      <div class="row"><label class="field">Keep the copy with</label>
        <select class="input" id="k-rule">${KEEP_RULES.map(([v, t]) => `<option value="${v}" ${st.keep.rule === v ? 'selected' : ''}>${t}</option>`).join('')}</select>
        <input class="input mono" id="k-text" placeholder="text, e.g. Q:\\Photos or Backup" value="${esc(st.keep.text)}" style="width:260px" ${['prefer', 'avoid'].includes(st.keep.rule) ? '' : 'disabled'}>
        <span class="spacer"></span>
        <button class="btn primary" data-act="q-bulk" ${busy ? 'disabled' : ''}>Move duplicates in filtered groups to quarantine…</button></div></div>
    <div class="card">
      <div class="row" style="margin-bottom:12px">
        ${nameFilterHtml('if', q)}
        ${segHtml('iq', 'drives', DRIVE_SEG)}
        <label class="field">Min size <input class="input" id="if-min" type="number" min="0" value="${esc(q.minMb)}" placeholder="0" style="width:80px"> MB</label>
      </div><div id="idgroups"></div></div>
    <div class="card" id="qruns"></div>`;
  wireIdOpts();
  if (prog) renderIdProgress(s);
  wireFilters(el, 'iq', { name: '#if-name', path: '#if-path', minMb: '#if-min' });
  $('#k-rule').onchange = e => { st.keep.rule = e.target.value; $('#k-text').disabled = !['prefer', 'avoid'].includes(st.keep.rule); };
  $('#k-text').oninput = e => { st.keep.text = e.target.value; };
  await Promise.all([renderIdGroups(), renderQRuns()]);
};
function wireIdOpts() {
  renderExtPicker('i');
  const b = $('#idrunbtn');
  if (b) b.onclick = safe(async () => { await api('/api/identical', readIdOpts()); toast('Finding identical files…'); kick(); });
}
function idQuery() {
  const q = st.iq;
  return { name: q.name, path: q.path, drives: q.drives, minMb: q.minMb || 0 };
}
async function renderIdGroups() {
  const box = $('#idgroups');
  if (!box) return;
  const q = st.iq;
  const seq = ++st.renderSeq;
  const r = await api('/api/idgroups?' + new URLSearchParams({ ...idQuery(), sort: q.sort, offset: q.offset, limit: q.limit }));
  if (seq !== st.renderSeq) return;
  const th = (key, label, cls) => sortTh('iq', key, label, cls);
  box.innerHTML = `<div class="row between" style="margin-bottom:6px"><span class="ink2">${fmt(r.total)} groups match · ${hsize(r.reclaimable)} can be freed</span></div>
    <div class="tbl-wrap cq"><table class="tbl fixed"><thead><tr>${th('name', 'File')}${th('size', 'Size each', 'num w-files')}${th('copies', 'Copies', 'num w-share')}
      ${th('reclaimable', 'Can free', 'num w-files')}<th class="opt">Folders</th>${th('newest', 'Newest', 'num w-date opt')}</tr></thead>
    <tbody>${r.rows.map(g => `<tr class="click" data-row="idgroup" data-id="${g.gid}">
      <td><div class="name">${IC.file}<div style="min-width:0"><div class="t ellipsis">${esc(g.name)}${g.otherNames ? ` <span class="muted">+${g.otherNames} other name${g.otherNames > 1 ? 's' : ''}</span>` : ''}</div>
        <div class="desc">${esc(g.drives)}${g.hardlinked ? ' · includes hard links' : ''}</div></div></div></td>
      <td class="num">${hsize(g.size)}</td><td class="num">${g.copies}</td><td class="num"><b>${hsize(g.reclaimable)}</b></td>
      <td class="opt" title="${esc(g.folders.join('\n'))}"><div class="mono ell-start muted"><span>${esc(g.folders[0] || '')}</span></div>
        <div class="mono ell-start muted"><span>${esc(g.folders[1] || '')}${g.moreFolders ? ` (+${g.moreFolders} more)` : ''}</span></div></td>
      <td class="num opt">${fdate(g.newest)}</td></tr>`).join('') || '<tr><td colspan="6" class="muted">No identical groups match these filters.</td></tr>'}</tbody></table></div>
    ${pagerHtml('iq', r)}`;
}
async function renderQRuns() {
  const box = $('#qruns');
  if (!box) return;
  const r = await api('/api/qruns');
  box.innerHTML = `<h2>Quarantine runs</h2><p class="sub">Moved copies stay on their drive under <span class="mono">_Duplicates (Storage Explorer)\\&lt;run&gt;</span>. Undo puts them back; delete a run folder yourself once you are sure. Logs: <span class="mono">app output\\quarantine\\</span></p>
    ${r.runs.length ? `<table class="tbl"><tbody>${r.runs.map(x => `<tr><td class="mono">${esc(x.run)}</td>
      <td class="num">${plural(x.moved, 'file')}</td><td class="num">${hsize(x.bytes)}</td>
      <td class="num">${x.undone ? '<span class="chip">undone</span>' : `<button class="btn sm" data-act="undo" data-run="${esc(x.run)}">Undo</button>`}</td></tr>`).join('')}</tbody></table>`
      : '<p class="muted">Nothing moved yet.</p>'}`;
}
async function quarantineBulk() {
  const body = { mode: 'bulk', rule: st.keep.rule, text: st.keep.text, filter: idQuery() };
  const plan = await api('/api/quarantine', { ...body, dryRun: true });
  if (!plan.files) { toast('Nothing to move for these filters.'); return; }
  const rule = KEEP_RULES.find(r => r[0] === st.keep.rule)[1] + (['prefer', 'avoid'].includes(st.keep.rule) ? ` "${st.keep.text}"` : '');
  if (!confirm(`Move ${fmt(plan.files)} duplicate files (${hsize(plan.bytes)}) from ${fmt(plan.groups)} groups to quarantine?\n\nKept in each group: ${rule}.\nFiles are moved, not deleted, and the run can be undone.`)) return;
  await api('/api/quarantine', body);
  toast('Moving duplicates…'); kick();
}
async function quarantineGroup(gid) {
  const keep = $('input[name="keepcopy"]:checked')?.value;
  if (!keep) return;
  const plan = await api('/api/quarantine', { mode: 'group', gid, keep: +keep, dryRun: true });
  if (!plan.files) { toast('Nothing to move.'); return; }
  if (!confirm(`Move ${plan.files} other cop${plan.files > 1 ? 'ies' : 'y'} (${hsize(plan.bytes)}) to quarantine and keep the selected one?`)) return;
  await api('/api/quarantine', { mode: 'group', gid, keep: +keep });
  closeDrawer(); toast('Moving…'); kick();
}
async function undoRun(run) {
  if (!confirm(`Put every file from quarantine run ${run} back where it was?`)) return;
  await api('/api/undo', { run });
  toast('Restoring…'); kick();
}
async function openIdGroup(gid) {
  const g = await api(`/api/idgroup?gid=${gid}&rule=${encodeURIComponent(st.keep.rule)}&text=${encodeURIComponent(st.keep.text)}`);
  const open = g.copiesList.filter(c => !c.moved);
  openDrawer(g.names.join(' · '),
    `${open.length} identical copies · ${hsize(g.size)} each · ${hsize(g.reclaimable)} can be freed · BLAKE2b ${g.hash.slice(0, 16)}…`,
    'Every copy below has exactly the same bytes. Pick the one to <b>keep</b>; the others are moved to quarantine on their own drive (undoable). Hard links are the same file under another name, so moving them frees nothing and they are left alone.',
    g.copiesList.map(c => `<label class="copy ${c.keep ? 'newest' : ''}" style="display:block;cursor:${c.moved ? 'default' : 'pointer'};${c.moved ? 'opacity:.55' : ''}">
      <div class="row" style="flex-wrap:nowrap;align-items:flex-start">
        <input type="radio" name="keepcopy" value="${c.id}" ${c.keep ? 'checked' : ''} ${c.moved ? 'disabled' : ''} style="margin-top:3px">
        <div style="min-width:0"><div class="path">${esc(c.path)}</div>
          <div class="badges">${c.newest ? '<span class="chip accent">Newest</span>' : ''}${c.moved ? '<span class="chip">moved to quarantine</span>' : ''}
            ${c.linkOf ? `<span class="chip">hard link of ${esc(c.linkOf.split('\\').slice(-2).join('\\'))}</span>` : ''}</div>
          <div class="meta"><span>Created ${fdate(c.created)}</span><span>Modified ${fdate(c.modified)}</span></div>
          ${copyActsHtml(c)}</div></div></label>`).join('')
    + (open.length > 1 ? `<div class="section-gap"></div><button class="btn primary" data-act="q-group" data-gid="${g.gid}" ${st.status?.job?.busy ? 'disabled' : ''}>Keep selected · move the other copies to quarantine…</button>` : ''));
  $$('input[name="keepcopy"]').forEach(r => r.onchange = () => $$('#dbody .copy').forEach(c => c.classList.toggle('newest', $('input', c).checked)));
}

// ================================================================= PAIRS
async function renderPairs() {
  const el = $('#page-pairs');
  const s = st.status;
  if (!s?.scan) { el.innerHTML = emptyHtml('No scan yet', 'Scan first to find folders that were copied to another place.', 'scan'); return; }
  if (!s.dups) { el.innerHTML = emptyHtml('Matching not finished', 'Backup roots come from same-name matching.', 'dups', 'Go to Same-name files'); return; }
  const seq = ++st.renderSeq;
  const r = await api('/api/pairs?min=' + st.pairsMin);
  if (seq !== st.renderSeq) return;
  const rootCell = (p, t) => `<div class="path">${esc(p)}</div>${t ? `<div class="desc">${esc(t)}</div>` : ''}`;
  el.innerHTML = `<div class="card"><div class="card-head"><div><h2>Folder trees found in two places</h2>
      <p class="sub">Each row is two “roots”: below them, the same folders and file names exist in both. These are usually backup copies. The “same size” bar shows how many shared files are also identical in size.</p></div>
      <label class="field">Min shared files <input class="input" id="pmin" type="number" min="1" value="${st.pairsMin}" style="width:90px"></label></div>
    <div class="tbl-wrap"><table class="tbl"><thead><tr><th>Root A</th><th>Root B</th><th>Drives</th><th class="num">Shared files</th><th>Same size</th><th class="num">Size</th><th class="num">Levels</th><th></th></tr></thead>
    <tbody>${r.rows.map(p => `<tr>
      <td style="max-width:360px">${rootCell(p.root_a, p.titleA)}</td><td style="max-width:360px">${rootCell(p.root_b, p.titleB)}</td>
      <td>${esc(p.drives)}</td><td class="num">${fmt(p.files)}</td><td>${sizeBar(p.pct_same / 100, p.pct_same + '%')}</td>
      <td class="num">${hsize(p.bytes)}</td><td class="num">${p.levels}</td>
      <td class="acts" style="opacity:1"><button class="ibtn" title="Open root A" data-act="open-root" data-path="${esc(p.root_a)}">${IC.open}</button>
        <button class="ibtn" title="Same-name files under A" data-act="dupes" data-path="${esc(p.root_a)}">${IC.dup}</button>
        <button class="ibtn" title="Open root B" data-act="open-root" data-path="${esc(p.root_b)}">${IC.open}</button>
        <button class="ibtn" title="Same-name files under B" data-act="dupes" data-path="${esc(p.root_b)}">${IC.dup}</button></td></tr>`).join('')
      || '<tr><td colspan="8" class="muted">No pairs with this many shared files.</td></tr>'}</tbody></table></div>
    <p class="sub" style="margin:10px 0 0">${fmt(r.total)} pairs${r.total > r.rows.length ? ` · showing first ${fmt(r.rows.length)}` : ''}. Icons: open root A · same-name files in A · open root B · same-name files in B.</p></div>`;
  $('#pmin').onchange = e => { st.pairsMin = Math.max(1, +e.target.value || 1); renderPairs(); };
}
RENDER.pairs = renderPairs;

// ================================================================= EXPORT
async function runExport(keys) {
  await api('/api/export', { keys });
  toast('Export started');
  kick();
}
RENDER.export = function renderExport() {
  const el = $('#page-export');
  const s = st.status;
  const items = [
    ['folders', 'Folder sizes', 'Every folder at every level with size, share of parent, file count and “what is it”.'],
    ['levels', 'Storage by level', 'Total size of all folders at each depth, per drive.'],
    ['dups', 'Same-name files', 'Groups, every copy with parent/grandparent match, path-match summary and backup root pairs (4 files).'],
    ['identical', 'Identical files', 'Every byte-identical copy with its BLAKE2b-256 hash, hard-link and quarantine status.'],
    ['inventory', 'All files inventory', 'Every file with dates and size. Can be several hundred MB for a whole-PC scan.'],
    ['errors', 'Unreadable folders', 'Folders the scan could not open.'],
  ];
  el.innerHTML = `<div class="card"><div class="card-head"><div><h2>Export CSV files</h2>
      <p class="sub">Saved to <span class="mono">${esc(st.init.outDir)}</span></p></div>
      <div class="row"><button class="btn" data-act="open-out">${IC.open} Open folder</button>
      <button class="btn primary" data-act="export" data-keys="folders,levels,dups,errors" ${s?.scan ? '' : 'disabled'}>${IC.down} Export all (except inventory)</button></div></div>
    <div class="tbl-wrap"><table class="tbl"><tbody>${items.map(([k, t, d]) => `<tr><td><b>${t}</b><div class="desc">${d}</div></td>
      <td class="num"><button class="btn sm" data-act="export" data-keys="${k}" ${s?.scan && (k !== 'dups' || s.dups) && (k !== 'identical' || s.identical) ? '' : 'disabled'}>${IC.down} Export</button></td></tr>`).join('')}</tbody></table></div></div>
    <div class="card"><h2>Recent exports</h2>${(s?.exports || []).length ? `<table class="tbl"><tbody>${s.exports.slice().reverse().map(x =>
      `<tr><td class="mono">${esc(x.file)}</td><td class="num">${hsize(x.size)}</td><td class="num">${new Date(x.time * 1000).toLocaleTimeString()}</td></tr>`).join('')}</tbody></table>`
      : '<p class="sub">Nothing exported in this session yet.</p>'}</div>`;
};

// ================================================================= boot
(async function boot() {
  let t = 'system';
  try { t = localStorage.getItem('theme') || 'system'; } catch (e) { /* storage unavailable */ }
  applyTheme(t);
  try {
    st.init = await api('/api/init');
  } catch (e) {
    document.body.innerHTML = '<p style="padding:40px">Could not reach the app server. Start it again with “Storage Explorer.bat”.</p>';
    return;
  }
  st.init.drives.forEach(d => { if (d.root !== st.init.systemDrive) st.sel.add(d.root); });
  // opened from the right-click menu (#page): jump there once the scan finishes
  if (TITLES[location.hash.slice(1)]) st.wantStorage = true;
  go('scan');
  poll();
})();
