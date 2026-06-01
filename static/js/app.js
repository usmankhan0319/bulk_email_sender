// =====================================================================
// Bulk Email Sender — dashboard logic
// =====================================================================

let parsedRecipients = [];
let lastLogId = 0;

// ---------- nav ----------

document.querySelectorAll('.nav-item').forEach(el => {
  el.addEventListener('click', () => switchPage(el.dataset.page));
});

function switchPage(name) {
  document.querySelectorAll('.nav-item').forEach(el =>
    el.classList.toggle('active', el.dataset.page === name));
  document.querySelectorAll('.page').forEach(el =>
    el.classList.toggle('active', el.dataset.page === name));
  if (name === 'smtp') loadSmtps();
  if (name === 'campaigns') loadCampaigns();
  if (name === 'dashboard') refreshAll();
}

// ---------- toast ----------

function toast(msg, kind='') {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast show ' + kind;
  setTimeout(() => t.classList.remove('show'), 3000);
}

// ---------- API helpers ----------

async function api(path, opts={}) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    const err = await res.json().catch(() => ({error: res.statusText}));
    throw new Error(err.error || err.detail || res.statusText);
  }
  return res.json();
}

async function apiUpload(path, file) {
  const fd = new FormData(); fd.append('file', file);
  return api(path, { method: 'POST', body: fd });
}

// ---------- dashboard ----------

async function refreshAll() {
  try {
    const stats = await api('/api/stats');
    document.getElementById('t-sent').textContent = stats.sent_today || 0;
    document.getElementById('t-opened').textContent = stats.recipients.opened || 0;
    document.getElementById('t-pending').textContent = stats.recipients.pending || 0;
    document.getElementById('t-failed').textContent = stats.recipients.failed || 0;

    const total = stats.recipients.total || 0;
    const sent = stats.recipients.sent || 0;
    const openR = total ? Math.round(100 * (stats.recipients.opened || 0) / Math.max(1, sent)) : 0;
    const failR = total ? Math.round(100 * (stats.recipients.failed || 0) / total) : 0;
    document.getElementById('t-open-rate').textContent = openR + '% open rate';
    document.getElementById('t-fail-rate').textContent = failR + '% failure rate';

    document.getElementById('sf-sent').textContent = stats.sent_today || 0;
    document.getElementById('sf-pending').textContent = stats.recipients.pending || 0;

    const d = await api('/api/stats/domains');
    const tb = document.getElementById('domains-tbody');
    if (!d.domains.length) {
      tb.innerHTML = '<tr><td colspan="6" class="muted">No data yet — upload recipients to begin.</td></tr>';
    } else {
      tb.innerHTML = d.domains.slice(0, 25).map(r => {
        const openRate = r.sent ? Math.round(100 * r.opened / r.sent) : 0;
        return `<tr>
          <td>${r.domain || '—'}</td>
          <td class="right">${r.total}</td>
          <td class="right">${r.sent}</td>
          <td class="right">${r.opened}</td>
          <td class="right">${r.failed}</td>
          <td class="right">${openRate}%</td>
        </tr>`;
      }).join('');
    }

    const s = await api('/api/stats/smtps');
    document.getElementById('nav-smtp-count').textContent = s.smtps.length;
    document.getElementById('sf-smtp').textContent = s.smtps.filter(x => x.status === 'active').length;
    const stb = document.getElementById('smtps-tbody');
    if (!s.smtps.length) {
      stb.innerHTML = '<tr><td colspan="6" class="muted">No SMTP servers configured.</td></tr>';
    } else {
      stb.innerHTML = s.smtps.map(r => {
        const pct = Math.min(100, Math.round(100 * r.sent_today / Math.max(1, r.daily_limit)));
        return `<tr>
          <td>${escape(r.name)}</td>
          <td class="muted">${escape(r.host)}</td>
          <td>${statusPill(r.status)}</td>
          <td class="right">${r.sent_today}</td>
          <td class="right">${r.daily_limit}</td>
          <td class="right" style="min-width:120px"><div class="progress"><div style="width:${pct}%"></div></div></td>
        </tr>`;
      }).join('');
    }

    const cl = await api('/api/campaigns');
    document.getElementById('nav-camp-count').textContent = cl.campaigns.length;
  } catch (e) {
    console.error(e);
  }
}

function statusPill(s) {
  if (s === 'active') return '<span class="pill good"><span class="dot"></span>active</span>';
  if (s === 'running') return '<span class="pill info"><span class="dot"></span>running</span>';
  if (s === 'scheduled') return '<span class="pill info">scheduled</span>';
  if (s === 'paused') return '<span class="pill warn">paused</span>';
  if (s === 'completed') return '<span class="pill good">completed</span>';
  if (s === 'inactive') return '<span class="pill bad">inactive</span>';
  if (s === 'failed') return '<span class="pill bad">failed</span>';
  return `<span class="pill">${escape(s || '')}</span>`;
}

function escape(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c]);
}

// ---------- SMTP ----------

async function loadSmtps() {
  const d = await api('/api/smtp');
  const tb = document.getElementById('smtp-tbody');
  if (!d.smtps.length) {
    tb.innerHTML = '<tr><td colspan="7" class="muted">No SMTP servers yet.</td></tr>';
    return;
  }
  tb.innerHTML = d.smtps.map(s => `
    <tr>
      <td>${s.id}</td>
      <td>${escape(s.name)}</td>
      <td class="muted">${escape(s.host)}:${s.port}</td>
      <td class="muted">${escape(s.from_email)}</td>
      <td>${statusPill(s.status)}${s.last_test_error ? `<div class="muted" style="font-size:11px;margin-top:4px">${escape(s.last_test_error).slice(0,80)}</div>` : ''}</td>
      <td class="right">${s.daily_limit}</td>
      <td class="right">
        <button class="btn small" onclick="testSmtp(${s.id})">Test</button>
        <button class="btn small danger" onclick="deleteSmtp(${s.id})">Delete</button>
      </td>
    </tr>`).join('');
}

function openSmtpModal() {
  ['s-name','s-host','s-user','s-pass','s-from-email','s-from-name'].forEach(id => document.getElementById(id).value = '');
  document.getElementById('s-port').value = 587;
  document.getElementById('s-limit').value = 500;
  document.getElementById('s-tls').checked = true;
  document.getElementById('smtp-test-result').textContent = '';
  document.getElementById('smtp-modal').classList.add('show');
}
function closeSmtpModal() { document.getElementById('smtp-modal').classList.remove('show'); }

async function submitSmtp() {
  const btn = document.getElementById('btn-smtp-save');
  const out = document.getElementById('smtp-test-result');
  btn.disabled = true; out.textContent = 'Testing connection & domain DNS...';
  try {
    const body = {
      name: document.getElementById('s-name').value.trim(),
      host: document.getElementById('s-host').value.trim(),
      port: parseInt(document.getElementById('s-port').value),
      username: document.getElementById('s-user').value.trim(),
      password: document.getElementById('s-pass').value,
      from_email: document.getElementById('s-from-email').value.trim(),
      from_name: document.getElementById('s-from-name').value.trim(),
      daily_limit: parseInt(document.getElementById('s-limit').value),
      use_tls: document.getElementById('s-tls').checked,
    };
    if (!body.name || !body.host || !body.from_email) throw new Error('Name, host and from_email are required');
    const res = await api('/api/smtp', {
      method: 'POST',
      headers: {'content-type': 'application/json'},
      body: JSON.stringify(body),
    });
    if (res.ok) {
      toast('SMTP connected. Checking deliverability...', 'good');
      closeSmtpModal();
      loadSmtps(); refreshAll();
      if (res.deliverability) {
        const domain = body.from_email.split('@')[1];
        document.getElementById('dlv-input').value = domain;
        renderDlv(res.deliverability);
        switchPage('dashboard');
      }
    } else {
      out.innerHTML = `<span style="color:var(--bad)">${escape(res.error || 'Connection failed')}</span>`;
      toast('SMTP saved but connection failed', 'bad');
    }
  } catch (e) {
    out.innerHTML = `<span style="color:var(--bad)">${escape(e.message)}</span>`;
  } finally {
    btn.disabled = false;
  }
}

// ---------- deliverability ----------

async function runDlvCheck() {
  const domain = document.getElementById('dlv-input').value.trim();
  if (!domain) return toast('Enter a domain', 'bad');
  document.getElementById('dlv-summary').textContent = 'Looking up DNS...';
  document.getElementById('dlv-detail').innerHTML = '';
  try {
    const r = await api(`/api/deliverability?domain=${encodeURIComponent(domain)}`);
    renderDlv(r);
  } catch (e) { toast(e.message, 'bad'); }
}

function renderDlv(r) {
  const color = r.verdict === 'good' ? 'var(--good)' : r.verdict === 'warn' ? 'var(--warn)' : 'var(--bad)';
  document.getElementById('dlv-summary').innerHTML =
    `<strong style="color:${color}">${r.score}/100</strong> ·
     <span class="pill ${r.verdict === 'good' ? 'good' : r.verdict === 'warn' ? 'warn' : 'bad'}">${r.verdict}</span>
     <span style="margin-left:8px">${escape(r.domain)}</span>`;
  const meter = `<div class="spam-meter"><div style="width:${r.score}%;background:${color}"></div></div>`;
  const present = `
    <div class="form-grid-3 mt-12" style="font-size:12px;">
      <div><div class="muted">SPF</div><div>${r.found.spf ? '<span class="pill good">found</span>' : '<span class="pill bad">missing</span>'}</div></div>
      <div><div class="muted">DKIM</div><div>${r.found.dkim.length ? `<span class="pill good">${r.found.dkim.map(d=>d.selector).join(', ')}</span>` : '<span class="pill bad">missing</span>'}</div></div>
      <div><div class="muted">DMARC</div><div>${r.found.dmarc ? '<span class="pill good">found</span>' : '<span class="pill bad">missing</span>'}</div></div>
    </div>`;
  const issues = r.issues.length
    ? r.issues.map(i => `
        <div class="issue">
          <span class="pill ${i.severity === 'high' ? 'bad' : i.severity === 'medium' ? 'warn' : 'info'}">${i.severity}</span>
          <div>
            <div><strong>${escape(i.title)}</strong></div>
            <div class="fix">${escape(i.detail)}</div>
            <pre style="background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:10px 12px;margin-top:6px;font-size:11px;color:var(--text-dim);white-space:pre-wrap;">${escape(i.fix)}</pre>
          </div>
        </div>`).join('')
    : '<div class="muted mt-12">All deliverability checks passing.</div>';
  document.getElementById('dlv-detail').innerHTML = meter + present + issues;
}

async function testSmtp(id) {
  toast('Testing...');
  try {
    const r = await api(`/api/smtp/${id}/test`, {method: 'POST'});
    toast(r.ok ? 'Connected' : 'Failed: ' + r.error, r.ok ? 'good' : 'bad');
    loadSmtps(); refreshAll();
  } catch (e) { toast(e.message, 'bad'); }
}

async function deleteSmtp(id) {
  if (!confirm('Delete this SMTP server?')) return;
  await api(`/api/smtp/${id}`, {method: 'DELETE'});
  loadSmtps(); refreshAll();
}

// ---------- file uploads ----------

function bindDrop(zoneId, inputId, handler) {
  const dz = document.getElementById(zoneId);
  const inp = document.getElementById(inputId);
  dz.addEventListener('click', () => inp.click());
  ['dragover','dragenter'].forEach(ev => dz.addEventListener(ev, e => { e.preventDefault(); dz.classList.add('drag'); }));
  ['dragleave','drop'].forEach(ev => dz.addEventListener(ev, e => { e.preventDefault(); dz.classList.remove('drag'); }));
  dz.addEventListener('drop', e => { if (e.dataTransfer.files[0]) handler(e.dataTransfer.files[0]); });
  inp.addEventListener('change', e => { if (e.target.files[0]) handler(e.target.files[0]); });
}

bindDrop('dz-content', 'file-content', async (file) => {
  document.getElementById('content-filename').textContent = file.name;
  try {
    const r = await apiUpload('/api/upload/content', file);
    if (!document.getElementById('c-subject').value) document.getElementById('c-subject').value = r.subject || '';
    document.getElementById('c-html').value = r.html_body || '';
    document.getElementById('c-text').value = r.text_body || '';
    toast('Content loaded', 'good');
    runSpamCheck();
  } catch (e) { toast(e.message, 'bad'); }
});

bindDrop('dz-recipients', 'file-recipients', async (file) => {
  document.getElementById('recipients-filename').textContent = file.name;
  try {
    const r = await apiUpload('/api/upload/recipients', file);
    parsedRecipients = r.recipients;
    const tb = document.getElementById('recipients-preview');
    if (!parsedRecipients.length) {
      tb.innerHTML = '<tr><td colspan="4" class="muted">No valid emails detected.</td></tr>';
    } else {
      tb.innerHTML = parsedRecipients.slice(0, 10).map(p => `
        <tr><td>${escape(p.email)}</td><td>${escape(p.first_name)}</td><td>${escape(p.last_name)}</td><td>${escape(p.company)}</td></tr>
      `).join('');
    }
    document.getElementById('recipients-summary').innerHTML =
      `<strong>${r.count}</strong> valid recipients parsed. ${r.invalid_count} invalid skipped. (showing first 10)`;
    toast(`${r.count} recipients loaded`, 'good');
  } catch (e) { toast(e.message, 'bad'); }
});

// ---------- spam check ----------

async function runSpamCheck() {
  const body = {
    subject: document.getElementById('c-subject').value,
    html_body: document.getElementById('c-html').value,
    text_body: document.getElementById('c-text').value,
  };
  if (!body.subject && !body.html_body) {
    document.getElementById('spam-summary').textContent = '';
    document.getElementById('spam-detail').innerHTML = '';
    return;
  }
  try {
    const r = await api('/api/spam-check', {
      method: 'POST',
      headers: {'content-type': 'application/json'},
      body: JSON.stringify(body),
    });
    const color = r.verdict === 'good' ? 'var(--good)' : r.verdict === 'warn' ? 'var(--warn)' : 'var(--bad)';
    document.getElementById('spam-summary').innerHTML =
      `Score: <strong style="color:${color}">${r.score}/100</strong>
       <span class="pill ${r.verdict === 'good' ? 'good' : r.verdict === 'warn' ? 'warn' : 'bad'}">${r.verdict}</span>`;
    const meter = `<div class="spam-meter"><div style="width:${r.score}%;background:${color}"></div></div>`;
    const issues = r.issues.length
      ? r.issues.map(i => `
          <div class="issue">
            <span class="pill ${i.severity === 'high' ? 'bad' : i.severity === 'medium' ? 'warn' : 'info'}">${i.severity}</span>
            <div><div>${escape(i.message)}</div><div class="fix">${escape(i.fix)}</div></div>
          </div>`).join('')
      : '<div class="muted mt-12">No issues detected.</div>';
    document.getElementById('spam-detail').innerHTML = meter + issues;
  } catch (e) { toast(e.message, 'bad'); }
}

// ---------- submit campaign ----------

async function submitCampaign(action) {
  const name = document.getElementById('c-name').value.trim();
  const subject = document.getElementById('c-subject').value.trim();
  const html = document.getElementById('c-html').value;
  const text = document.getElementById('c-text').value;
  const sched = document.getElementById('c-schedule').value;

  if (!name) return toast('Campaign name is required', 'bad');
  if (!subject) return toast('Subject is required', 'bad');
  if (!html.trim()) return toast('Email content is required', 'bad');
  if (!parsedRecipients.length) return toast('Upload a recipients file first', 'bad');
  if (action === 'schedule' && !sched) return toast('Pick a date/time to schedule', 'bad');

  const body = {
    name, subject, html_body: html, text_body: text || html.replace(/<[^>]+>/g, ''),
    recipients: parsedRecipients, action,
    scheduled_at: action === 'schedule' ? new Date(sched).toISOString() : null,
  };
  try {
    const r = await api('/api/campaigns', {
      method: 'POST',
      headers: {'content-type': 'application/json'},
      body: JSON.stringify(body),
    });
    toast(`Campaign #${r.id} created (${r.inserted} recipients, status=${r.status})`, 'good');
    document.getElementById('c-name').value = '';
    parsedRecipients = [];
    document.getElementById('recipients-preview').innerHTML = '<tr><td colspan="4" class="muted">No recipients loaded.</td></tr>';
    document.getElementById('recipients-summary').textContent = '';
    document.getElementById('content-filename').textContent = '';
    document.getElementById('recipients-filename').textContent = '';
    switchPage('campaigns');
  } catch (e) { toast(e.message, 'bad'); }
}

// ---------- campaigns list ----------

async function loadCampaigns() {
  const r = await api('/api/campaigns');
  const tb = document.getElementById('camp-tbody');
  if (!r.campaigns.length) {
    tb.innerHTML = '<tr><td colspan="9" class="muted">No campaigns yet.</td></tr>';
    return;
  }
  tb.innerHTML = r.campaigns.map(c => `
    <tr>
      <td>${c.id}</td>
      <td>${escape(c.name)}<div class="muted" style="font-size:11px">${escape(c.subject).slice(0,60)}</div></td>
      <td>${statusPill(c.status)}</td>
      <td class="right">${c.total}</td>
      <td class="right">${c.sent}</td>
      <td class="right">${c.opened}</td>
      <td class="right">${c.failed}</td>
      <td class="muted" style="font-size:12px">${new Date(c.created_at).toLocaleString()}</td>
      <td>${campActions(c)}</td>
    </tr>`).join('');
}

function campActions(c) {
  if (c.status === 'draft' || c.status === 'paused')
    return `<button class="btn small" onclick="campAction(${c.id},'start')">Start</button>`;
  if (c.status === 'running')
    return `<button class="btn small" onclick="campAction(${c.id},'pause')">Pause</button>`;
  if (c.status === 'scheduled')
    return `<span class="muted" style="font-size:12px">at ${new Date(c.scheduled_at).toLocaleString()}</span>`;
  return '';
}

async function campAction(id, action) {
  await api(`/api/campaigns/${id}/${action}`, {method: 'POST'});
  loadCampaigns();
}

// ---------- logs ----------

async function pollLogs() {
  try {
    const r = await api(`/api/logs?after=${lastLogId}`);
    if (r.logs.length) {
      const box = document.getElementById('log-box');
      const atBottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 50;
      r.logs.forEach(l => {
        lastLogId = Math.max(lastLogId, l.id);
        const line = document.createElement('div');
        line.className = 'log-line ' + l.level;
        line.innerHTML = `
          <span class="time">${new Date(l.at).toLocaleTimeString()}</span>
          <span class="lvl">${l.level.toUpperCase()}</span>
          <span class="src">${escape(l.source || '')}</span>
          <span>${escape(l.message)}</span>`;
        box.appendChild(line);
      });
      if (atBottom) box.scrollTop = box.scrollHeight;
    }
  } catch {}
}

function clearLogs() {
  document.getElementById('log-box').innerHTML = '';
}

// ---------- bootstrap ----------

refreshAll();
loadSmtps();
loadCampaigns();
setInterval(refreshAll, 5000);
setInterval(loadCampaigns, 5000);
setInterval(pollLogs, 2000);
pollLogs();
