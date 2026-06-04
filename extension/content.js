/* ============================================================
 * ContextLayer — Content Script
 * Centralized DOM selectors + conversation scraper for 5 LLM sites
 * ============================================================ */

// --------------- CENTRALIZED SITE CONFIG ---------------
const SITE_CONFIG = {
  'claude.ai': {
    name: 'Claude',
    contextWindow: 200000,
    userSelector: '[data-testid="human-turn"]',
    assistantSelector: '[data-testid="ai-turn"]',
  },
  'chat.openai.com': {
    name: 'ChatGPT',
    contextWindow: 128000,
    userSelector: '[data-message-author-role="user"]',
    assistantSelector: '[data-message-author-role="assistant"]',
  },
  'gemini.google.com': {
    name: 'Gemini',
    contextWindow: 1000000,
    userSelector: '.user-query-text',
    assistantSelector: '.model-response-text',
  },
  'perplexity.ai': {
    name: 'Perplexity',
    contextWindow: 128000,
    userSelector: '.user-message',
    assistantSelector: '.answer-text',
  },
  'grok.com': {
    name: 'Grok',
    contextWindow: 131072,
    userSelector: '[data-testid="user-message"]',
    assistantSelector: '[data-testid="assistant-message"]',
  },
};

// --------------- SITE DETECTION ---------------
function detectSite() {
  const hostname = window.location.hostname.replace(/^www\./, '');
  for (const [domain, config] of Object.entries(SITE_CONFIG)) {
    if (hostname === domain || hostname.endsWith('.' + domain)) {
      return { domain, ...config };
    }
  }
  return null;
}

// --------------- CONVERSATION SCRAPER ---------------
function scrapeConversation(siteConfig) {
  const messages = [];

  // Tag each element with its role so we can sort by DOM order
  const tagged = [];

  // Scrape user elements — isolated try/catch so assistant scraping still runs
  try {
    const userEls = document.querySelectorAll(siteConfig.userSelector);
    userEls.forEach((el) => {
      try {
        tagged.push({ role: 'user', el });
      } catch (elErr) {
        console.warn(`[ContextLayer] Skipping user element on ${siteConfig.name}:`, elErr.message);
      }
    });
  } catch (err) {
    console.error(`[ContextLayer] User selector failed for ${siteConfig.name}:`, err.message);
  }

  // Scrape assistant elements — isolated try/catch so user data is preserved
  try {
    const assistantEls = document.querySelectorAll(siteConfig.assistantSelector);
    assistantEls.forEach((el) => {
      try {
        tagged.push({ role: 'assistant', el });
      } catch (elErr) {
        console.warn(`[ContextLayer] Skipping assistant element on ${siteConfig.name}:`, elErr.message);
      }
    });
  } catch (err) {
    console.error(`[ContextLayer] Assistant selector failed for ${siteConfig.name}:`, err.message);
  }

  // Sort by DOM order using compareDocumentPosition
  try {
    tagged.sort((a, b) => {
      const pos = a.el.compareDocumentPosition(b.el);
      if (pos & Node.DOCUMENT_POSITION_FOLLOWING) return -1;
      if (pos & Node.DOCUMENT_POSITION_PRECEDING) return 1;
      return 0;
    });
  } catch (sortErr) {
    console.warn(`[ContextLayer] DOM sort failed for ${siteConfig.name}:`, sortErr.message);
  }

  // Extract text content — per-element try/catch so one bad node doesn't skip all
  for (const item of tagged) {
    try {
      const content = (item.el.textContent || '').trim();
      if (content.length > 0) {
        messages.push({ role: item.role, content });
      }
    } catch (extractErr) {
      console.warn(`[ContextLayer] Text extraction failed for ${item.role} element:`, extractErr.message);
    }
  }

  return messages;
}

// --------------- TOKEN COUNTING (approximate) ---------------
function countTokens(text) {
  if (!text || text.length === 0) return 0;
  // Split on whitespace and common punctuation boundaries
  const chunks = text.split(/[\s,.;:!?()[\]{}"'`\-/\\]+/).filter(Boolean);
  // Multiply by 1.3 to approximate sub-word tokenization
  return Math.ceil(chunks.length * 1.3);
}

// --------------- COMBINED DATA GETTER ---------------
function getConversationData() {
  const site = detectSite();
  if (!site) {
    return {
      site: null,
      messages: [],
      tokenCount: 0,
      contextWindow: 0,
      warningLevel: 'ok',
    };
  }

  const messages = scrapeConversation(site);
  const fullText = messages.map((m) => m.content).join(' ');
  const tokenCount = countTokens(fullText);
  const contextWindow = site.contextWindow;
  const usagePercent = contextWindow > 0 ? tokenCount / contextWindow : 0;

  let warningLevel = 'ok';
  if (usagePercent > 0.9) {
    warningLevel = 'critical';
  } else if (usagePercent > 0.75) {
    warningLevel = 'warning';
  }

  return {
    site: { name: site.name, domain: site.domain },
    messages,
    tokenCount,
    contextWindow,
    warningLevel,
  };
}

// --------------- MUTATION OBSERVER (debounced) ---------------
let scrapeTimeout = null;
let lastConversationData = null;

function debouncedScrape() {
  if (scrapeTimeout) clearTimeout(scrapeTimeout);
  scrapeTimeout = setTimeout(() => {
    const data = getConversationData();
    lastConversationData = data;

    // Notify background with fresh data
    try {
      chrome.runtime.sendMessage({
        type: 'CONVERSATION_DATA',
        ...data,
      });
    } catch (err) {
      // Extension context may be invalidated — ignore
    }

    // Fire console warning if approaching limit
    if (data.warningLevel === 'warning') {
      console.warn(
        `[ContextLayer] ⚠️ Token usage at ${Math.round(
          (data.tokenCount / data.contextWindow) * 100
        )}% of ${data.site?.name} context window (${data.tokenCount.toLocaleString()} / ${data.contextWindow.toLocaleString()})`
      );
    } else if (data.warningLevel === 'critical') {
      console.error(
        `[ContextLayer] 🚨 Token usage at ${Math.round(
          (data.tokenCount / data.contextWindow) * 100
        )}% — approaching ${data.site?.name} context limit!`
      );
    }
  }, 2000);
}

// Observe DOM changes
const observer = new MutationObserver(debouncedScrape);
observer.observe(document.body, {
  childList: true,
  subtree: true,
});

// Initial scrape
debouncedScrape();

// --------------- MESSAGE LISTENER ---------------
chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message.type === 'GET_CONVERSATION') {
    // Return cached data or do a fresh scrape
    const data = lastConversationData || getConversationData();
    lastConversationData = data;
    sendResponse(data);
    return true; // keep channel open for async
  }

  if (message.type === 'INJECT_HANDOFF') {
    try {
      injectHandoffNote(message.note);
      sendResponse({ success: true });
    } catch (err) {
      sendResponse({ success: false, error: err.message });
    }
    return true;
  }

  return false;
});

// --------------- HANDOFF INJECTION ---------------
function injectHandoffNote(note) {
  const site = detectSite();
  if (!site) return;

  // Common chat input selectors per site
  const inputSelectors = {
    'claude.ai': '[contenteditable="true"].ProseMirror, div[contenteditable="true"]',
    'chat.openai.com': '#prompt-textarea, textarea[data-id="root"]',
    'gemini.google.com': '.ql-editor, div[contenteditable="true"]',
    'perplexity.ai': 'textarea[placeholder], div[contenteditable="true"]',
    'grok.com': 'textarea, div[contenteditable="true"]',
  };

  const selector = inputSelectors[site.domain];
  if (!selector) return;

  const input = document.querySelector(selector);
  if (!input) return;

  if (input.tagName === 'TEXTAREA' || input.tagName === 'INPUT') {
    const nativeSetter = Object.getOwnPropertyDescriptor(
      window.HTMLTextAreaElement.prototype,
      'value'
    )?.set || Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype,
      'value'
    )?.set;
    if (nativeSetter) {
      nativeSetter.call(input, note);
    } else {
      input.value = note;
    }
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
  } else {
    // contenteditable
    input.textContent = note;
    input.dispatchEvent(new InputEvent('input', { bubbles: true, data: note }));
  }
}

console.log('[ContextLayer] Content script loaded for', detectSite()?.name || 'unknown site');
