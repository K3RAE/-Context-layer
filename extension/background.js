/* ============================================================
 * ContextLayer — Background Service Worker (Manifest V3)
 * Handles tab events, handoff injection, and message relay
 * ============================================================ */

// --------------- SUPPORTED SITES ---------------
const SUPPORTED_PATTERNS = [
  'claude.ai',
  'chat.openai.com',
  'gemini.google.com',
  'perplexity.ai',
  'grok.com',
];

function isSupportedSite(url) {
  if (!url) return false;
  try {
    const hostname = new URL(url).hostname.replace(/^www\./, '');
    return SUPPORTED_PATTERNS.some(
      (pattern) => hostname === pattern || hostname.endsWith('.' + pattern)
    );
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
      await checkAndOfferHandoff(tab.id, tab.url);
    }
  } catch {
    // Tab may have been closed
  }
});

chrome.tabs.onUpdated.addListener(async (tabId, changeInfo, tab) => {
  if (changeInfo.status === 'complete' && tab.url && isSupportedSite(tab.url)) {
    await checkAndOfferHandoff(tabId, tab.url);
  }
});

// --------------- HANDOFF INJECTION ---------------
async function checkAndOfferHandoff(tabId, url) {
  try {
    const stored = await chrome.storage.local.get([
      'cl_handoff_pending',
      'cl_handoff_note',
    ]);

    if (!stored.cl_handoff_pending || !stored.cl_handoff_note) return;

    // Inject the handoff banner via scripting API
    await chrome.scripting.executeScript({
      target: { tabId },
      func: injectHandoffBanner,
      args: [stored.cl_handoff_note],
    });
  } catch (err) {
    console.error('[ContextLayer BG] Handoff injection failed:', err.message);
  }
}

// This function is serialized and injected into the page
function injectHandoffBanner(handoffNote) {
  // Don't inject if banner already exists
  if (document.getElementById('cl-handoff-banner')) return;

  const banner = document.createElement('div');
  banner.id = 'cl-handoff-banner';
  banner.style.cssText = `
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    z-index: 2147483647;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 12px;
    padding: 12px 20px;
    background: rgba(10, 10, 15, 0.92);
    backdrop-filter: blur(16px);
    -webkit-backdrop-filter: blur(16px);
    border-bottom: 1px solid rgba(139, 92, 246, 0.3);
    font-family: Inter, -apple-system, BlinkMacSystemFont, sans-serif;
    font-size: 13px;
    color: #e8e8ed;
    animation: clSlideDown 0.3s ease;
  `;

  // Add animation keyframes
  const style = document.createElement('style');
  style.textContent = `
    @keyframes clSlideDown {
      from { transform: translateY(-100%); opacity: 0; }
      to { transform: translateY(0); opacity: 1; }
    }
    @keyframes clSlideUp {
      from { transform: translateY(0); opacity: 1; }
      to { transform: translateY(-100%); opacity: 0; }
    }
  `;
  document.head.appendChild(style);

  const msgSpan = document.createElement('span');
  msgSpan.textContent = '◈ ContextLayer has a handoff note ready. Inject it?';
  msgSpan.style.cssText = 'flex: 1; text-align: center;';

  const btnBase = `
    border: none;
    border-radius: 6px;
    padding: 6px 16px;
    font-family: Inter, sans-serif;
    font-size: 12px;
    font-weight: 600;
    color: #fff;
    cursor: pointer;
    transition: transform 0.2s, filter 0.2s;
  `;

  const injectBtn = document.createElement('button');
  injectBtn.id = 'cl-handoff-inject-btn';
  injectBtn.textContent = 'Inject';
  injectBtn.style.cssText = btnBase + 'background: linear-gradient(135deg, #8b5cf6, #6d28d9); box-shadow: 0 2px 8px rgba(139,92,246,0.3);';

  const dismissBtn = document.createElement('button');
  dismissBtn.id = 'cl-handoff-dismiss-btn';
  dismissBtn.textContent = 'Dismiss';
  dismissBtn.style.cssText = btnBase + 'background: rgba(255,255,255,0.1);';

  function removeBanner() {
    banner.style.animation = 'clSlideUp 0.3s ease forwards';
    setTimeout(() => {
      banner.remove();
      style.remove();
    }, 300);
    // Clear pending flag
    if (typeof chrome !== 'undefined' && chrome.storage) {
      chrome.storage.local.set({
        cl_handoff_pending: false,
        cl_handoff_note: '',
      });
    }
  }

  injectBtn.addEventListener('click', () => {
    // Find the chat input
    const inputSelectors = [
      '[contenteditable="true"].ProseMirror',
      'div[contenteditable="true"]',
      '#prompt-textarea',
      'textarea[data-id="root"]',
      '.ql-editor',
      'textarea[placeholder]',
      'textarea',
    ];

    let input = null;
    for (const sel of inputSelectors) {
      input = document.querySelector(sel);
      if (input) break;
    }

    if (input) {
      if (input.tagName === 'TEXTAREA' || input.tagName === 'INPUT') {
        const nativeSetter =
          Object.getOwnPropertyDescriptor(
            window.HTMLTextAreaElement.prototype,
            'value'
          )?.set ||
          Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype,
            'value'
          )?.set;
        if (nativeSetter) {
          nativeSetter.call(input, handoffNote);
        } else {
          input.value = handoffNote;
        }
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
      } else {
        input.textContent = handoffNote;
        input.dispatchEvent(
          new InputEvent('input', { bubbles: true, data: handoffNote })
        );
      }
    }

    removeBanner();
  });

  dismissBtn.addEventListener('click', removeBanner);

  banner.appendChild(msgSpan);
  banner.appendChild(injectBtn);
  banner.appendChild(dismissBtn);
  document.body.appendChild(banner);
}

// --------------- MESSAGE RELAY ---------------
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  // Content script → Background: cache conversation data
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

  // Popup → Background: get cached conversation data
  if (message.type === 'GET_CONVERSATION_CACHED') {
    sendResponse(cachedConversationData);
    return true;
  }

  return false;
});

// --------------- LIFECYCLE ---------------
console.log('[ContextLayer] Background service worker loaded');
