/* ============================================================
 * ContextLayer — esbuild Build Config
 * Bundles content.js, popup.js, background.js and copies static assets
 * ============================================================ */

import { build } from 'esbuild';
import { cpSync, mkdirSync, existsSync, rmSync } from 'fs';
import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const isProduction =
  process.env.NODE_ENV === 'production' || process.argv.includes('--production');
const outdir = resolve(__dirname, 'dist');

// Start from a clean dist directory
rmSync(outdir, { recursive: true, force: true });
mkdirSync(outdir, { recursive: true });

async function buildAll() {
  console.log(`\n⚡ ContextLayer build (${isProduction ? 'production' : 'development'})\n`);

  // 1. Bundle content.js → dist/content.js (IIFE, runs in page context)
  await build({
    entryPoints: [resolve(__dirname, 'content.js')],
    bundle: true,
    outfile: resolve(outdir, 'content.js'),
    format: 'iife',
    platform: 'browser',
    target: ['chrome110'],
    minify: isProduction,
    sourcemap: !isProduction,
    logLevel: 'info',
  });
  console.log('  ✔ content.js bundled');

  // 2. Bundle popup.js → dist/popup.js (IIFE, runs in popup context)
  await build({
    entryPoints: [resolve(__dirname, 'popup.js')],
    bundle: true,
    outfile: resolve(outdir, 'popup.js'),
    format: 'iife',
    platform: 'browser',
    target: ['chrome110'],
    minify: isProduction,
    sourcemap: !isProduction,
    logLevel: 'info',
  });
  console.log('  ✔ popup.js bundled');

  // 3. Bundle background.js → dist/background.js (classic worker: uses importScripts)
  await build({
    entryPoints: [resolve(__dirname, 'background.js')],
    bundle: true,
    outfile: resolve(outdir, 'background.js'),
    format: 'iife',
    platform: 'browser',
    target: ['chrome110'],
    minify: isProduction,
    sourcemap: !isProduction,
    logLevel: 'info',
  });
  console.log('  ✔ background.js bundled');

  // 4. Copy static assets to dist/
  // scraper.js is loaded by importScripts / executeScript by name, so it is copied as-is
  const staticFiles = ['manifest.json', 'popup.html', 'styles.css', 'scraper.js', 'selectors.json'];
  for (const file of staticFiles) {
    const src = resolve(__dirname, file);
    const dest = resolve(outdir, file);
    if (existsSync(src)) {
      cpSync(src, dest, { force: true });
      console.log(`  ✔ ${file} copied`);
    } else {
      console.warn(`  ⚠ ${file} not found, skipping`);
    }
  }

  // 5. Copy icons/ directory
  const iconsDir = resolve(__dirname, 'icons');
  const iconsOutDir = resolve(outdir, 'icons');
  if (existsSync(iconsDir)) {
    cpSync(iconsDir, iconsOutDir, { recursive: true, force: true });
    console.log('  ✔ icons/ copied');
  } else {
    mkdirSync(iconsOutDir, { recursive: true });
    console.warn('  ⚠ icons/ directory not found — created empty placeholder');
  }

  console.log(`\n✅ Build complete → ${outdir}\n`);
}

buildAll().catch((err) => {
  console.error('❌ Build failed:', err);
  process.exit(1);
});
