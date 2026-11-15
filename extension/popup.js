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

  sectionCompress: $('section-compress'),
  compressTextarea: $('compress-textarea'),
  btnCopyCompressed: $('btn-copy-compressed'),

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
  toggleSelectorUpdates: $('toggle-selector-updates'),
  selectorStatus: $('selector-status'),
  btnCheckSelectors: $('btn-check-selectors'),
  btnCopyDiagnostics: $('btn-copy-diagnostics'),
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
    dom.btnCompress.disabled = false;
    dom.btnHandoff.disabled = false;

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
    dom.btnCompress.disabled = true;
    dom.btnHandoff.disabled = true;
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

const LAYER_LABELS = {
  site: 'site rules',
  attributes: 'auto-detected',
  structural: 'auto-detected (layout)',
  none: 'no conversation found',
};

let lastDiagnostics = null;

function updateModelInfo(layer, count) {
  if (!currentSite) {
    dom.modelInfo.textContent = 'Open a chat page, then reopen this popup';
    return;
  }
  const how = LAYER_LABELS[layer] || '';
  const msgs = typeof count === 'number' ? ` • ${count} msgs` : '';
  dom.modelInfo.textContent = `${currentSite.name}${msgs}${how ? ' • ' + how : ''}`;
  dom.modelInfo.title =
    layer === 'none'
      ? 'Could not find chat messages on this page. Use Settings → Copy diagnostics to report it.'
      : `Read using ${how}`;
}

// Ask the tab's content script; if there is none (a site not in the
// manifest), inject the scraper on demand. activeTab grants this for the
// current tab only, because the user opened the popup on it.
async function requestConversation(tab) {
  try {
    return await chrome.tabs.sendMessage(tab.id, { type: 'GET_CONVERSATION' });
  } catch {
    // no content script yet
  }
  if (!/^https?:/.test(tab.url || '')) return null;
  await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    files: ['scraper.js', 'content.js'],
  });
  return chrome.tabs.sendMessage(tab.id, { type: 'GET_CONVERSATION' });
}

// --------------- LOAD CONVERSATION DATA ---------------
async function loadConversationData() {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.id) return;

    const response = await requestConversation(tab);
    if (response) {
      currentConversation = response.messages || [];
      currentSite = response.site || null;
      tokenCount = response.tokenCount || 0;
      contextWindow = response.contextWindow || 0;
      lastDiagnostics = response.diagnostics || null;
      updateTokenBar();
      updateModelInfo(response.layer, currentConversation.length);
    } else {
      dom.modelInfo.textContent = 'ContextLayer cannot read this page';
    }
  } catch {
    // e.g. chrome:// pages, the Web Store, or PDF viewers cannot be scripted
    dom.modelInfo.textContent = 'ContextLayer cannot read this page';
  }
}

// --------------- SELECTOR UPDATES & DIAGNOSTICS ---------------
function describeSelectorStatus(status) {
  if (!status) return 'Never checked';
  const when = status.lastCheck ? new Date(status.lastCheck).toLocaleString() : '';
  const v = status.currentVersion ? `v${status.currentVersion}` : '';
  if (status.result === 'updated') return `Updated to ${v} (${when})`;
  if (status.result === 'up-to-date') return `Up to date, ${v} (${when})`;
  if (status.result === 'disabled') return 'Auto-update off';
  if (status.result === 'error') return `Using ${v || 'bundled'}; last check failed (${when})`;
  return v;
}

async function refreshSelectorStatus() {
  const { cl_selector_status: status, cl_auto_update_selectors: auto } = await chrome.storage.local.get([
    'cl_selector_status',
    'cl_auto_update_selectors',
  ]);
  dom.toggleSelectorUpdates.checked = auto !== false;
  dom.selectorStatus.textContent = describeSelectorStatus(status);
}

async function handleSelectorToggle() {
  await chrome.storage.local.set({ cl_auto_update_selectors: dom.toggleSelectorUpdates.checked });
  await refreshSelectorStatus();
}

async function handleCheckSelectors() {
  dom.btnCheckSelectors.disabled = true;
  try {
    const status = await chrome.runtime.sendMessage({ type: 'CHECK_SELECTOR_UPDATES' });
    dom.selectorStatus.textContent = describeSelectorStatus(status);
    if (status && status.result === 'updated') await loadConversationData();
  } catch (err) {
    showFeedback(dom.feedbackSettings, `Check failed: ${err.message}`, 'error');
  } finally {
    dom.btnCheckSelectors.disabled = false;
  }
}

async function handleCopyDiagnostics() {
  // Counts and selector names only — never message text
  const report = {
    diagnostics: lastDiagnostics,
    userAgent: navigator.userAgent,
    when: new Date().toISOString(),
  };
  try {
    await navigator.clipboard.writeText(JSON.stringify(report, null, 2));
    showFeedback(dom.feedbackSettings, 'Diagnostics copied (no message text included)', 'success');
  } catch {
    showFeedback(dom.feedbackSettings, 'Failed to copy diagnostics', 'error');
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
      // First LLMLingua run may download its model; allow extra time
      timeoutMs: 120000,
      body: JSON.stringify({
        messages: currentConversation,
        preserve_last_n: 4,
      }),
    });

    const savedPct = Math.max(0, Math.round(data.saved_percent || 0));
    dom.tokenBadge.textContent = `${savedPct}% saved`;
    dom.tokenBadge.classList.add('visible');

    // Show the compressed conversation so it can be pasted into a new chat
    const compressedText = (data.compressed_messages || [])
      .map((m) => `${(m.role || 'unknown').toUpperCase()}: ${m.content || ''}`)
      .join('\n\n');
    dom.compressTextarea.value = compressedText;
    dom.sectionCompress.classList.remove('hidden');

    showFeedback(
      dom.feedbackCompress,
      `Compressed ${data.original_tokens} → ${data.compressed_tokens} tokens (${savedPct}% saved, ${data.method})`,
      'success'
    );
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
      timeoutMs: 150000,
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

    const facts = data.facts || [];
    if (facts.length === 0) {
      dom.recallResults.appendChild(makeRecallItem(null, 'No memories found for this query.'));
    } else {
      for (const f of facts) {
        const label = f.source_llm ? `${f.source_llm}` : 'Memory';
        const text = `${f.entity} ${String(f.relation || '').replace(/_/g, ' ')} ${f.target}`;
        dom.recallResults.appendChild(makeRecallItem(label, text));
      }
    }
  } catch (err) {
    showFeedback(dom.feedbackRecall, `Recall failed: ${err.message}`, 'error');
  } finally {
    setButtonLoading(dom.btnRecallSearch, dom.spinnerRecall, false);
  }
}

// Build a recall result row without innerHTML (content comes from web pages)
function makeRecallItem(label, text) {
  const item = document.createElement('div');
  item.className = 'recall-item';
  if (label) {
    const strong = document.createElement('strong');
    strong.textContent = label;
    item.appendChild(strong);
    item.appendChild(document.createTextNode(': '));
  }
  item.appendChild(document.createTextNode(text));
  return item;
}

async function handleCopyCompressed() {
  try {
    await navigator.clipboard.writeText(dom.compressTextarea.value);
    showFeedback(dom.feedbackCompress, 'Compressed conversation copied', 'success');
  } catch {
    showFeedback(dom.feedbackCompress, 'Failed to copy to clipboard', 'error');
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
    dom.recallResults.textContent = '';
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
  dom.btnCopyCompressed.addEventListener('click', handleCopyCompressed);
  dom.toggleAutoinject.addEventListener('change', handleAutoInjectToggle);
  dom.btnSettings.addEventListener('click', toggleSettings);
  dom.btnClearMemory.addEventListener('click', handleClearMemory);
  dom.toggleSelectorUpdates.addEventListener('change', handleSelectorToggle);
  dom.btnCheckSelectors.addEventListener('click', handleCheckSelectors);
  dom.btnCopyDiagnostics.addEventListener('click', handleCopyDiagnostics);

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
  await refreshSelectorStatus();
}

document.addEventListener('DOMContentLoaded', init);
