/* ============================================================
 * ContextLayer — Background Service Worker (Manifest V3)
 * - Serves the site-selector config (bundled, or a newer validated copy)
 * - Refreshes that config daily from the project repo (data only)
 * - Offers pending handoff notes on chat tabs
 * ============================================================ */

/* global importScripts, ContextLayerScraper */
importScripts('scraper.js');

// --------------- SELECTOR CONFIG ---------------
// Data-only updates: the fetched JSON can only change CSS selectors, names
// and context sizes; it is validated by ContextLayerScraper.validateConfig.
// It cannot add code or change which sites the extension runs on.
const REMOTE_SELECTORS_URL =
  'https://raw.githubusercontent.com/K3RAE/-Context-layer/main/extension/selectors.json';
const UPDATE_ALARM = 'cl-selector-update';
const UPDATE_PERIOD_MINUTES = 24 * 60;
const FETCH_TIMEOUT_MS = 10000;

let bundledConfigPromise = null;

function getBundledConfig() {
  if (!bundledConfigPromise) {
    bundledConfigPromise = fetch(chrome.runtime.getURL('selectors.json'))
      .then((r) => r.text())
      .then((text) => ContextLayerScraper.validateConfig(text))
      .catch((err) => {
        console.error('[ContextLayer] bundled selectors.json invalid:', err);
        return null;
      });
  }
  return bundledConfigPromise;
}

async function getActiveConfig() {
  const bundled = await getBundledConfig();
  const { cl_selector_config: stored, cl_auto_update_selectors: autoUpdate } =
    await chrome.storage.local.get(['cl_selector_config', 'cl_auto_update_selectors']);
  if (autoUpdate !== false && stored) {
    const valid = ContextLayerScraper.validateConfig(stored);
    if (valid && (!bundled || valid.version > bundled.version)) return valid;
  }
  return bundled;
}

async function checkForSelectorUpdate({ force = false } = {}) {
  const { cl_auto_update_selectors: autoUpdate } = await chrome.storage.local.get('cl_auto_update_selectors');
  if (autoUpdate === false && !force) return { status: 'disabled' };

  const current = await getActiveConfig();
  const status = { lastCheck: new Date().toISOString(), currentVersion: current ? current.version : 0 };
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
    const res = await fetch(REMOTE_SELECTORS_URL, {
      cache: 'no-store',
      credentials: 'omit',
      signal: controller.signal,
    });
    clearTimeout(timer);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const text = await res.text();
    const remote = ContextLayerScraper.validateConfig(text);
    if (!remote) throw new Error('remote config failed validation');

    if (!current || remote.version > current.version) {
      await chrome.storage.local.set({ cl_selector_config: remote });
      status.result = 'updated';
      status.currentVersion = remote.version;
      broadcastConfig(remote);
    } else {
      status.result = 'up-to-date';
    }
  } catch (err) {
    // Keep using the bundled / last good copy
    status.result = 'error';
    status.error = String(err && err.message ? err.message : err).slice(0, 200);
  }
  await chrome.storage.local.set({ cl_selector_status: status });
  return status;
}

async function broadcastConfig(config) {
  try {
    const tabs = await chrome.tabs.query({});
    for (const tab of tabs) {
      chrome.tabs.sendMessage(tab.id, { type: 'SELECTOR_CONFIG_UPDATED', config }).catch(() => {});
    }
  } catch (_err) {
    // ignore
  }
}

function scheduleUpdates() {
  chrome.alarms.create(UPDATE_ALARM, { delayInMinutes: 1, periodInMinutes: UPDATE_PERIOD_MINUTES });
}

chrome.runtime.onInstalled.addListener(scheduleUpdates);
chrome.runtime.onStartup.addListener(scheduleUpdates);
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === UPDATE_ALARM) checkForSelectorUpdate();
});

// --------------- AUTO-INJECTED SITES (must match manifest content_scripts) ---------------
const SUPPORTED_PATTERNS = [
  'claude.ai',
  'chatgpt.com',
  'chat.openai.com',
  'gemini.google.com',
  'perplexity.ai',
  'grok.com',
];

function isSupportedSite(url) {
  if (!url) return false;
  try {
    const hostname = new URL(url).hostname.replace(/^www\./, '');
    return SUPPORTED_PATTERNS.some((p) => hostname === p || hostname.endsWith('.' + p));
  } catch {
    return false;
  }
}

// --------------- CONVERSATION DATA CACHE ---------------
let cachedConversationData = null;

// --------------- TAB EVENT LISTENERS ---------------
chrome.tabs.onActivated.addListener(async (activeInfo) => {
  try {
    const tab = await chrome.tabs.get(activeInfo.tabId);
    if (tab.url && isSupportedSite(tab.url) && tab.status === 'complete') {
      await checkAndOfferHandoff(tab.id);
    }
  } catch {
    // Tab may have been closed
  }
});

chrome.tabs.onUpdated.addListener(async (tabId, changeInfo, tab) => {
  if (changeInfo.status === 'complete' && tab.url && isSupportedSite(tab.url)) {
    await checkAndOfferHandoff(tabId);
  }
});

// --------------- HANDOFF BANNER ---------------
async function checkAndOfferHandoff(tabId) {
  try {
    const stored = await chrome.storage.local.get(['cl_handoff_pending', 'cl_handoff_note']);
    if (!stored.cl_handoff_pending || !stored.cl_handoff_note) return;
    await chrome.scripting.executeScript({
      target: { tabId },
      func: injectHandoffBanner,
      args: [stored.cl_handoff_note],
    });
  } catch (err) {
    console.error('[ContextLayer BG] Handoff injection failed:', err.message);
  }
}

// Serialized and run in the page's isolated world (same world as content.js,
// so window.ContextLayerScraper is available there).
function injectHandoffBanner(handoffNote) {
  if (document.getElementById('cl-handoff-banner')) return;

  const banner = document.createElement('div');
  banner.id = 'cl-handoff-banner';
  banner.style.cssText = `
    position: fixed; top: 0; left: 0; right: 0; z-index: 2147483647;
    display: flex; align-items: center; justify-content: center; gap: 12px;
    padding: 12px 20px; background: rgba(10, 10, 15, 0.92);
    backdrop-filter: blur(16px); -webkit-backdrop-filter: blur(16px);
    border-bottom: 1px solid rgba(139, 92, 246, 0.3);
    font-family: Inter, -apple-system, BlinkMacSystemFont, sans-serif;
    font-size: 13px; color: #e8e8ed;`;

  const msgSpan = document.createElement('span');
  msgSpan.textContent = '◈ ContextLayer has a handoff note ready. Inject it?';
  msgSpan.style.cssText = 'flex: 1; text-align: center;';

  const btnBase = `border: none; border-radius: 6px; padding: 6px 16px;
    font-family: Inter, sans-serif; font-size: 12px; font-weight: 600;
    color: #fff; cursor: pointer;`;

  const injectBtn = document.createElement('button');
  injectBtn.textContent = 'Inject';
  injectBtn.style.cssText = btnBase + 'background: linear-gradient(135deg, #8b5cf6, #6d28d9);';

  const dismissBtn = document.createElement('button');
  dismissBtn.textContent = 'Dismiss';
  dismissBtn.style.cssText = btnBase + 'background: rgba(255,255,255,0.1);';

  function removeBanner() {
    banner.remove();
    chrome.storage.local.set({ cl_handoff_pending: false, cl_handoff_note: '' });
  }

  function findInput() {
    const S = window.ContextLayerScraper;
    if (S) return S.findComposer(document, null);
    return document.querySelector('textarea, [contenteditable="true"]');
  }

  injectBtn.addEventListener('click', () => {
    const input = findInput();
    if (input) {
      input.focus();
      if (input.tagName === 'TEXTAREA' || input.tagName === 'INPUT') {
        const proto = input.tagName === 'TEXTAREA' ? HTMLTextAreaElement : HTMLInputElement;
        const setter = Object.getOwnPropertyDescriptor(proto.prototype, 'value')?.set;
        if (setter) setter.call(input, handoffNote);
        else input.value = handoffNote;
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
      } else {
        input.textContent = handoffNote;
        input.dispatchEvent(new InputEvent('input', { bubbles: true, data: handoffNote }));
      }
    }
    removeBanner();
  });
  dismissBtn.addEventListener('click', removeBanner);

  banner.append(msgSpan, injectBtn, dismissBtn);
  document.body.appendChild(banner);
}

// --------------- MESSAGE RELAY ---------------
chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message.type === 'GET_SELECTOR_CONFIG') {
    getActiveConfig().then(sendResponse);
    return true;
  }

  if (message.type === 'CHECK_SELECTOR_UPDATES') {
    checkForSelectorUpdate({ force: true }).then(sendResponse);
    return true;
  }

  if (message.type === 'CONVERSATION_DATA') {
    cachedConversationData = {
      messages: message.messages,
      tokenCount: message.tokenCount,
      contextWindow: message.contextWindow,
      site: message.site,
      warningLevel: message.warningLevel,
      timestamp: Date.now(),
    };
    return false;
  }

  if (message.type === 'GET_CONVERSATION_CACHED') {
    sendResponse(cachedConversationData);
    return false;
  }

  return false;
});

console.log('[ContextLayer] Background service worker loaded');
