/* ============================================================
 * ContextLayer — Adaptive conversation scraper
 *
 * Reads the chat the user has open, on any AI chat site, and keeps working
 * when sites change their HTML. Three layers, tried in order; a layer's
 * result is only accepted if it looks like a real conversation:
 *
 *   1. "site"        Per-site CSS selectors from selectors.json. Each role
 *                    has several candidates (newest markup first). The
 *                    list is refreshed from the project repo (data only).
 *   2. "attributes"  Generic detection from role hints in the markup:
 *                    class names, data-* attributes, aria-labels such as
 *                    "user-message", "assistant", "model-response", ...
 *   3. "structural"  Finds the container whose children are a repeated
 *                    run of similar blocks (the message list) and assigns
 *                    roles from hints or alternation.
 *
 * Pure DOM code, no extension APIs: shared by the content script, the
 * background worker (config validation) and the Node test-suite.
 * Nothing here reads anything but the page it runs in.
 * ============================================================ */

(function (root) {
  'use strict';

  const CONFIG_SCHEMA_VERSION = 1;
  const MAX_CONFIG_BYTES = 200 * 1024;
  const MAX_SELECTOR_LENGTH = 300;
  const MAX_SELECTORS_PER_LIST = 12;
  const MAX_SITES = 200;
  const MAX_ELEMENTS_SCANNED = 20000;
  const DEFAULT_CONTEXT_WINDOW = 128000;

  const USER_TOKENS = new Set([
    'user', 'human', 'you', 'me', 'mine', 'self', 'query', 'question', 'prompt',
    'request', 'outgoing', 'sent', 'author',
  ]);
  const ASSISTANT_TOKENS = new Set([
    'assistant', 'bot', 'ai', 'model', 'answer', 'response', 'reply', 'llm',
    'agent', 'incoming', 'received', 'gpt', 'chatgpt', 'claude', 'gemini',
    'copilot', 'grok', 'mistral', 'llama', 'deepseek', 'qwen', 'perplexity',
  ]);
  // Tokens that look like hints but describe UI chrome, not messages.
  const NOISE_TOKENS = new Set([
    'avatar', 'icon', 'name', 'menu', 'button', 'btn', 'input', 'composer',
    'settings', 'profile', 'account', 'tooltip', 'badge', 'label', 'action',
    'actions', 'toolbar', 'feedback', 'copy', 'edit', 'sidebar', 'nav',
  ]);
  const EXCLUDED_ZONES =
    'nav, header, footer, aside, form, button, select, textarea, script, style, noscript, ' +
    'template, [contenteditable="true"], [role="navigation"], [role="banner"], ' +
    '[role="contentinfo"], [role="complementary"], [aria-hidden="true"], [hidden]';
  const TEXT_SKIP =
    'script, style, noscript, button, svg, template, [aria-hidden="true"], [hidden], .sr-only';

  // --------------------------------------------------------------------------
  // Text helpers
  // --------------------------------------------------------------------------

  function getText(el) {
    if (!el) return '';
    const doc = el.ownerDocument;
    const NodeFilter_ = (doc.defaultView && doc.defaultView.NodeFilter) || root.NodeFilter;
    const walker = doc.createTreeWalker(el, NodeFilter_.SHOW_TEXT);
    const parts = [];
    let node;
    while ((node = walker.nextNode())) {
      // skip text inside buttons, icons, hidden helpers, ... within this element
      const skipper = node.parentElement && node.parentElement.closest(TEXT_SKIP);
      if (skipper && skipper !== el && el.contains(skipper)) continue;
      parts.push(node.nodeValue);
    }
    return parts.join(' ').replace(/[ \t\f\v\r]+/g, ' ').replace(/\s*\n\s*/g, '\n').trim();
  }

  function tokenize(str) {
    return String(str || '')
      .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
      .toLowerCase()
      .split(/[^a-z0-9]+/)
      .filter(Boolean);
  }

  /** Classify an element by the role hints in its own attributes. */
  function roleHint(el) {
    const tokens = [];
    tokens.push(...tokenize(el.getAttribute('class')));
    tokens.push(...tokenize(el.tagName));
    for (const attr of el.attributes) {
      const name = attr.name.toLowerCase();
      if (name === 'class' || name === 'style' || name === 'href' || name === 'src') continue;
      if (name.startsWith('data-') || name === 'role' || name === 'aria-label' || name === 'id') {
        tokens.push(...tokenize(name.replace(/^data-/, '')));
        if (attr.value.length <= 80) tokens.push(...tokenize(attr.value));
      }
    }
    if (tokens.some((t) => NOISE_TOKENS.has(t))) return null;
    const isUser = tokens.some((t) => USER_TOKENS.has(t));
    const isAssistant = tokens.some((t) => ASSISTANT_TOKENS.has(t));
    if (isUser && !isAssistant) return 'user';
    if (isAssistant && !isUser) return 'assistant';
    // e.g. data-message-author-role="user": attribute value wins over name
    for (const attr of el.attributes) {
      const v = attr.value.toLowerCase();
      if (/^(user|human)$/.test(v)) return 'user';
      if (/^(assistant|bot|ai|model)$/.test(v)) return 'assistant';
    }
    return null;
  }

  function inExcludedZone(el) {
    return !!(el.closest && el.closest(EXCLUDED_ZONES));
  }

  function domOrder(a, b) {
    if (a === b) return 0;
    const pos = a.compareDocumentPosition(b);
    // eslint-disable-next-line no-bitwise
    if (pos & 4) return -1; // b follows a
    // eslint-disable-next-line no-bitwise
    if (pos & 2) return 1;
    return 0;
  }

  /** Keep only outermost elements of each role and drop containers that hold both roles. */
  function dedupe(items) {
    const byRole = { user: [], assistant: [] };
    for (const it of items) byRole[it.role].push(it.el);
    const all = items.map((i) => i.el);

    const kept = items.filter(({ el, role }) => {
      // nested inside another element of the same role -> drop (keep outermost)
      for (const other of byRole[role]) {
        if (other !== el && other.contains(el)) return false;
      }
      // contains an element of the opposite role -> it is a container, drop
      const opposite = role === 'user' ? byRole.assistant : byRole.user;
      for (const other of opposite) {
        if (el.contains(other)) return false;
      }
      return true;
    });
    // an element of one role nested inside one of the other role: keep outer only
    return kept.filter(({ el }) => !all.some((o) => o !== el && o.contains(el) && kept.some((k) => k.el === o)));
  }

  function toMessages(items) {
    items.sort((a, b) => domOrder(a.el, b.el));
    const out = [];
    for (const { el, role } of items) {
      const content = getText(el);
      if (content) out.push({ role, content });
    }
    return out;
  }

  function isPlausible(messages, { requireBothRoles = true } = {}) {
    if (!messages || messages.length === 0) return false;
    const users = messages.filter((m) => m.role === 'user').length;
    const assistants = messages.length - users;
    if (requireBothRoles) return users >= 1 && assistants >= 1;
    return messages.length >= 1;
  }

  function countRoles(messages) {
    const user = messages.filter((m) => m.role === 'user').length;
    return { user, assistant: messages.length - user };
  }

  // --------------------------------------------------------------------------
  // Layer 1: per-site selectors
  // --------------------------------------------------------------------------

  function safeQueryAll(doc, selector) {
    try {
      return Array.from(doc.querySelectorAll(selector));
    } catch (_err) {
      return null; // invalid selector
    }
  }

  function firstMatching(doc, selectors) {
    for (const sel of selectors || []) {
      const found = safeQueryAll(doc, sel);
      if (found && found.length) return { selector: sel, elements: found };
    }
    return { selector: null, elements: [] };
  }

  function scrapeSite(doc, site) {
    if (!site) return { messages: [], detail: { matched: false } };
    const users = firstMatching(doc, site.user);
    const assistants = firstMatching(doc, site.assistant);
    const items = [
      ...users.elements.map((el) => ({ el, role: 'user' })),
      ...assistants.elements.map((el) => ({ el, role: 'assistant' })),
    ];
    const messages = toMessages(dedupe(items));
    return {
      messages,
      detail: {
        matched: true,
        userSelector: users.selector,
        assistantSelector: assistants.selector,
        userCount: users.elements.length,
        assistantCount: assistants.elements.length,
      },
    };
  }

  // --------------------------------------------------------------------------
  // Layer 2: attribute / role-hint heuristic
  // --------------------------------------------------------------------------

  function scrapeAttributes(doc) {
    const scope = doc.querySelector('main, [role="main"]') || doc.body;
    if (!scope) return { messages: [], detail: { candidates: 0 } };
    const items = [];
    const all = scope.getElementsByTagName('*');
    const limit = Math.min(all.length, MAX_ELEMENTS_SCANNED);
    for (let i = 0; i < limit; i++) {
      const el = all[i];
      const role = roleHint(el);
      if (!role) continue;
      if (inExcludedZone(el)) continue;
      items.push({ el, role });
    }
    const messages = toMessages(dedupe(items));
    return { messages, detail: { candidates: items.length } };
  }

  // --------------------------------------------------------------------------
  // Layer 3: structural heuristic (repeated sibling blocks)
  // --------------------------------------------------------------------------

  function signature(el) {
    const classes = tokenize(el.getAttribute('class'))
      .filter((t) => !/\d/.test(t) && t.length > 1) // drop hashed / generated classes
      .sort()
      .slice(0, 4)
      .join('.');
    return el.tagName.toLowerCase() + (classes ? '.' + classes : '');
  }

  function deepRoleHint(el, depth = 3) {
    const own = roleHint(el);
    if (own) return own;
    if (depth === 0) return null;
    for (const child of el.children) {
      const r = deepRoleHint(child, depth - 1);
      if (r) return r;
    }
    return null;
  }

  function scrapeStructural(doc) {
    const scope = doc.querySelector('main, [role="main"]') || doc.body;
    if (!scope) return { messages: [], detail: { container: null } };

    let best = null;
    const all = [scope, ...scope.getElementsByTagName('*')];
    const limit = Math.min(all.length, MAX_ELEMENTS_SCANNED);
    for (let i = 0; i < limit; i++) {
      const container = all[i];
      if (container.children.length < 2 || inExcludedZone(container)) continue;
      const textual = Array.from(container.children).filter(
        (c) => !c.matches(EXCLUDED_ZONES) && getText(c).length > 0
      );
      if (textual.length < 2) continue;

      const groups = new Map();
      for (const c of textual) {
        const sig = signature(c);
        groups.set(sig, (groups.get(sig) || 0) + 1);
      }
      const top = Array.from(groups.entries()).sort((a, b) => b[1] - a[1]).slice(0, 2);
      const topSigs = new Set(top.map(([s]) => s));
      const members = textual.filter((c) => topSigs.has(signature(c)));
      // the message list should be mostly made of those blocks, and repeat
      if (members.length < 2 || members.length / textual.length < 0.6) continue;
      if (top[0][1] < 2 && members.length < 3) continue;

      const avgLen = members.reduce((n, c) => n + getText(c).length, 0) / members.length;
      // depth bonus: prefer the most specific container on ties
      let depth = 0;
      for (let p = container; p && p !== scope; p = p.parentElement) depth++;
      const score = members.length * Math.log(1 + avgLen) + depth * 0.01;
      if (!best || score > best.score) {
        best = { container, members, sigs: top.map(([s]) => s), score };
      }
    }

    if (!best) return { messages: [], detail: { container: null } };

    const items = best.members.map((el, idx) => {
      let role = deepRoleHint(el);
      if (!role) {
        if (best.sigs.length === 2 && best.sigs[0] !== best.sigs[1]) {
          // two block styles: whichever style comes first in the list is the user
          const firstSig = signature(best.members[0]);
          role = signature(el) === firstSig ? 'user' : 'assistant';
        } else {
          role = idx % 2 === 0 ? 'user' : 'assistant';
        }
      }
      return { el, role };
    });
    const messages = toMessages(items);
    return {
      messages,
      detail: {
        container: signature(best.container),
        blocks: best.members.length,
      },
    };
  }

  // --------------------------------------------------------------------------
  // Site lookup & composer
  // --------------------------------------------------------------------------

  function findSite(hostname, config) {
    const host = String(hostname || '').toLowerCase().replace(/^www\./, '');
    const sites = (config && config.sites) || {};
    // longest matching domain first (e.g. "huggingface.co/chat" style keys are not supported)
    const keys = Object.keys(sites).sort((a, b) => b.length - a.length);
    for (const domain of keys) {
      if (host === domain || host.endsWith('.' + domain)) {
        return Object.assign({ domain }, sites[domain]);
      }
    }
    return null;
  }

  function isVisibleish(el) {
    if (el.closest('[hidden], [aria-hidden="true"]')) return false;
    if (el.disabled || el.readOnly) return false;
    const style = el.getAttribute('style') || '';
    return !/display\s*:\s*none|visibility\s*:\s*hidden/i.test(style);
  }

  /** Find the chat input box to paste a handoff note into. */
  function findComposer(doc, site) {
    if (site && site.composer) {
      for (const sel of site.composer) {
        const found = safeQueryAll(doc, sel);
        const usable = found && found.filter(isVisibleish);
        if (usable && usable.length) return usable[usable.length - 1];
      }
    }
    const candidates = Array.from(
      doc.querySelectorAll('textarea, [contenteditable="true"], [contenteditable=""], [role="textbox"]')
    ).filter(isVisibleish);
    if (!candidates.length) return null;
    const hinted = candidates.filter((el) =>
      /message|ask|prompt|chat|type|send|question|anything/i.test(
        [el.getAttribute('placeholder'), el.getAttribute('aria-label'), el.getAttribute('data-placeholder'), el.id]
          .filter(Boolean)
          .join(' ')
      )
    );
    const pool = hinted.length ? hinted : candidates;
    return pool[pool.length - 1]; // chat inputs are almost always last in the DOM
  }

  // --------------------------------------------------------------------------
  // Main entry point
  // --------------------------------------------------------------------------

  /**
   * @returns {{messages: Array<{role:string,content:string}>, layer: string,
   *            site: object|null, diagnostics: object}}
   */
  function scrape(doc, hostname, config) {
    const site = findSite(hostname, config);
    const diagnostics = {
      host: String(hostname || '').replace(/^www\./, ''),
      knownSite: !!site,
      configVersion: (config && config.version) || 0,
      layers: {},
    };

    const siteResult = scrapeSite(doc, site);
    diagnostics.layers.site = Object.assign(countRoles(siteResult.messages), siteResult.detail);
    if (isPlausible(siteResult.messages)) {
      return { messages: siteResult.messages, layer: 'site', site, diagnostics: done(diagnostics, 'site') };
    }

    const attr = scrapeAttributes(doc);
    diagnostics.layers.attributes = Object.assign(countRoles(attr.messages), attr.detail);
    if (isPlausible(attr.messages)) {
      return { messages: attr.messages, layer: 'attributes', site, diagnostics: done(diagnostics, 'attributes') };
    }

    const struct = scrapeStructural(doc);
    diagnostics.layers.structural = Object.assign(countRoles(struct.messages), struct.detail);
    if (isPlausible(struct.messages)) {
      return { messages: struct.messages, layer: 'structural', site, diagnostics: done(diagnostics, 'structural') };
    }

    // A site layer that found only one role (e.g. a brand-new chat) is still useful
    if (isPlausible(siteResult.messages, { requireBothRoles: false })) {
      return { messages: siteResult.messages, layer: 'site', site, diagnostics: done(diagnostics, 'site') };
    }
    return { messages: [], layer: 'none', site, diagnostics: done(diagnostics, 'none') };
  }

  function done(diagnostics, layer) {
    diagnostics.layerUsed = layer;
    return diagnostics;
  }

  // --------------------------------------------------------------------------
  // Config validation (remote updates are data only)
  // --------------------------------------------------------------------------

  function cleanSelectorList(value) {
    if (!Array.isArray(value)) return [];
    return value
      .filter((s) => typeof s === 'string' && s.trim() && s.length <= MAX_SELECTOR_LENGTH)
      .map((s) => s.trim())
      .slice(0, MAX_SELECTORS_PER_LIST);
  }

  /**
   * Validate and sanitize a selectors config. Returns a clean copy or null.
   * Accepts a JSON string or an object. Only known fields survive; every
   * selector must be a short string. `isValidSelector` (optional) can be
   * passed to reject selectors the browser cannot parse.
   */
  function validateConfig(input, isValidSelector) {
    let data = input;
    try {
      if (typeof input === 'string') {
        if (input.length > MAX_CONFIG_BYTES) return null;
        data = JSON.parse(input);
      }
    } catch (_err) {
      return null;
    }
    if (!data || typeof data !== 'object' || Array.isArray(data)) return null;
    if (data.schema !== CONFIG_SCHEMA_VERSION) return null;
    if (!Number.isInteger(data.version) || data.version < 1) return null;
    if (!data.sites || typeof data.sites !== 'object' || Array.isArray(data.sites)) return null;

    const sites = {};
    const entries = Object.entries(data.sites).slice(0, MAX_SITES);
    for (const [domain, cfg] of entries) {
      if (!/^[a-z0-9.-]+\.[a-z]{2,}$/i.test(domain) || !cfg || typeof cfg !== 'object') continue;
      const clean = {
        name: typeof cfg.name === 'string' ? cfg.name.slice(0, 60) : domain,
        contextWindow:
          Number.isInteger(cfg.contextWindow) && cfg.contextWindow > 0 && cfg.contextWindow <= 100000000
            ? cfg.contextWindow
            : DEFAULT_CONTEXT_WINDOW,
        user: cleanSelectorList(cfg.user),
        assistant: cleanSelectorList(cfg.assistant),
        composer: cleanSelectorList(cfg.composer),
      };
      if (isValidSelector) {
        for (const key of ['user', 'assistant', 'composer']) {
          clean[key] = clean[key].filter((s) => isValidSelector(s));
        }
      }
      sites[domain.toLowerCase()] = clean;
    }
    if (!Object.keys(sites).length) return null;
    return {
      schema: CONFIG_SCHEMA_VERSION,
      version: data.version,
      updated: typeof data.updated === 'string' ? data.updated.slice(0, 40) : '',
      sites,
    };
  }

  const api = {
    scrape,
    scrapeSite,
    scrapeAttributes,
    scrapeStructural,
    findSite,
    findComposer,
    validateConfig,
    getText,
    roleHint,
    DEFAULT_CONTEXT_WINDOW,
    CONFIG_SCHEMA_VERSION,
  };

  root.ContextLayerScraper = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
