(() => {
  'use strict';
  const base = document.body.dataset.base || '';
  const csrf = document.body.dataset.csrf;
  const $ = (id) => document.getElementById(id);
  const SEC_PER_PAGE = 1.3, SEC_BASE = 15;
  const LABEL = { queued: 'En cola…', running: 'Traduciendo…', done: 'Listo', error: 'Ha fallado' };
  let maxBytes = 0, pollTimer = null;

  const api = (path, opts = {}) => fetch(base + path, {
    credentials: 'same-origin', ...opts,
    headers: { 'X-CSRF-Token': csrf, ...(opts.headers || {}) },
  });

  function showError(msg) { const e = $('error'); e.textContent = msg || ''; e.hidden = !msg; }

  function etaText(job) {
    if (!job.pages) return 'Calculando el tiempo…';
    const total = SEC_BASE + job.pages * SEC_PER_PAGE;
    const left = Math.max(0, total * (1 - job.progress / 100));
    if (left < 45) return 'Menos de un minuto';
    return 'Unos ' + Math.round(left / 60) + ' min';
  }

  // ---------------------------------------------------------------- estado de mordor
  async function refreshStatus() {
    try {
      const r = await api('/api/status');
      if (!r.ok) return;
      const s = await r.json();
      maxBytes = s.max_mb * 1024 * 1024;
      const b = $('status');
      b.hidden = s.mordor;
      b.className = 'banner down';
      b.textContent = s.mordor ? '' : s.message;
      $('pick').disabled = !s.mordor;
      updateGo(!s.mordor);
    } catch (e) { /* sin red: se reintenta */ }
  }

  // ---------------------------------------------------------------- selección y envío
  let down = false;
  function updateGo(isDown) {
    if (typeof isDown === 'boolean') down = isDown;
    $('go').disabled = down || !$('file').files.length;
  }

  $('pick').addEventListener('click', () => $('file').click());
  $('file').addEventListener('change', () => {
    const f = $('file').files[0];
    showError('');
    if (f && maxBytes && f.size > maxBytes) {
      showError('El PDF supera el tamaño máximo (' + Math.round(maxBytes / 1048576) + ' MB).');
      $('file').value = '';
    }
    const g = $('file').files[0];
    $('chosen').textContent = g ? g.name + ' (' + (g.size / 1048576).toFixed(1) + ' MB)' : '';
    updateGo();
  });

  async function ensurePush() {
    // Debe llamarse desde un gesto del usuario (el toque en "Traducir").
    if (!('serviceWorker' in navigator) || !('PushManager' in window) || !('Notification' in window)) return;
    try {
      const reg = await navigator.serviceWorker.register(base + '/sw.js', { scope: base + '/' });
      if (Notification.permission === 'default') await Notification.requestPermission();
      if (Notification.permission !== 'granted') return;
      const { key } = await (await api('/api/push/key')).json();
      const pad = '='.repeat((4 - key.length % 4) % 4);
      const raw = atob((key + pad).replace(/-/g, '+').replace(/_/g, '/'));
      const appKey = Uint8Array.from(raw, (c) => c.charCodeAt(0));
      const sub = (await reg.pushManager.getSubscription()) ||
        (await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: appKey }));
      await api('/api/push/subscribe', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(sub),
      });
    } catch (e) { /* el aviso es opcional: la traducción sigue adelante */ }
  }

  $('upload-form').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const f = $('file').files[0];
    if (!f) return;
    showError('');
    $('go').disabled = true;
    await ensurePush();
    const fd = new FormData();
    fd.append('pdf', f);
    const xhr = new XMLHttpRequest();
    xhr.open('POST', base + '/api/jobs');
    xhr.setRequestHeader('X-CSRF-Token', csrf);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) showProgress({ filename: f.name, status: 'queued', progress: 0 },
        'Subiendo… ' + Math.round(100 * e.loaded / e.total) + ' %', true);
    };
    xhr.onload = () => {
      let body = {};
      try { body = JSON.parse(xhr.responseText); } catch (e) { /* respuesta no JSON */ }
      if (xhr.status === 202) {
        $('file').value = ''; $('chosen').textContent = '';
        history.replaceState(null, '', '?job=' + body.id);
        watch(body.id);
      } else {
        $('progress-card').hidden = true;
        showError(body.error || 'No se pudo subir el PDF.');
        updateGo();
      }
    };
    xhr.onerror = () => { $('progress-card').hidden = true; showError('No hay conexión.'); updateGo(); };
    xhr.send(fd);
  });

  // ---------------------------------------------------------------- seguimiento
  function showProgress(job, text, indeterminate) {
    $('progress-card').hidden = false;
    $('p-name').textContent = job.filename;
    $('bar').value = indeterminate ? 0 : job.progress;
    $('p-text').textContent = text;
    $('p-hint').hidden = job.status === 'done' || job.status === 'error';
    const done = job.status === 'done';
    $('p-actions').hidden = !done;
    if (done) {
      $('p-view').href = base + '/jobs/' + job.id + '/view';
      $('p-download').href = base + '/jobs/' + job.id + '/download';
    }
  }

  async function watch(id) {
    clearTimeout(pollTimer);
    try {
      const r = await api('/api/jobs/' + id);
      if (!r.ok) { $('progress-card').hidden = true; return; }
      const job = await r.json();
      let text = LABEL[job.status];
      if (job.status === 'running') text += ' ' + job.progress + ' % · ' + etaText(job);
      if (job.status === 'error') text = job.error || LABEL.error;
      showProgress(job, text, false);
      if (job.status === 'queued' || job.status === 'running') {
        pollTimer = setTimeout(() => watch(id), 2000);
      } else {
        updateGo(); loadJobs();
      }
    } catch (e) { pollTimer = setTimeout(() => watch(id), 5000); }
  }

  // ---------------------------------------------------------------- historial
  async function loadJobs() {
    try {
      const { jobs } = await (await api('/api/jobs')).json();
      const ul = $('jobs');
      ul.replaceChildren();
      $('jobs-empty').hidden = jobs.length > 0;
      for (const j of jobs) {
        const li = document.createElement('li');
        const name = document.createElement('div');
        name.className = 'name';
        name.textContent = j.filename;
        if (j.status === 'error' && j.error) {
          const why = document.createElement('div');
          why.className = 'muted';
          why.textContent = j.error;
          name.append(why);
        }
        const acts = document.createElement('div');
        acts.className = 'actions';
        const tag = document.createElement('span');
        tag.className = 'tag ' + j.status;
        tag.textContent = LABEL[j.status];
        acts.append(tag);
        if (j.status === 'done') {
          for (const [label, path] of [['Ver', 'view'], ['Descargar', 'download']]) {
            const a = document.createElement('a');
            a.className = 'btn small';
            a.textContent = label;
            a.href = base + '/jobs/' + j.id + '/' + path;
            if (path === 'view') { a.target = '_blank'; a.rel = 'noopener'; }
            acts.append(a);
          }
        }
        if (j.status === 'done' || j.status === 'error') {
          const del = document.createElement('button');
          del.className = 'btn small';
          del.textContent = 'Borrar';
          del.addEventListener('click', async () => { await api('/api/jobs/' + j.id + '/delete', { method: 'POST' }); loadJobs(); });
          acts.append(del);
        }
        li.append(name, acts);
        ul.append(li);
      }
    } catch (e) { /* se reintenta en la siguiente carga */ }
  }

  refreshStatus();
  setInterval(refreshStatus, 30000);
  loadJobs();
  const open = new URLSearchParams(location.search).get('job');
  if (open) watch(open);
})();
