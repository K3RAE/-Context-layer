/* ============================================================
 * ContextLayer — Extension sanity check (used by CI)
 * - manifest.json parses and is Manifest V3
 * - every file the manifest references exists
 * - icons are real PNGs with the declared dimensions
 * - every JS file is syntactically valid
 * ============================================================ */

import { readFileSync, existsSync } from 'fs';
import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';
import { execFileSync } from 'child_process';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const dir = process.argv[2] ? resolve(process.argv[2]) : root;
const errors = [];
const fail = (msg) => errors.push(msg);

let manifest;
try {
  manifest = JSON.parse(readFileSync(resolve(dir, 'manifest.json'), 'utf8'));
} catch (err) {
  console.error(`manifest.json is invalid: ${err.message}`);
  process.exit(1);
}

if (manifest.manifest_version !== 3) fail('manifest_version must be 3');

const referenced = new Set();
if (manifest.background?.service_worker) referenced.add(manifest.background.service_worker);
if (manifest.action?.default_popup) referenced.add(manifest.action.default_popup);
for (const cs of manifest.content_scripts || []) {
  for (const f of [...(cs.js || []), ...(cs.css || [])]) referenced.add(f);
}
const icons = { ...(manifest.icons || {}), ...(manifest.action?.default_icon || {}) };
for (const f of Object.values(icons)) referenced.add(f);

for (const f of referenced) {
  if (!existsSync(resolve(dir, f))) fail(`missing file referenced by manifest: ${f}`);
}

// PNG signature + IHDR width/height
const PNG_SIG = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
for (const [size, f] of Object.entries(icons)) {
  const p = resolve(dir, f);
  if (!existsSync(p)) continue;
  const buf = readFileSync(p);
  if (!buf.subarray(0, 8).equals(PNG_SIG)) {
    fail(`${f} is not a real PNG file`);
    continue;
  }
  const w = buf.readUInt32BE(16);
  const h = buf.readUInt32BE(20);
  if (w !== Number(size) || h !== Number(size)) fail(`${f} is ${w}x${h}, expected ${size}x${size}`);
}

// Syntax-check JS
for (const f of [...referenced].filter((f) => f.endsWith('.js')).concat(['popup.js'])) {
  const p = resolve(dir, f);
  if (!existsSync(p)) continue;
  try {
    execFileSync(process.execPath, ['--check', p], { stdio: 'pipe' });
  } catch (err) {
    fail(`syntax error in ${f}: ${err.stderr?.toString() || err.message}`);
  }
}

if (errors.length) {
  console.error('Extension check FAILED:');
  for (const e of errors) console.error('  - ' + e);
  process.exit(1);
}
console.log(`Extension check passed (${dir})`);
