'use strict';

// ── STATE ──────────────────────────────────────────────────────────────────────
let currentSite = null;   // { site_id, title, url, page_count, sections }
let sessionId = 'sess_' + Math.random().toString(36).slice(2);
let chatHistory = [];
let suggestedQuestions = [
  'Что это за сайт и чем он занимается?',
  'Какие основные услуги или функции предлагаются?',
  'Как связаться с командой?',
  'Есть ли документация или инструкции?'
];

// ── INIT ───────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  checkAiStatus();
  loadSavedSite();
});

function checkAiStatus() {
  const dot = document.getElementById('aiStatus');
  const txt = document.getElementById('aiStatusText');
  fetch('/api/sites')
    .then(r => {
      if (r.ok) {
        // Check if ollama is available by trying a request
        dot.className = 'status-dot fallback';
        txt.textContent = 'Keyword Search (AI ready)';
      }
    })
    .catch(() => {
      dot.className = 'status-dot';
      txt.textContent = 'Сервер недоступен';
    });
}

function loadSavedSite() {
  const saved = sessionStorage.getItem('currentSite');
  if (saved) {
    try {
      setCurrentSite(JSON.parse(saved));
    } catch(e) {}
  }
}

// ── NAVIGATION ─────────────────────────────────────────────────────────────────
function switchView(name, load = false) {
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n => {
    n.classList.toggle('active', n.dataset.view === name);
  });
  document.getElementById('view-' + name).classList.add('active');

  if (name === 'chat') renderChat();
  if (name === 'sites' || load) loadSites();
}

// ── SCAN ───────────────────────────────────────────────────────────────────────
function setUrl(domain) {
  document.getElementById('urlInput').value = domain;
}

async function startScan() {
  const raw = document.getElementById('urlInput').value.trim();
  if (!raw) return alert('Введите URL сайта');

  const url = raw.startsWith('http') ? raw : 'https://' + raw;

  document.getElementById('btnScan').disabled = true;
  document.getElementById('scanResult').style.display = 'none';
  showScanProgress(url);

  // Animate steps
  const stepDelay = [800, 1200, 900, 600];
  animateSteps(stepDelay);

  try {
    const res = await fetch('/api/scan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url })
    });
    const data = await res.json();

    if (!res.ok) throw new Error(data.detail || 'Ошибка сканирования');

    await delay(3800);
    hideScanProgress();
    showScanResult(data);
    setCurrentSite(data);
  } catch(e) {
    hideScanProgress();
    alert('Ошибка: ' + e.message);
  } finally {
    document.getElementById('btnScan').disabled = false;
  }
}

function showScanProgress(url) {
  const prog = document.getElementById('scanProgress');
  prog.style.display = 'block';
  document.getElementById('progressUrl').textContent = url;
  // Reset steps
  for (let i = 0; i < 4; i++) {
    const el = document.getElementById('ps' + i);
    el.className = 'ps-item';
    el.querySelector('.ps-spinner') && (el.querySelector('.ps-spinner').style.display = 'block');
  }
  document.getElementById('ps0').classList.add('active');
}

async function animateSteps(delays) {
  for (let i = 0; i < 4; i++) {
    if (i > 0) {
      const prev = document.getElementById('ps' + (i-1));
      prev.className = 'ps-item done';
    }
    const cur = document.getElementById('ps' + i);
    cur.className = 'ps-item active';
    await delay(delays[i]);
  }
  const last = document.getElementById('ps3');
  last.className = 'ps-item done';
}

function hideScanProgress() {
  document.getElementById('scanProgress').style.display = 'none';
}

function showScanResult(data) {
  const el = document.getElementById('scanResult');
  el.style.display = 'flex';

  document.getElementById('resultTitle').textContent = data.title;
  document.getElementById('resultUrl').textContent = data.url;

  document.getElementById('resultStats').innerHTML = `
    <div class="stat-item">
      <div class="stat-num">${data.page_count}</div>
      <div class="stat-label">Страниц</div>
    </div>
    <div class="stat-item">
      <div class="stat-num">${data.sections?.length || 0}</div>
      <div class="stat-label">Разделов</div>
    </div>
    ${data.cached ? '<div class="stat-item"><div class="stat-num" style="font-size:13px;color:var(--text2)">Кэш</div><div class="stat-label">Из базы</div></div>' : ''}
  `;

  const sectEl = document.getElementById('resultSections');
  sectEl.innerHTML = (data.sections || []).map(s =>
    `<span class="section-tag">${s}</span>`
  ).join('');
}

function goToChat() {
  switchView('chat');
}

// ── SITE STATE ────────────────────────────────────────────────────────────────
function setCurrentSite(site) {
  currentSite = site;
  sessionStorage.setItem('currentSite', JSON.stringify(site));

  // Update sidebar
  const sc = document.getElementById('sidebarCurrent');
  sc.style.display = 'block';
  document.getElementById('currentSiteName').textContent = site.title || site.url;
  document.getElementById('currentSiteUrl').textContent = site.url;
  document.getElementById('currentSiteMeta').textContent = `${site.page_count} страниц`;

  // Update AI status
  document.getElementById('aiStatus').className = 'status-dot ok';
  document.getElementById('aiStatusText').textContent = 'База готова';
}

// ── CHAT ──────────────────────────────────────────────────────────────────────
function renderChat() {
  if (!currentSite) {
    document.getElementById('chatEmpty').style.display = 'flex';
    document.getElementById('chatActive').style.display = 'none';
    return;
  }

  document.getElementById('chatEmpty').style.display = 'none';
  document.getElementById('chatActive').style.display = 'flex';
  document.getElementById('chatSiteName').textContent = currentSite.title;
  document.getElementById('chatSiteUrl').textContent = currentSite.url;

  document.getElementById('chatWelcome').textContent =
    `Привет! Я изучил сайт "${currentSite.title}" (${currentSite.page_count} страниц). Задавайте любые вопросы — отвечу как консультант!`;

  // Render suggested questions
  const sqEl = document.getElementById('suggestedQs');
  sqEl.innerHTML = suggestedQuestions.map(q =>
    `<button class="sq-btn" onclick="useSuggested('${q.replace(/'/g,"\\'")}')"> ${q}</button>`
  ).join('');

  // Render existing history
  const msgs = document.getElementById('chatMessages');
  msgs.innerHTML = '';
  msgs.appendChild(document.getElementById('chatWelcome'));
  chatHistory.forEach(m => appendMessage(m.role, m.content));
}

function useSuggested(q) {
  document.getElementById('chatInput').value = q;
  document.getElementById('suggestedQs').style.display = 'none';
  sendChat();
}

function handleChatKey(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendChat();
  }
}

function autoResize(el) {
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 140) + 'px';
}

async function sendChat() {
  const input = document.getElementById('chatInput');
  const text = input.value.trim();
  if (!text || !currentSite) return;

  input.value = '';
  input.style.height = 'auto';
  document.getElementById('btnSend').disabled = true;
  document.getElementById('suggestedQs').style.display = 'none';

  appendMessage('user', text);
  chatHistory.push({ role: 'user', content: text });

  const typingId = showTyping();

  try {
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        site_id: currentSite.site_id,
        session_id: sessionId,
        message: text,
        history: chatHistory.slice(-8)
      })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Ошибка');

    removeTyping(typingId);
    appendMessage('bot', data.answer, data.sources);
    chatHistory.push({ role: 'assistant', content: data.answer });
  } catch(e) {
    removeTyping(typingId);
    appendMessage('bot', 'Произошла ошибка: ' + e.message);
  } finally {
    document.getElementById('btnSend').disabled = false;
    input.focus();
  }
}

function appendMessage(role, text, sources = []) {
  const msgs = document.getElementById('chatMessages');
  const div = document.createElement('div');
  div.className = 'msg ' + role;

  const bubble = document.createElement('div');
  bubble.className = 'msg-bubble';
  bubble.textContent = text;
  div.appendChild(bubble);

  if (sources && sources.length > 0) {
    const srcs = document.createElement('div');
    srcs.className = 'msg-sources';
    sources.forEach(s => {
      const a = document.createElement('a');
      a.className = 'source-tag';
      a.href = s.url;
      a.target = '_blank';
      a.textContent = '↗ ' + (s.title || s.url).slice(0, 40);
      srcs.appendChild(a);
    });
    div.appendChild(srcs);
  }

  msgs.appendChild(div);
  msgs.scrollTop = msgs.scrollHeight;
}

let typingCounter = 0;
function showTyping() {
  const id = 'typing_' + (++typingCounter);
  const msgs = document.getElementById('chatMessages');
  const div = document.createElement('div');
  div.className = 'msg bot typing-msg';
  div.id = id;
  div.innerHTML = `<div class="msg-bubble"><div class="typing-dot"></div><div class="typing-dot"></div><div class="typing-dot"></div></div>`;
  msgs.appendChild(div);
  msgs.scrollTop = msgs.scrollHeight;
  return id;
}

function removeTyping(id) {
  const el = document.getElementById(id);
  if (el) el.remove();
}

function clearChat() {
  chatHistory = [];
  sessionId = 'sess_' + Math.random().toString(36).slice(2);
  const msgs = document.getElementById('chatMessages');
  msgs.innerHTML = '';
  msgs.appendChild(document.getElementById('chatWelcome') || (() => {
    const w = document.createElement('div');
    w.id = 'chatWelcome';
    w.className = 'chat-welcome';
    return w;
  })());
  document.getElementById('suggestedQs').style.display = 'flex';
  renderChat();
}

// ── SITES LIST ────────────────────────────────────────────────────────────────
async function loadSites() {
  const grid = document.getElementById('sitesGrid');
  grid.innerHTML = '<div class="loading-text">Загрузка...</div>';

  try {
    const res = await fetch('/api/sites');
    const sites = await res.json();
    if (!sites.length) {
      grid.innerHTML = '<p class="empty-sites">Нет просканированных сайтов. Сканируйте первый!</p>';
      return;
    }

    grid.innerHTML = '';
    sites.forEach(s => {
      const card = document.createElement('div');
      card.className = 'site-card';
      const date = new Date(s.scanned_at).toLocaleDateString('ru-RU');
      card.innerHTML = `
        <div>
          <div class="site-card-title">${esc(s.title || s.url)}</div>
          <div class="site-card-url">${esc(s.url)}</div>
        </div>
        <div class="site-card-meta">
          <div class="site-meta-item">
            <div class="site-meta-num">${s.page_count}</div>
            <div class="site-meta-label">Страниц</div>
          </div>
        </div>
        <div class="site-card-date">Сканирован ${date}</div>
        <div class="site-card-actions">
          <button class="btn-card" onclick="selectSite(${JSON.stringify(s).replace(/"/g,'&quot;')})">Открыть чат</button>
          <button class="btn-card danger" onclick="deleteSite('${s.id}', this)">Удалить</button>
        </div>
      `;
      grid.appendChild(card);
    });
  } catch(e) {
    grid.innerHTML = '<p class="empty-sites">Ошибка загрузки</p>';
  }
}

function selectSite(s) {
  setCurrentSite({ site_id: s.id, title: s.title, url: s.url, page_count: s.page_count, sections: [] });
  chatHistory = [];
  switchView('chat');
}

async function deleteSite(id, btn) {
  if (!confirm('Удалить сайт из базы?')) return;
  btn.disabled = true;
  await fetch('/api/sites/' + id, { method: 'DELETE' });
  if (currentSite?.site_id === id) {
    currentSite = null;
    sessionStorage.removeItem('currentSite');
    document.getElementById('sidebarCurrent').style.display = 'none';
  }
  loadSites();
}

// ── UTILS ──────────────────────────────────────────────────────────────────────
function delay(ms) { return new Promise(r => setTimeout(r, ms)); }
function esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
