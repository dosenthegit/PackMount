const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];

const state = {
  sourceType: 'local',
  shareType: 'smb',
  mounts: [],
  shares: [],
  logs: [],
  path: null,
  roots: []
};

async function api(path, options = {}) {
  const res = await fetch(path, {
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options
  });
  if (res.status === 401) {
    showLogin();
    throw new Error('Not authenticated');
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || data.message || `Request failed: ${res.status}`);
  return data;
}

function toast(message, ms = 3500) {
  const el = $('#toast');
  el.textContent = message;
  el.classList.remove('hidden');
  clearTimeout(window.__toastTimer);
  window.__toastTimer = setTimeout(() => el.classList.add('hidden'), ms);
}

function showLogin() { $('#loginBackdrop').classList.remove('hidden'); }
function hideLogin() { $('#loginBackdrop').classList.add('hidden'); }

function setBusy(button, busy, text) {
  if (!button) return;
  if (busy) {
    button.dataset.original = button.textContent;
    button.textContent = text || 'Working...';
    button.disabled = true;
  } else {
    button.textContent = button.dataset.original || button.textContent;
    button.disabled = false;
  }
}

function sourceLabel(m) {
  const type = (m.source_type || '').toUpperCase();
  return `${m.source_path || ''} (${type === 'LOCAL' ? 'Local folder' : type})`;
}

function statusClass(status) {
  return String(status || '').toLowerCase().includes('error') ? 'status-badge error' : 'status-badge';
}

function renderMounts() {
  const rows = $('#mountRows');
  rows.innerHTML = '';
  if (!state.mounts.length) {
    rows.innerHTML = `<div class="mount-row"><span>No mounts yet. Add a source and press Mount.</span><span></span><span></span><span></span></div>`;
    return;
  }
  for (const m of state.mounts) {
    const row = document.createElement('div');
    row.className = 'mount-row';
    row.innerHTML = `
      <div class="mount-source"><span class="source-icon">${m.source_type === 'nfs' ? '⌯' : m.source_type === 'smb' ? '▤' : '▣'}</span><div><strong title="${escapeHtml(m.source_path)}">${escapeHtml(m.source_path)}</strong><small>${m.source_type === 'local' ? 'Local folder' : m.source_type.toUpperCase()}</small></div></div>
      <span class="mountpoint" title="${escapeHtml(m.mount_to)}">${escapeHtml(m.mount_to)}</span>
      <span><span class="${statusClass(m.status)}"><span class="status-dot"></span>${escapeHtml(m.status || 'Created')}</span></span>
      <span class="action-buttons">
        <button title="Start" data-action="start" data-id="${m.id}">▶</button>
        <button title="Stop" data-action="stop" data-id="${m.id}">■</button>
        <button title="Delete" data-action="delete" data-id="${m.id}">🗑</button>
      </span>`;
    rows.appendChild(row);
  }
}

function renderShares() {
  const list = $('#shareList');
  if (!list) return;
  list.innerHTML = '';
  if (!state.shares.length) return;
  for (const s of state.shares) {
    const item = document.createElement('div');
    item.className = 'share-item';
    item.innerHTML = `
      <div><strong>${escapeHtml(s.share_name)} · ${s.share_type.toUpperCase()}</strong><small>${escapeHtml(s.path)} · ${s.permission === 'rw' ? 'Read/write' : 'Read-only'} · ${s.enabled ? 'Enabled' : 'Disabled'}</small></div>
      <div class="share-actions"><button data-share-action="toggle" data-id="${s.id}">${s.enabled ? 'Disable' : 'Enable'}</button><button data-share-action="delete" data-id="${s.id}">Delete</button></div>`;
    list.appendChild(item);
  }
}

function renderStatus() {
  const mounted = state.mounts.filter(m => m.mounted || m.status === 'Mounted').length;
  const activeShares = state.shares.filter(s => s.enabled);
  const smb = activeShares.filter(s => s.share_type === 'smb').length;
  const nfs = activeShares.filter(s => s.share_type === 'nfs').length;
  $('#mountedCount').textContent = mounted;
  $('#shareCount').textContent = activeShares.length;
  $('#shareBreakdown').textContent = `${smb} SMB, ${nfs} NFS`;
  const activity = $('#activityList');
  activity.innerHTML = '';
  for (const log of state.logs.slice(0, 4)) {
    const div = document.createElement('div');
    div.className = 'activity-item';
    div.innerHTML = `<span class="status-dot"></span><span>${escapeHtml(log.message)}</span><time>${formatTime(log.created_at)}</time>`;
    activity.appendChild(div);
  }
}

function renderLogsOverlay() {
  closeOverlay();
  const overlay = document.createElement('section');
  overlay.className = 'logs-overlay glass-panel';
  overlay.id = 'overlay';
  overlay.innerHTML = `<div class="overlay-head"><h2>Logs</h2><button id="closeOverlay">Close</button></div><div id="overlayBody"></div>`;
  document.body.appendChild(overlay);
  const body = overlay.querySelector('#overlayBody');
  body.innerHTML = state.logs.map(log => `
    <div class="log-line"><small>${escapeHtml(formatDateTime(log.created_at))}</small><small>${escapeHtml(log.level)}</small><span>${escapeHtml(log.message)}${log.details ? `<br><small>${escapeHtml(log.details)}</small>` : ''}</span></div>`).join('') || '<p>No logs yet.</p>';
  $('#closeOverlay').addEventListener('click', closeOverlay);
}

async function renderSettingsOverlay() {
  closeOverlay();
  const overlay = document.createElement('section');
  overlay.className = 'settings-overlay glass-panel';
  overlay.id = 'overlay';
  overlay.innerHTML = `<div class="overlay-head"><h2>Settings</h2><button id="closeOverlay">Close</button></div><div id="overlayBody"><p>Loading...</p></div>`;
  document.body.appendChild(overlay);
  $('#closeOverlay').addEventListener('click', closeOverlay);
  try {
    const sys = await api('/api/system');
    $('#overlayBody').innerHTML = `
      <p>Mount root: <code>${escapeHtml(sys.mount_root)}</code></p>
      <p>Allowed local roots: <code>${escapeHtml((sys.allowed_local_roots || []).join(', '))}</code></p>
      <h3>System tools</h3>
      ${Object.entries(sys.tools || {}).map(([k, v]) => `<div class="cap-line"><span>${escapeHtml(k)}</span><strong class="${v ? 'cap-ok' : 'cap-bad'}">${v ? 'OK' : 'Missing'}</strong></div>`).join('')}`;
  } catch (err) {
    $('#overlayBody').innerHTML = `<p>${escapeHtml(err.message)}</p>`;
  }
}

function closeOverlay() { const el = $('#overlay'); if (el) el.remove(); }

async function refreshAll() {
  const [mounts, shares, logs] = await Promise.all([
    api('/api/mounts'),
    api('/api/shares'),
    api('/api/logs?limit=100')
  ]);
  state.mounts = mounts.items || [];
  state.shares = shares.items || [];
  state.logs = logs.items || [];
  renderMounts();
  renderShares();
  renderStatus();
}

async function checkSession() {
  try {
    const me = await api('/api/me');
    $('#sideVersion').textContent = `Version ${me.version || '1.2.3'}`;
    hideLogin();
    await refreshAll();
  } catch (_) {
    showLogin();
  }
}

function payloadForMount() {
  return {
    source_type: state.sourceType,
    source_path: $('#sourcePath').value.trim(),
    username: $('#sourceUsername').value.trim() || null,
    password: $('#sourcePassword').value || null,
    mount_to: $('#mountTo').value.trim(),
    mount_name: $('#mountName').value.trim(),
    automount: $('#automount').checked,
    read_only: $('#readOnly').checked
  };
}

function payloadForShare() {
  return {
    share_type: state.shareType,
    share_name: $('#shareName').value.trim(),
    path: $('#sharePath').value.trim(),
    permission: $('#permission').value,
    allow_guest: $('#allowGuest').checked,
    allow_network: $('#allowNetwork').checked,
    allowed_clients: $('#allowedClients').value.trim() || null
  };
}

async function openPath(path) {
  const data = await api(`/api/path${path ? `?path=${encodeURIComponent(path)}` : ''}`);
  state.path = data.path;
  state.roots = data.roots || [];
  $('#pathCurrent').textContent = data.path;
  $('#rootList').innerHTML = state.roots.map(r => `<button type="button" class="secondary-btn" data-root="${escapeHtml(r)}">${escapeHtml(r)}</button>`).join('');
  $('#directoryList').innerHTML = (data.entries || []).map(e => `<button type="button" data-path="${escapeHtml(e.path)}">▣ ${escapeHtml(e.name)}</button>`).join('') || '<p>No folders found.</p>';
  $('#pathBackdrop').classList.remove('hidden');
  $('#pathUpBtn').dataset.parent = data.parent || data.path;
}

function bindEvents() {
  $('#loginForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const btn = e.submitter;
    setBusy(btn, true, 'Logging in...');
    try {
      await api('/api/login', { method: 'POST', body: JSON.stringify({ username: $('#loginUsername').value, password: $('#loginPassword').value, remember: $('#rememberMe').checked }) });
      hideLogin();
      await refreshAll();
      toast('Logged in');
    } catch (err) {
      toast(err.message || 'Login failed');
    } finally {
      setBusy(btn, false);
    }
  });

  $('.modal-close').addEventListener('click', () => $('#loginBackdrop').classList.add('hidden'));
  $('#logoutBtn').addEventListener('click', async () => { try { await api('/api/logout', { method: 'POST' }); } finally { showLogin(); } });
  $('#refreshBtn').addEventListener('click', async () => { await refreshAll(); toast('Refreshed'); });
  $('#viewLogsBtn').addEventListener('click', renderLogsOverlay);

  $$('.nav-item').forEach(btn => btn.addEventListener('click', async () => {
    $$('.nav-item').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    const section = btn.dataset.section;
    if (section === 'logs') {
      await refreshAll(); renderLogsOverlay();
    } else if (section === 'settings') {
      renderSettingsOverlay();
    } else {
      closeOverlay();
      document.querySelector(`[class*="${section === 'overview' ? 'status-panel' : section === 'sources' ? 'source-panel' : section === 'mounts' ? 'active-mounts' : 'share-panel'}"]`)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  }));

  $$('.source-tabs .seg').forEach(btn => btn.addEventListener('click', () => {
    $$('.source-tabs .seg').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    state.sourceType = btn.dataset.source;
    if (state.sourceType === 'local') $('#sourcePath').value = '/data/archive';
    if (state.sourceType === 'smb') $('#sourcePath').value = '//nas/archive';
    if (state.sourceType === 'nfs') $('#sourcePath').value = '192.168.1.50:/export/archive';
  }));

  $$('.share-tabs .seg').forEach(btn => btn.addEventListener('click', () => {
    $$('.share-tabs .seg').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    state.shareType = btn.dataset.share;
  }));

  $('#toggleSourcePassword').addEventListener('click', () => {
    const input = $('#sourcePassword');
    input.type = input.type === 'password' ? 'text' : 'password';
  });

  $('#mountSettingsForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const btn = e.submitter;
    setBusy(btn, true, 'Mounting...');
    try {
      const res = await api('/api/mounts', { method: 'POST', body: JSON.stringify(payloadForMount()) });
      $('#sharePath').value = res.item.mount_to;
      await refreshAll();
      toast('Mounted successfully');
    } catch (err) {
      toast(err.message || 'Mount failed', 7000);
    } finally {
      setBusy(btn, false);
    }
  });

  $('#mountRows').addEventListener('click', async (e) => {
    const btn = e.target.closest('button[data-action]');
    if (!btn) return;
    const id = btn.dataset.id;
    const action = btn.dataset.action;
    setBusy(btn, true, '...');
    try {
      if (action === 'delete') await api(`/api/mounts/${id}`, { method: 'DELETE' });
      else await api(`/api/mounts/${id}/${action}`, { method: 'POST' });
      await refreshAll();
      toast(`Mount ${action} completed`);
    } catch (err) {
      toast(err.message || `Mount ${action} failed`, 7000);
    } finally {
      setBusy(btn, false);
    }
  });

  $('#enableShareBtn').addEventListener('click', async (e) => {
    const btn = e.currentTarget;
    setBusy(btn, true, 'Applying...');
    try {
      await api('/api/shares', { method: 'POST', body: JSON.stringify(payloadForShare()) });
      await refreshAll();
      toast('Sharing enabled');
    } catch (err) {
      toast(err.message || 'Share failed', 7000);
    } finally {
      setBusy(btn, false);
    }
  });

  $('#shareList').addEventListener('click', async (e) => {
    const btn = e.target.closest('button[data-share-action]');
    if (!btn) return;
    setBusy(btn, true, '...');
    try {
      if (btn.dataset.shareAction === 'delete') await api(`/api/shares/${btn.dataset.id}`, { method: 'DELETE' });
      else await api(`/api/shares/${btn.dataset.id}/toggle`, { method: 'POST' });
      await refreshAll();
      toast('Share updated');
    } catch (err) {
      toast(err.message || 'Share update failed', 7000);
    } finally {
      setBusy(btn, false);
    }
  });

  $('#browseBtn').addEventListener('click', async () => {
    try { await openPath($('#sourcePath').value.startsWith('/') ? $('#sourcePath').value : null); }
    catch (err) { toast(err.message || 'Could not open browser', 6000); }
  });
  $('#closePathBtn').addEventListener('click', () => $('#pathBackdrop').classList.add('hidden'));
  $('#directoryList').addEventListener('click', async (e) => {
    const btn = e.target.closest('button[data-path]');
    if (btn) await openPath(btn.dataset.path);
  });
  $('#rootList').addEventListener('click', async (e) => {
    const btn = e.target.closest('button[data-root]');
    if (btn) await openPath(btn.dataset.root);
  });
  $('#pathUpBtn').addEventListener('click', async (e) => openPath(e.currentTarget.dataset.parent));
  $('#selectPathBtn').addEventListener('click', () => {
    $('#sourcePath').value = state.path;
    $('#pathBackdrop').classList.add('hidden');
  });
}

function formatTime(value) {
  try { return new Date(value).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }); } catch (_) { return ''; }
}
function formatDateTime(value) {
  try { return new Date(value).toLocaleString(); } catch (_) { return value || ''; }
}
function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>'"]/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[ch]));
}

document.addEventListener('DOMContentLoaded', async () => {
  bindEvents();
  await checkSession();
  setInterval(() => { if ($('#loginBackdrop').classList.contains('hidden')) refreshAll().catch(() => {}); }, 10000);
});
