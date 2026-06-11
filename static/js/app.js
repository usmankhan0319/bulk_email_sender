// =====================================================================
// Bulk Email Sender — dashboard logic
// =====================================================================

let parsedRecipients = [];
let lastLogId = 0;
let editingSmtpId = null;     // null = add mode, else editing this id
let editingCampaignId = null; // null = create mode, else editing this campaign

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
  if (name === 'campaign') loadCampaignSmtpChoices();
}

async function loadCampaignSmtpChoices() {
  try {
    const d = await api('/api/smtp');
    const box = document.getElementById('c-smtp-list');
    if (!d.smtps.length) {
      box.innerHTML = '<span class="muted">No SMTP servers yet — add one in the SMTP Servers tab first.</span>';
      return;
    }
    box.innerHTML = d.smtps.map(s => {
      const dis = s.status !== 'active';
      return `<label class="checkbox-row" style="${dis ? 'opacity:0.5' : ''}">
        <input type="checkbox" class="c-smtp-check" value="${s.id}" ${dis ? 'disabled' : ''}>
        <span>${escape(s.name)} <span class="muted">(${escape(s.from_email)})</span> ${statusPill(s.status)}</span>
      </label>`;
    }).join('');
  } catch (e) { /* ignore */ }
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
        const cap = r.effective_limit || r.daily_limit;
        const pct = Math.min(100, Math.round(100 * r.sent_today / Math.max(1, cap)));
        const limitCell = r.warmup_enabled
          ? `${cap} <span class="muted">/ ${r.daily_limit}</span><div class="muted" style="font-size:11px">warm-up day ${r.warmup_day || 1}</div>`
          : `${r.daily_limit}`;
        return `<tr>
          <td>${escape(r.name)}</td>
          <td class="muted">${escape(r.host)}</td>
          <td>${statusPill(r.status)}</td>
          <td class="right">${r.sent_today}</td>
          <td class="right">${limitCell}</td>
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
      <td>${warmupCell(s)}</td>
      <td class="right">${s.daily_limit}</td>
      <td class="right">
        <button class="btn small" onclick="testSmtp(${s.id})">Test</button>
        <button class="btn small" onclick="openSmtpEdit(${s.id})">Edit</button>
        <button class="btn small danger" onclick="deleteSmtp(${s.id})">Delete</button>
      </td>
    </tr>`).join('');
}

function warmupCell(s) {
  if (s.warmup_enabled) {
    const day = s.warmup_day || 1;
    return `<span class="pill info" title="Day ${day} of warm-up">warming · day ${day}</span>
            <button class="btn small ghost" style="margin-top:4px" onclick="toggleWarmup(${s.id}, false)">Disable</button>`;
  }
  return `<button class="btn small" onclick="toggleWarmup(${s.id}, true)">Start warm-up</button>`;
}

async function toggleWarmup(id, enabled) {
  try {
    await api(`/api/smtp/${id}/warmup?enabled=${enabled}`, {method: 'POST'});
    toast(enabled ? 'Warm-up started — ramping gradually' : 'Warm-up disabled', 'good');
    loadSmtps(); refreshAll();
  } catch (e) { toast(e.message, 'bad'); }
}

function openSmtpModal() {
  editingSmtpId = null;
  document.getElementById('smtp-modal-title').textContent = 'Add SMTP Server';
  ['s-name','s-host','s-user','s-pass','s-from-email','s-from-name','s-reply-to'].forEach(id => document.getElementById(id).value = '');
  document.getElementById('s-port').value = 587;
  document.getElementById('s-limit').value = 500;
  document.getElementById('s-tls').checked = true;
  document.getElementById('s-warmup').checked = false;
  document.getElementById('smtp-test-result').textContent = '';
  document.getElementById('smtp-modal').classList.add('show');
}

async function openSmtpEdit(id) {
  try {
    const d = await api('/api/smtp');
    const s = d.smtps.find(x => x.id === id);
    if (!s) return toast('SMTP not found', 'bad');
    editingSmtpId = id;
    document.getElementById('smtp-modal-title').textContent = 'Edit SMTP Server';
    document.getElementById('s-name').value = s.name || '';
    document.getElementById('s-host').value = s.host || '';
    document.getElementById('s-port').value = s.port || 587;
    document.getElementById('s-user').value = s.username || '';
    document.getElementById('s-pass').value = '';   // blank = keep current
    document.getElementById('s-from-email').value = s.from_email || '';
    document.getElementById('s-from-name').value = s.from_name || '';
    document.getElementById('s-reply-to').value = s.reply_to || '';
    document.getElementById('s-limit').value = s.daily_limit || 500;
    document.getElementById('s-tls').checked = !!s.use_tls;
    document.getElementById('s-warmup').checked = !!s.warmup_enabled;
    document.getElementById('smtp-test-result').textContent = '';
    document.getElementById('smtp-modal').classList.add('show');
  } catch (e) { toast(e.message, 'bad'); }
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
      reply_to: document.getElementById('s-reply-to').value.trim(),
      daily_limit: parseInt(document.getElementById('s-limit').value),
      use_tls: document.getElementById('s-tls').checked,
      warmup_enabled: document.getElementById('s-warmup').checked,
    };
    if (!body.name || !body.host || !body.from_email) throw new Error('Name, host and from_email are required');

    const isEdit = editingSmtpId !== null;
    const res = await api(isEdit ? `/api/smtp/${editingSmtpId}` : '/api/smtp', {
      method: isEdit ? 'PUT' : 'POST',
      headers: {'content-type': 'application/json'},
      body: JSON.stringify(body),
    });
    if (res.ok) {
      toast(isEdit ? 'SMTP updated & reconnected' : 'SMTP connected.', 'good');
      closeSmtpModal();
      loadSmtps(); refreshAll();
      if (!isEdit && res.deliverability) {
        const domain = body.from_email.split('@')[1];
        document.getElementById('dlv-input').value = domain;
        renderDlv(res.deliverability);
        switchPage('dashboard');
      }
    } else {
      out.innerHTML = `<span style="color:var(--bad)">${escape(res.error || 'Connection failed')}</span>`;
      toast('Saved but connection failed', 'bad');
      loadSmtps();
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
  const selector = document.getElementById('dlv-selector').value.trim();
  if (!domain) return toast('Enter a domain', 'bad');
  document.getElementById('dlv-summary').textContent = 'Looking up DNS...';
  document.getElementById('dlv-detail').innerHTML = '';
  try {
    let url = `/api/deliverability?domain=${encodeURIComponent(domain)}`;
    if (selector) url += `&selector=${encodeURIComponent(selector)}`;
    const r = await api(url);
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

// ---------- personalization helpers ----------

let lastFocusedField = 'c-html';
['c-html', 'c-text', 'c-subject'].forEach(id => {
  const el = document.getElementById(id);
  if (el) el.addEventListener('focus', () => { lastFocusedField = id; });
});

function insertTag(tag) {
  const el = document.getElementById(lastFocusedField) || document.getElementById('c-html');
  const start = el.selectionStart ?? el.value.length;
  const end = el.selectionEnd ?? el.value.length;
  el.value = el.value.slice(0, start) + tag + el.value.slice(end);
  el.focus();
  el.selectionStart = el.selectionEnd = start + tag.length;
  toast(`Inserted ${tag}`, 'good');
}

async function previewEmail() {
  const subject = document.getElementById('c-subject').value;
  const html = document.getElementById('c-html').value;
  const text = document.getElementById('c-text').value;
  if (!html.trim() && !subject.trim()) return toast('Add content first', 'bad');

  // Use the first uploaded recipient so the client sees a REAL name
  const recipient = parsedRecipients.length ? parsedRecipients[0] : null;
  try {
    const r = await api('/api/preview', {
      method: 'POST',
      headers: {'content-type': 'application/json'},
      body: JSON.stringify({subject, html_body: html, text_body: text, recipient}),
    });
    const who = `${r.recipient.first_name || ''} ${r.recipient.last_name || ''}`.trim() || r.recipient.email;
    let warn = '';
    if (!r.has_personalization) {
      warn = `<div class="issue mt-8"><span class="pill warn">warning</span>
        <div><strong>Koi personalization tag nahi mila</strong>
        <div class="fix">Content mein {{Name}} ya {{first_name}} use karein, warna har email same jayegi.
        Agar naam literal likha hai (jaise "Ethan Whitmore") to woh change nahi hoga.</div></div></div>`;
    } else {
      warn = `<div class="muted mt-8" style="font-size:12px">Tags found: ${r.placeholders_used.map(escape).join(', ')}</div>`;
    }
    document.getElementById('preview-box').innerHTML = `
      <div class="card" style="margin-top:12px;background:var(--bg);">
        <div class="card-header"><h2>Preview — as sent to: ${escape(who)}</h2>
          <div class="hint">first uploaded recipient</div></div>
        <div class="muted" style="font-size:12px;">Subject:</div>
        <div style="margin-bottom:12px;"><strong>${escape(r.subject)}</strong></div>
        <div class="muted" style="font-size:12px;">Body:</div>
        <div style="background:#fff;color:#222;border-radius:8px;padding:16px;">${r.html_body}</div>
        ${warn}
      </div>`;
  } catch (e) { toast(e.message, 'bad'); }
}

// ---------- submit campaign ----------

function selectedSmtpIds() {
  return Array.from(document.querySelectorAll('.c-smtp-check:checked')).map(c => parseInt(c.value));
}

async function submitCampaign(action) {
  const name = document.getElementById('c-name').value.trim();
  const subject = document.getElementById('c-subject').value.trim();
  const html = document.getElementById('c-html').value;
  const text = document.getElementById('c-text').value;
  const sched = document.getElementById('c-schedule').value;
  const gap = parseInt(document.getElementById('c-gap').value) || 0;
  const smtp_ids = selectedSmtpIds();

  if (!name) return toast('Campaign name is required', 'bad');
  if (!subject) return toast('Subject is required', 'bad');
  if (!html.trim()) return toast('Email content is required', 'bad');

  // EDIT MODE — update existing campaign (no recipients re-upload)
  if (editingCampaignId !== null) {
    try {
      await api(`/api/campaigns/${editingCampaignId}`, {
        method: 'PUT',
        headers: {'content-type': 'application/json'},
        body: JSON.stringify({
          name, subject, html_body: html,
          text_body: text || html.replace(/<[^>]+>/g, ''),
          smtp_ids, send_gap_seconds: gap,
        }),
      });
      toast(`Campaign #${editingCampaignId} updated`, 'good');
      resetCampaignForm();
      switchPage('campaigns');
    } catch (e) { toast(e.message, 'bad'); }
    return;
  }

  // CREATE MODE
  if (!parsedRecipients.length) return toast('Upload a recipients file first', 'bad');
  if (action === 'schedule' && !sched) return toast('Pick a date/time to schedule', 'bad');

  const body = {
    name, subject, html_body: html, text_body: text || html.replace(/<[^>]+>/g, ''),
    recipients: parsedRecipients, action,
    scheduled_at: action === 'schedule' ? new Date(sched).toISOString() : null,
    smtp_ids, send_gap_seconds: gap,
  };
  try {
    const r = await api('/api/campaigns', {
      method: 'POST',
      headers: {'content-type': 'application/json'},
      body: JSON.stringify(body),
    });
    toast(`Campaign #${r.id} created (${r.inserted} recipients, status=${r.status})`, 'good');
    resetCampaignForm();
    switchPage('campaigns');
  } catch (e) { toast(e.message, 'bad'); }
}

function resetCampaignForm() {
  editingCampaignId = null;
  ['c-name','c-subject','c-html','c-text','c-schedule'].forEach(id => document.getElementById(id).value = '');
  document.getElementById('c-gap').value = '0';
  parsedRecipients = [];
  document.getElementById('recipients-preview').innerHTML = '<tr><td colspan="4" class="muted">No recipients loaded.</td></tr>';
  document.getElementById('recipients-summary').textContent = '';
  document.getElementById('content-filename').textContent = '';
  document.getElementById('recipients-filename').textContent = '';
  document.getElementById('spam-summary').textContent = '';
  document.getElementById('spam-detail').innerHTML = '';
  const pb = document.getElementById('preview-box');
  if (pb) pb.innerHTML = '';
  document.querySelectorAll('.c-smtp-check').forEach(c => c.checked = false);
}

async function editCampaign(id) {
  try {
    const c = await api(`/api/campaigns/${id}`);
    if (c.status === 'running') return toast('Pause the campaign before editing', 'bad');
    editingCampaignId = id;
    switchPage('campaign');
    document.getElementById('c-name').value = c.name || '';
    document.getElementById('c-subject').value = c.subject || '';
    document.getElementById('c-html').value = c.html_body || '';
    document.getElementById('c-text').value = c.text_body || '';
    document.getElementById('c-gap').value = String(c.send_gap_seconds || 0);
    // restore selected SMTPs after the checkbox list loads
    const ids = (c.smtp_ids || '').split(',').map(x => x.trim()).filter(Boolean);
    setTimeout(() => {
      document.querySelectorAll('.c-smtp-check').forEach(cb => { cb.checked = ids.includes(cb.value); });
    }, 300);
    runSpamCheck();
    toast(`Editing campaign #${id} — recipients stay as-is`, 'info');
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
  let btns = '';
  if (c.status === 'draft' || c.status === 'paused')
    btns += `<button class="btn small" onclick="campAction(${c.id},'start')">Start</button> `;
  if (c.status === 'running')
    btns += `<button class="btn small" onclick="campAction(${c.id},'pause')">Pause</button> `;
  if (c.status === 'scheduled')
    btns += `<span class="muted" style="font-size:12px">at ${new Date(c.scheduled_at).toLocaleString()}</span> `;
  // Edit allowed for anything not actively running
  if (c.status !== 'running')
    btns += `<button class="btn small" onclick="editCampaign(${c.id})">Edit</button> `;
  // Delete always available
  btns += `<button class="btn small danger" onclick="deleteCampaign(${c.id})">Delete</button>`;
  return btns;
}

async function campAction(id, action) {
  await api(`/api/campaigns/${id}/${action}`, {method: 'POST'});
  loadCampaigns();
}

async function deleteCampaign(id) {
  if (!confirm('Delete this campaign and all its recipients? This cannot be undone.')) return;
  try {
    await api(`/api/campaigns/${id}`, {method: 'DELETE'});
    toast(`Campaign #${id} deleted`, 'good');
    loadCampaigns(); refreshAll();
  } catch (e) { toast(e.message, 'bad'); }
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
