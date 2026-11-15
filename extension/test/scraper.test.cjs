/* Scraper tests (node:test + jsdom).
 *
 * The fixtures are hand-written pages that imitate common chat-UI patterns.
 * They prove the detection layers behave as designed; they do NOT prove the
 * selectors match today's live sites. */

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');

const S = require('../scraper.js');
const bundled = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'selectors.json'), 'utf8'));
const config = S.validateConfig(bundled);

function doc(html) {
  return new JSDOM(`<!doctype html><html><body>${html}</body></html>`).window.document;
}

// ---------------------------------------------------------------------------
// Layer 1: site selectors
// ---------------------------------------------------------------------------

test('bundled selectors.json is valid', () => {
  assert.ok(config, 'validateConfig rejected the bundled file');
  assert.ok(config.sites['claude.ai']);
  assert.ok(config.sites['chatgpt.com']);
});

test('site layer: ChatGPT-style data-message-author-role', () => {
  const d = doc(`
    <main>
      <div data-message-author-role="user">How do I sort a list?</div>
      <div data-message-author-role="assistant">Use <code>sorted()</code>.<button>Copy</button></div>
      <div data-message-author-role="user">Thanks</div>
    </main>`);
  const r = S.scrape(d, 'chatgpt.com', config);
  assert.equal(r.layer, 'site');
  assert.deepEqual(r.messages.map((m) => m.role), ['user', 'assistant', 'user']);
  assert.equal(r.messages[1].content, 'Use sorted() .');
  assert.equal(r.site.name, 'ChatGPT');
});

test('site layer: falls through to a newer candidate selector', () => {
  // old "human-turn" markup is gone; newer data-testid is first in the list
  const d = doc(`
    <main>
      <div data-testid="user-message">Plan my trip</div>
      <div class="font-claude-response">Sure, where to?</div>
    </main>`);
  const r = S.scrape(d, 'claude.ai', config);
  assert.equal(r.layer, 'site');
  assert.equal(r.diagnostics.layers.site.userSelector, '[data-testid="user-message"]');
  assert.equal(r.messages.length, 2);
});

test('subdomains match and www. is ignored', () => {
  assert.equal(S.findSite('www.perplexity.ai', config).name, 'Perplexity');
  assert.equal(S.findSite('foo.chatgpt.com', config).name, 'ChatGPT');
  assert.equal(S.findSite('notchatgpt.com', config), null);
});

// ---------------------------------------------------------------------------
// Layer 2: attribute hints (site markup changed / unknown site)
// ---------------------------------------------------------------------------

test('attributes layer: known site whose selectors broke', () => {
  const d = doc(`
    <nav><div class="user-menu">Account</div><div class="chat-item">Old chat</div></nav>
    <main>
      <div class="msg-row"><div class="UserBubble">What is Rust?</div></div>
      <div class="msg-row"><div class="AssistantBubble">A systems language.</div></div>
      <div class="msg-row"><div class="UserBubble">Is it fast?</div></div>
      <div class="msg-row"><div class="AssistantBubble">Yes.</div></div>
    </main>`);
  const r = S.scrape(d, 'claude.ai', config);
  assert.equal(r.layer, 'attributes');
  assert.deepEqual(
    r.messages.map((m) => `${m.role}:${m.content}`),
    ['user:What is Rust?', 'assistant:A systems language.', 'user:Is it fast?', 'assistant:Yes.']
  );
});

test('attributes layer: nested hints keep the outermost message only', () => {
  const d = doc(`
    <main>
      <article data-role="user"><div class="user-text">Hello</div></article>
      <article data-role="assistant"><div class="model-answer"><p class="response-body">Hi there</p></div></article>
    </main>`);
  const r = S.scrape(d, 'unknown-ai.example', config);
  assert.equal(r.layer, 'attributes');
  assert.equal(r.messages.length, 2);
  assert.equal(r.messages[1].content, 'Hi there');
});

test('attributes layer: ignores UI chrome like avatars and user menus', () => {
  const d = doc(`
    <main>
      <div class="turn"><img class="user-avatar"><div data-author="human">First question</div></div>
      <div class="turn"><span class="bot-icon"></span><div data-author="bot">First answer</div></div>
    </main>`);
  const r = S.scrape(d, 'newchat.example', config);
  assert.deepEqual(r.messages.map((m) => m.content), ['First question', 'First answer']);
});

test('attributes layer: Gemini-style custom elements', () => {
  const d = doc(`
    <main>
      <user-query><span>Translate hola</span></user-query>
      <model-response><div>Hello</div></model-response>
    </main>`);
  const r = S.scrape(d, 'some-gemini-clone.example', config);
  assert.equal(r.layer, 'attributes');
  assert.deepEqual(r.messages.map((m) => m.role), ['user', 'assistant']);
});

// ---------------------------------------------------------------------------
// Layer 3: structural
// ---------------------------------------------------------------------------

test('structural layer: unknown site, no hints, two alternating block styles', () => {
  const d = doc(`
    <div class="app">
      <div class="side"><a>Home</a></div>
      <div class="thread">
        <div class="r a1">Write a haiku about rain</div>
        <div class="l b2">Soft rain on the roof / quiet streets shine like silver / puddles hold the sky</div>
        <div class="r a1">Another one</div>
        <div class="l b2">Grey clouds drift away / the garden drinks and listens / one last drop falls</div>
      </div>
      <div contenteditable="true" aria-label="Write a message"></div>
    </div>`);
  const r = S.scrape(d, 'totally-unknown.example', config);
  assert.equal(r.layer, 'structural');
  assert.deepEqual(r.messages.map((m) => m.role), ['user', 'assistant', 'user', 'assistant']);
  assert.match(r.messages[1].content, /Soft rain/);
});

test('structural layer: identical blocks alternate starting with user', () => {
  const d = doc(`
    <section>
      <div class="m">q1</div><div class="m">a1</div><div class="m">q2</div><div class="m">a2</div>
    </section>
    <textarea></textarea>`);
  const r = S.scrape(d, 'x.example', config);
  assert.equal(r.layer, 'structural');
  assert.deepEqual(r.messages.map((m) => `${m.role}:${m.content}`), ['user:q1', 'assistant:a1', 'user:q2', 'assistant:a2']);
});

test('structural layer: picks the turns, not the paragraphs of one long answer', () => {
  const paras = Array.from({ length: 8 }, (_, i) => `<p>Paragraph ${i} of a long explanation with plenty of words.</p>`).join('');
  const d = doc(`
    <div class="thread">
      <div class="t">How do tides work?</div>
      <div class="t"><div class="body">${paras}</div></div>
      <div class="t">And neap tides?</div>
      <div class="t">They happen at quarter moons.</div>
    </div>
    <textarea placeholder="Send a message"></textarea>`);
  const r = S.scrape(d, 'x.example', config);
  assert.equal(r.layer, 'structural');
  assert.equal(r.messages.length, 4);
  assert.deepEqual(r.messages.map((m) => m.role), ['user', 'assistant', 'user', 'assistant']);
});

test('fallback layers refuse pages without a chat input (not a chat)', () => {
  const d = doc(`
    <ul class="results">
      <li class="row">First search result</li>
      <li class="row">Second search result</li>
      <li class="row">Third search result</li>
    </ul>`);
  const r = S.scrape(d, 'news.example', config);
  assert.equal(r.layer, 'none');
});

test('utility classes are not role hints (Tailwind / Bootstrap)', () => {
  const d = doc(`
    <main>
      <div class="flex self-end me-2" data-role="user">Question one</div>
      <div data-role="assistant">Answer <span class="user-select-none">with code</span> and more</div>
      <div class="flex self-end me-2" data-role="user">Question two</div>
    </main>
    <textarea aria-label="Message"></textarea>`);
  const r = S.scrape(d, 'tailwind-chat.example', config);
  assert.equal(r.layer, 'attributes');
  assert.deepEqual(r.messages.map((m) => m.role), ['user', 'assistant', 'user']);
  assert.match(r.messages[1].content, /with code/);
});

test('aria-label is a usable hint', () => {
  const d = doc(`
    <main>
      <div aria-label="User message">Hi</div>
      <div aria-label="Assistant response">Hello!</div>
    </main>
    <textarea></textarea>`);
  const r = S.scrape(d, 'aria-chat.example', config);
  assert.equal(r.layer, 'attributes');
  assert.equal(r.messages.length, 2);
});

test('data-* names alone are not hints (data-user-id)', () => {
  assert.equal(S.roleHint(doc('<div data-user-id="42"></div>').querySelector('div')), null);
  assert.equal(S.roleHint(doc('<div class="user-select-none"></div>').querySelector('div')), null);
  assert.equal(S.roleHint(doc('<div class="self-end me-2"></div>').querySelector('div')), null);
  assert.equal(S.roleHint(doc('<div data-message-author-role="user"></div>').querySelector('div')), 'user');
  assert.equal(S.roleHint(doc('<div class="font-claude-response"></div>').querySelector('div')), 'assistant');
});

// ---------------------------------------------------------------------------
// Nothing found / partial
// ---------------------------------------------------------------------------

test('empty page returns layer none', () => {
  const r = S.scrape(doc('<main><p>Welcome! Start a new chat.</p></main>'), 'claude.ai', config);
  assert.equal(r.layer, 'none');
  assert.deepEqual(r.messages, []);
  assert.equal(r.diagnostics.layerUsed, 'none');
});

test('new chat with only a user message is still returned from site layer', () => {
  const d = doc('<main><div data-message-author-role="user">First message</div></main>');
  const r = S.scrape(d, 'chatgpt.com', config);
  assert.equal(r.layer, 'site');
  assert.equal(r.messages.length, 1);
});

test('diagnostics contain counts but never message text', () => {
  const d = doc(`<main><div data-message-author-role="user">SECRET-TEXT</div>
                 <div data-message-author-role="assistant">MORE-SECRET</div></main>`);
  const r = S.scrape(d, 'chatgpt.com', config);
  const json = JSON.stringify(r.diagnostics);
  assert.ok(!json.includes('SECRET'));
  assert.equal(r.diagnostics.layers.site.user, 1);
});

// ---------------------------------------------------------------------------
// Composer
// ---------------------------------------------------------------------------

test('findComposer: site selector, then generic fallback', () => {
  const d = doc(`
    <textarea placeholder="Search"></textarea>
    <div contenteditable="true" aria-label="Message the AI"></div>`);
  assert.equal(S.findComposer(d, null).getAttribute('aria-label'), 'Message the AI');
  const d2 = doc('<textarea id="prompt-textarea"></textarea><textarea></textarea>');
  assert.equal(S.findComposer(d2, config.sites['chatgpt.com']).id, 'prompt-textarea');
});

// ---------------------------------------------------------------------------
// Config validation (remote updates)
// ---------------------------------------------------------------------------

test('validateConfig: rejects bad input', () => {
  assert.equal(S.validateConfig('not json'), null);
  assert.equal(S.validateConfig({}), null);
  assert.equal(S.validateConfig({ schema: 2, version: 1, sites: { 'a.com': {} } }), null);
  assert.equal(S.validateConfig({ schema: 1, version: 0, sites: { 'a.com': {} } }), null);
  assert.equal(S.validateConfig('x'.repeat(300 * 1024)), null);
});

test('validateConfig: strips unknown fields and non-string selectors', () => {
  const out = S.validateConfig({
    schema: 1,
    version: 5,
    sites: {
      'a.com': {
        name: 'A',
        user: ['.u', 42, { evil: true }, 'x'.repeat(500)],
        assistant: ['.a'],
        script: 'alert(1)',
        onload: 'steal()',
      },
      'not a domain': { user: ['.x'] },
    },
    code: 'eval("x")',
  });
  assert.deepEqual(Object.keys(out.sites), ['a.com']);
  assert.deepEqual(out.sites['a.com'].user, ['.u']);
  assert.equal(out.sites['a.com'].script, undefined);
  assert.equal(out.code, undefined);
  assert.equal(out.sites['a.com'].contextWindow, S.DEFAULT_CONTEXT_WINDOW);
});

test('validateConfig: optional selector syntax check', () => {
  const out = S.validateConfig(
    { schema: 1, version: 2, sites: { 'a.com': { user: ['.ok', '[[bad'], assistant: [] } } },
    (sel) => {
      try {
        doc('').querySelector(sel);
        return true;
      } catch (_e) {
        return false;
      }
    }
  );
  assert.deepEqual(out.sites['a.com'].user, ['.ok']);
});
