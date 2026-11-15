/* ============================================================
 * ContextLayer — Content Script
 *
 * Runs automatically on known AI chat sites, and is injected on demand
 * (when the user opens the popup) on any other site. Reads the open
 * conversation with the adaptive scraper (scraper.js, loaded first) and
 * answers the popup. It only reads the page the user is looking at.
 * ============================================================ */

(function () {
  'use strict';

  // Injected twice (manifest + on-demand)? Keep the first instance, unless
  // it belongs to a previous load of the extension (orphaned after an
  // extension reload/update), in which case this new instance takes over.
  if (typeof window.__contextLayerAlive === 'function' && window.__contextLayerAlive()) return;
  window.__contextLayerAlive = () => {
    try {
      return !!(chrome.runtime && chrome.runtime.id);
    } catch (_err) {
      return false;
    }
  };

  const Scraper = window.ContextLayerScraper;
  if (!Scraper) {
    console.error('[ContextLayer] scraper.js not loaded');
    return;
  }

  // --------------- SELECTOR CONFIG (from background) ---------------
  let selectorConfig = null;

  function loadConfig() {
    return new Promise((resolve) => {
      try {
        chrome.runtime.sendMessage({ type: 'GET_SELECTOR_CONFIG' }, (cfg) => {
          if (chrome.runtime.lastError) {
            resolve(null);
            return;
          }
          resolve(cfg || null);
        });
      } catch (_err) {
        resolve(null);
      }
    });
  }

  // --------------- TOKEN COUNTING (approximate) ---------------
  function countTokens(text) {
    if (!text) return 0;
    const chunks = text.split(/[\s,.;:!?()[\]{}"'`\-/\\]+/).filter(Boolean);
    return Math.ceil(chunks.length * 1.3);
  }

  // --------------- COMBINED DATA GETTER ---------------
  function getConversationData() {
    const host = window.location.hostname.replace(/^www\./, '');
    const result = Scraper.scrape(document, host, selectorConfig);
    const site = result.site
      ? { name: result.site.name, domain: result.site.domain, known: true }
      : { name: host || 'This page', domain: host, known: false };
    const contextWindow = (result.site && result.site.contextWindow) || Scraper.DEFAULT_CONTEXT_WINDOW;

    const tokenCount = countTokens(result.messages.map((m) => m.content).join(' '));
    const usage = contextWindow > 0 ? tokenCount / contextWindow : 0;
    const warningLevel = usage > 0.9 ? 'critical' : usage > 0.75 ? 'warning' : 'ok';

    return {
      site,
      messages: result.messages,
      tokenCount,
      contextWindow,
      warningLevel,
      layer: result.layer,
      diagnostics: Object.assign({}, result.diagnostics, {
        extensionVersion: chrome.runtime.getManifest().version,
      }),
    };
  }

  // The page is only read when the popup asks (no background polling).

  // --------------- MESSAGE LISTENER ---------------
  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message.type === 'PING_CONTEXTLAYER') {
      sendResponse({ ok: true });
      return false;
    }

    if (message.type === 'GET_CONVERSATION') {
      // Always scrape fresh when the popup asks (after the config has loaded)
      const respond = () => sendResponse(getConversationData());
      if (selectorConfig) {
        respond();
        return false;
      }
      loadConfig().then((cfg) => {
        selectorConfig = cfg;
        respond();
      });
      return true; // async response
    }

    if (message.type === 'INJECT_HANDOFF') {
      try {
        const ok = injectHandoffNote(message.note);
        sendResponse({ success: ok, error: ok ? undefined : 'Chat input not found' });
      } catch (err) {
        sendResponse({ success: false, error: err.message });
      }
      return false;
    }

    if (message.type === 'SELECTOR_CONFIG_UPDATED') {
      selectorConfig = message.config || selectorConfig;
      return false;
    }

    return false;
  });

  // --------------- HANDOFF INJECTION ---------------
  function injectHandoffNote(note) {
    const host = window.location.hostname.replace(/^www\./, '');
    const site = Scraper.findSite(host, selectorConfig);
    const input = Scraper.findComposer(document, site);
    if (!input) return false;

    input.focus();
    if (input.tagName === 'TEXTAREA' || input.tagName === 'INPUT') {
      const proto = input.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement : window.HTMLInputElement;
      const setter = Object.getOwnPropertyDescriptor(proto.prototype, 'value')?.set;
      if (setter) setter.call(input, note);
      else input.value = note;
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.dispatchEvent(new Event('change', { bubbles: true }));
    } else {
      input.textContent = note;
      input.dispatchEvent(new InputEvent('input', { bubbles: true, data: note }));
    }
    return true;
  }

  // --------------- START ---------------
  loadConfig().then((cfg) => {
    selectorConfig = selectorConfig || cfg;
  });
})();
