/* ============================================================
 * ContextLayer — Popup UI Logic
 * All backend calls wrapped in try/catch, inline error feedback
 * ============================================================ */

// --------------- STATE ---------------
let backendUrl = 'http://localhost:8000';
let isBackendOnline = false;
let currentConversation = [];
let currentSite = null;
let tokenCount = 0;
let contextWindow = 0;

// --------------- DOM REFS ---------------
const $ = (id) => document.getElementById(id);

const dom = {
  statusDot: $('status-dot'),
  bannerOffline: $('banner-offline'),
  tokenLabel: $('token-label'),
  tokenCount: $('token-count'),
  tokenBarFill: $('token-bar-fill'),
  tokenBadge: $('token-badge'),
  modelInfo: $('model-info'),

  btnCompress: $('btn-compress'),
  btnHandoff: $('btn-handoff'),
  btnSave: $('btn-save'),
  btnRecall: $('btn-recall'),
  spinnerCompress: $('spinner-compress'),
  spinnerHandoff: $('spinner-handoff'),
  spinnerSave: $('spinner-save'),
  spinnerRecall: $('spinner-recall'),
  feedbackCompress: $('feedback-compress'),
  feedbackHandoff: $('feedback-handoff'),
  feedbackSave: $('feedback-save'),
  feedbackRecall: $('feedback-recall'),

  sectionHandoff: $('section-handoff'),
  handoffTextarea: $('handoff-textarea'),
  btnCopyHandoff: $('btn-copy-handoff'),
  btnCopyText: $('btn-copy-text'),
  toggleAutoinject: $('toggle-autoinject'),

  sectionRecall: $('section-recall'),
  recallInput: $('recall-input'),
  btnRecallSearch: $('btn-recall-search'),
  recallResults: $('recall-results'),

  btnSettings: $('btn-settings'),
  sectionSettings: $('section-settings'),
  inputBackendUrl: $('input-backend-url'),
  statusOllama: $('status-ollama'),
  statusLlmlingua: $('status-llmlingua'),
  btnClearMemory: $('btn-clear-memory'),
  feedbackSettings: $('feedback-settings'),
};

// --------------- HELPERS ---------------
function showFeedback(el, message, type = 'error') {
  el.textContent = message;
  el.className = `feedback feedback-${type}`;
  // Auto-clear after 5s
  setTimeout(() => {
    el.textContent = '';
    el.className = 'feedback';
  }, 5000);
}

function setSpinner(spinner, visible) {
  spinner.classList.toggle('hidden', !visible);
}

function setButtonLoading(btn, spinner, loading) {
  btn.disabled = loading;
  setSpinner(spinner, loading);
}

function generateSessionId() {
  return 'cl_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2, 8);
}

// --------------- BACKEND CALLS ---------------
async function checkHealth() {
  try {
    const res = await fetch(`${backendUrl}/health`, { signal: AbortSignal.timeout(3000) });
    if (!res.ok) throw new Error('Not OK');
    const data = await res.json();

    isBackendOnline = true;
    dom.statusDot.className = 'status-dot connected';
    dom.bannerOffline.classList.add('hidden');
    dom.btnSave.disabled = false;
    dom.btnRecall.disabled = false;

    // Update service statuses
    if (data.ollama !== undefined) {
      dom.statusOllama.textContent = data.ollama ? 'Online' : 'Offline';
      dom.statusOllama.className = `status-pill ${data.ollama ? 'online' : 'offline'}`;
    }
    if (data.llmlingua !== undefined) {
      dom.statusLlmlingua.textContent = data.llmlingua ? 'Online' : 'Offline';
      dom.statusLlmlingua.className = `status-pill ${data.llmlingua ? 'online' : 'offline'}`;
    }

    return true;
  } catch {
    isBackendOnline = false;
    dom.statusDot.className = 'status-dot disconnected';
    dom.bannerOffline.classList.remove('hidden');
    dom.btnSave.disabled = true;
    // Keep recall button enabled but will show error
    dom.statusOllama.textContent = 'Unknown';
    dom.statusOllama.className = 'status-pill';
    dom.statusLlmlingua.textContent = 'Unknown';
    dom.statusLlmlingua.className = 'status-pill';
    return false;
  }
}

async function fetchFromBackend(endpoint, options = {}) {
  const { timeoutMs = 30000, ...fetchOptions } = options;
  const res = await fetch(`${backendUrl}${endpoint}`, {
    ...fetchOptions,
    headers: {
      'Content-Type': 'application/json',
      ...(fetchOptions.headers || {}),
    },
    signal: AbortSignal.timeout(timeoutMs),
  });
  if (!res.ok) {
    const errBody = await res.text().catch(() => '');
    throw new Error(errBody || `HTTP ${res.status}`);
  }
  return res.json();
}

// --------------- UI UPDATES ---------------
function updateTokenBar() {
  const pct = contextWindow > 0 ? Math.min((tokenCount / contextWindow) * 100, 100) : 0;
  dom.tokenBarFill.style.width = `${pct}%`;
  dom.tokenCount.textContent = `${tokenCount.toLocaleString()} / ${contextWindow.toLocaleString()}`;

  // Warning levels
  dom.tokenBarFill.classList.remove('warning', 'critical');
  if (pct > 90) {
    dom.tokenBarFill.classList.add('critical');
  } else if (pct > 75) {
    dom.tokenBarFill.classList.add('warning');
  }
}

function updateModelInfo() {
  if (currentSite) {
    dom.modelInfo.textContent = `${currentSite.name} • ${currentSite.domain}`;
  } else {
    dom.modelInfo.textContent = 'No LLM site detected';
  }
}

// --------------- LOAD CONVERSATION DATA ---------------
async function loadConversationData() {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.id) return;

    const response = await chrome.tabs.sendMessage(tab.id, { type: 'GET_CONVERSATION' });
    if (response) {
      currentConversation = response.messages || [];
      currentSite = response.site || null;
      tokenCount = response.tokenCount || 0;
      contextWindow = response.contextWindow || 0;
      updateTokenBar();
      updateModelInfo();
    }
  } catch {
    // Content script not injected on this tab
    dom.modelInfo.textContent = 'Navigate to a supported LLM site';
  }
}

// --------------- BUTTON HANDLERS ---------------

// Compress
async function handleCompress() {
  if (currentConversation.length === 0) {
    showFeedback(dom.feedbackCompress, 'No conversation to compress', 'error');
    return;
  }

  setButtonLoading(dom.btnCompress, dom.spinnerCompress, true);
  dom.feedbackCompress.textContent = '';

  try {
    const data = await fetchFromBackend('/compress', {
      method: 'POST',
      body: JSON.stringify({
        messages: currentConversation,
        preserve_last_n: 4,
      }),
    });

    const savedPct = data.saved_percent || data.compression_ratio || 0;
    dom.tokenBadge.textContent = `${Math.round(savedPct)}% saved`;
    dom.tokenBadge.classList.add('visible');
    showFeedback(dom.feedbackCompress, `Compressed! ${Math.round(savedPct)}% tokens saved`, 'success');
  } catch (err) {
    showFeedback(dom.feedbackCompress, `Compress failed: ${err.message}`, 'error');
  } finally {
    setButtonLoading(dom.btnCompress, dom.spinnerCompress, false);
  }
}

// Handoff
async function handleHandoff() {
  if (currentConversation.length === 0) {
    showFeedback(dom.feedbackHandoff, 'No conversation to generate handoff from', 'error');
    return;
  }

  setButtonLoading(dom.btnHandoff, dom.spinnerHandoff, true);
  dom.feedbackHandoff.textContent = '';

  try {
    const sessionId = generateSessionId();
    const data = await fetchFromBackend('/handoff', {
      method: 'POST',
      timeoutMs: 60000,
      body: JSON.stringify({
        messages: currentConversation,
        session_id: sessionId,
      }),
    });

    const note = data.handoff_note || data.note || JSON.stringify(data, null, 2);

    dom.handoffTextarea.value = note;
    dom.sectionHandoff.classList.remove('hidden');

    // Store in chrome.storage.local
    await chrome.storage.local.set({
      cl_handoff_note: note,
      cl_last_session_id: sessionId,
    });

    showFeedback(dom.feedbackHandoff, 'Handoff note generated!', 'success');
  } catch (err) {
    showFeedback(dom.feedbackHandoff, `Handoff failed: ${err.message}`, 'error');
  } finally {
    setButtonLoading(dom.btnHandoff, dom.spinnerHandoff, false);
  }
}

// Save to Memory
async function handleSave() {
  if (!isBackendOnline) {
    showFeedback(dom.feedbackSave, 'Backend is offline', 'error');
    return;
  }
  if (currentConversation.length === 0) {
    showFeedback(dom.feedbackSave, 'No conversation to save', 'error');
    return;
  }

  setButtonLoading(dom.btnSave, dom.spinnerSave, true);
  dom.feedbackSave.textContent = '';

  try {
    const sessionId = generateSessionId();
    const data = await fetchFromBackend('/remember', {
      method: 'POST',
      body: JSON.stringify({
        messages: currentConversation,
        session_id: sessionId,
        source_llm: currentSite?.name || 'Unknown',
      }),
    });

    const factsCount = data.facts_stored || data.count || 0;
    showFeedback(dom.feedbackSave, `${factsCount} facts stored in memory`, 'success');

    await chrome.storage.local.set({ cl_last_session_id: sessionId });
  } catch (err) {
    showFeedback(dom.feedbackSave, `Save failed: ${err.message}`, 'error');
  } finally {
    setButtonLoading(dom.btnSave, dom.spinnerSave, false);
  }
}

// Recall Memory
async function handleRecall() {
  dom.sectionRecall.classList.remove('hidden');
  dom.recallInput.focus();
}

async function handleRecallSearch() {
  const query = dom.recallInput.value.trim();
  if (!query) {
    showFeedback(dom.feedbackRecall, 'Enter a search query', 'error');
    return;
  }

  if (!isBackendOnline) {
    showFeedback(dom.feedbackRecall, 'Backend is offline', 'error');
    return;
  }

  setButtonLoading(dom.btnRecallSearch, dom.spinnerRecall, true);
  dom.recallResults.innerHTML = '';

  try {
    const data = await fetchFromBackend('/recall', {
      method: 'POST',
      body: JSON.stringify({ query }),
    });

    const results = data.results || data.memories || [];
    if (results.length === 0) {
      dom.recallResults.innerHTML = '<div class="recall-item">No memories found for this query.</div>';
    } else {
      dom.recallResults.innerHTML = results
        .map(
          (r) =>
            `<div class="recall-item"><strong>${r.source || 'Memory'}</strong>: ${r.content || r.text || JSON.stringify(r)}</div>`
        )
        .join('');
    }
  } catch (err) {
    showFeedback(dom.feedbackRecall, `Recall failed: ${err.message}`, 'error');
  } finally {
    setButtonLoading(dom.btnRecallSearch, dom.spinnerRecall, false);
  }
}

// Copy Handoff
async function handleCopyHandoff() {
  try {
    await navigator.clipboard.writeText(dom.handoffTextarea.value);
    dom.btnCopyText.textContent = 'Copied!';
    setTimeout(() => {
      dom.btnCopyText.textContent = 'Copy to Clipboard';
    }, 2000);
  } catch {
    showFeedback(dom.feedbackHandoff, 'Failed to copy to clipboard', 'error');
  }
}

// Auto-inject toggle
async function handleAutoInjectToggle() {
  const checked = dom.toggleAutoinject.checked;
  await chrome.storage.local.set({ cl_handoff_pending: checked });
}

// Settings
function toggleSettings() {
  dom.sectionSettings.classList.toggle('hidden');
}

async function handleBackendUrlChange() {
  const url = dom.inputBackendUrl.value.trim();
  if (!url) return;
  backendUrl = url.replace(/\/+$/, ''); // strip trailing slash
  await chrome.storage.local.set({ cl_backend_url: backendUrl });
  await checkHealth();
  showFeedback(dom.feedbackSettings, 'Backend URL updated', 'success');
}

async function handleClearMemory() {
  const confirmed = confirm(
    'Are you sure you want to clear ALL stored memory? This action cannot be undone.'
  );
  if (!confirmed) return;

  try {
    await fetchFromBackend('/memory', { method: 'DELETE' });
    showFeedback(dom.feedbackSettings, 'All memory cleared', 'success');
  } catch (err) {
    showFeedback(dom.feedbackSettings, `Clear failed: ${err.message}`, 'error');
  }
}

// --------------- EVENT BINDINGS ---------------
function bindEvents() {
  dom.btnCompress.addEventListener('click', handleCompress);
  dom.btnHandoff.addEventListener('click', handleHandoff);
  dom.btnSave.addEventListener('click', handleSave);
  dom.btnRecall.addEventListener('click', handleRecall);
  dom.btnRecallSearch.addEventListener('click', handleRecallSearch);
  dom.btnCopyHandoff.addEventListener('click', handleCopyHandoff);
  dom.toggleAutoinject.addEventListener('change', handleAutoInjectToggle);
  dom.btnSettings.addEventListener('click', toggleSettings);
  dom.btnClearMemory.addEventListener('click', handleClearMemory);

  // Save backend URL on blur or Enter
  dom.inputBackendUrl.addEventListener('blur', handleBackendUrlChange);
  dom.inputBackendUrl.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') handleBackendUrlChange();
  });

  // Recall on Enter
  dom.recallInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') handleRecallSearch();
  });
}

// --------------- INIT ---------------
async function init() {
  // Load settings from storage
  const stored = await chrome.storage.local.get([
    'cl_backend_url',
    'cl_handoff_note',
    'cl_handoff_pending',
  ]);

  if (stored.cl_backend_url) {
    backendUrl = stored.cl_backend_url;
    dom.inputBackendUrl.value = backendUrl;
  }

  // Restore handoff note if exists
  if (stored.cl_handoff_note) {
    dom.handoffTextarea.value = stored.cl_handoff_note;
    dom.sectionHandoff.classList.remove('hidden');
  }

  if (stored.cl_handoff_pending) {
    dom.toggleAutoinject.checked = true;
  }

  bindEvents();

  // Check backend health
  await checkHealth();

  // Load conversation data from active tab
  await loadConversationData();
}

document.addEventListener('DOMContentLoaded', init);
