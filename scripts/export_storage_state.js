#!/usr/bin/env node
'use strict';

const fs = require('node:fs');
const path = require('node:path');
const readline = require('node:readline');
const { createRequire } = require('node:module');

const requireFromHere = createRequire(__filename);
const repoRoot = path.resolve(__dirname, '..');

function usage() {
  console.log(`
Usage:
  node scripts/export_storage_state.js --url <login-or-home-url> --out <state.json>

Options:
  --url     URL to open before manual login.
  --out     Output Playwright storageState JSON path. Default: storage/auth-states/manual.json
  --browser Chrome or Edge executable path. Optional.
`);
}

function arg(name, fallback = '') {
  const index = process.argv.indexOf(name);
  if (index < 0) return fallback;
  return process.argv[index + 1] || fallback;
}

function browserExecutablePath() {
  const configured = arg('--browser') || process.env.MIDSCENE_BROWSER_EXECUTABLE;
  if (configured) return configured;
  const candidates = [
    'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
    'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
    'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
    'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
    '/usr/bin/google-chrome',
    '/usr/bin/chromium',
    '/usr/bin/chromium-browser',
    '/usr/bin/microsoft-edge',
  ];
  return candidates.find((candidate) => fs.existsSync(candidate));
}

async function importPlaywright() {
  try {
    return require('playwright');
  } catch (firstError) {
    const resolved = requireFromHere.resolve('playwright', {
      paths: [repoRoot, path.join(repoRoot, '.midscene-poc')],
    });
    return require(resolved);
  }
}

function waitForEnter() {
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  return new Promise((resolve) => {
    rl.question('Manual login complete? Press Enter to export storageState...', () => {
      rl.close();
      resolve();
    });
  });
}

async function main() {
  if (process.argv.includes('--help') || process.argv.includes('-h')) {
    usage();
    return;
  }
  const url = arg('--url');
  if (!url) {
    usage();
    process.exitCode = 2;
    return;
  }
  const out = path.resolve(arg('--out', path.join('storage', 'auth-states', 'manual.json')));
  const profileDir = path.resolve(
    process.env.TAGENT_EXPORT_PROFILE_DIR ||
      path.join('storage', 'auth-state-export-profile'),
  );
  fs.mkdirSync(path.dirname(out), { recursive: true });
  fs.mkdirSync(profileDir, { recursive: true });

  const { chromium } = await importPlaywright();
  const executablePath = browserExecutablePath();
  const context = await chromium.launchPersistentContext(profileDir, {
    headless: false,
    executablePath,
    viewport: { width: 1366, height: 900 },
  });
  const page = context.pages()[0] || (await context.newPage());
  await page.goto(url, { waitUntil: 'domcontentloaded' });

  console.log(`Opened: ${url}`);
  console.log('Complete username/password login and mobile verification in the browser window.');
  await waitForEnter();

  await context.storageState({ path: out, indexedDB: true });
  await context.close();
  console.log(`Saved storageState: ${out}`);
}

main().catch((error) => {
  console.error(error && error.stack ? error.stack : String(error));
  process.exit(1);
});
