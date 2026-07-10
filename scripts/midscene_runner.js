#!/usr/bin/env node
'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { createRequire } = require('node:module');

const requireFromHere = createRequire(__filename);
const repoRoot = path.resolve(__dirname, '..');

function log(...args) {
  console.error('[midscene-runner]', ...args);
}

function loadDotEnv(file) {
  if (!fs.existsSync(file)) return;
  const text = fs.readFileSync(file, 'utf8');
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith('#')) continue;
    const eq = line.indexOf('=');
    if (eq <= 0) continue;
    const key = line.slice(0, eq).trim();
    let value = line.slice(eq + 1).trim();
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }
    if (process.env[key] === undefined) process.env[key] = value;
  }
}

async function importFromProject(specifier) {
  try {
    return await import(specifier);
  } catch (firstError) {
    const searchPaths = [repoRoot, path.join(repoRoot, '.midscene-poc')];
    try {
      const resolved = requireFromHere.resolve(specifier, { paths: searchPaths });
      return await import(pathToFileURL(resolved).href);
    } catch (secondError) {
      secondError.message = `${secondError.message}\nInitial import error: ${firstError.message}`;
      throw secondError;
    }
  }
}

function readStdin() {
  return new Promise((resolve, reject) => {
    let data = '';
    process.stdin.setEncoding('utf8');
    process.stdin.on('data', (chunk) => {
      data += chunk;
    });
    process.stdin.on('end', () => resolve(data));
    process.stdin.on('error', reject);
  });
}

function pickModelConfig(payloadConfig = {}) {
  const name =
    process.env.MIDSCENE_MODEL_NAME ||
    payloadConfig.MIDSCENE_MODEL_NAME ||
    payloadConfig.modelName ||
    (process.env.LLM_MODEL || '').replace(/^openai\//, '');
  const baseURL =
    process.env.MIDSCENE_MODEL_BASE_URL ||
    payloadConfig.MIDSCENE_MODEL_BASE_URL ||
    payloadConfig.baseURL ||
    process.env.LLM_API_BASE ||
    '';
  const apiKey =
    process.env.MIDSCENE_MODEL_API_KEY ||
    payloadConfig.MIDSCENE_MODEL_API_KEY ||
    payloadConfig.apiKey ||
    process.env.LLM_API_KEY ||
    '';
  const family =
    process.env.MIDSCENE_MODEL_FAMILY ||
    payloadConfig.MIDSCENE_MODEL_FAMILY ||
    payloadConfig.family ||
    (name.includes('qwen3') ? 'qwen3' : '');

  return {
    MIDSCENE_MODEL_NAME: name,
    MIDSCENE_MODEL_BASE_URL: baseURL,
    MIDSCENE_MODEL_API_KEY: apiKey,
    MIDSCENE_MODEL_FAMILY: family,
  };
}

function browserExecutablePath() {
  if (process.env.MIDSCENE_BROWSER_EXECUTABLE) return process.env.MIDSCENE_BROWSER_EXECUTABLE;
  const candidates = [
    'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
    'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
    'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
    'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  ];
  return candidates.find((candidate) => fs.existsSync(candidate));
}

async function screenshot(page, artifactDir, name) {
  const file = path.join(artifactDir, name);
  await page.screenshot({ path: file, fullPage: true });
  return file;
}

async function run() {
  loadDotEnv(path.join(repoRoot, '.env'));
  loadDotEnv(path.join(repoRoot, '.midscene-poc', '.env'));

  const input = await readStdin();
  const payload = JSON.parse(input || '{}');
  const artifactDir = path.resolve(payload.artifact_dir || path.join(repoRoot, 'storage', 'midscene'));
  fs.mkdirSync(artifactDir, { recursive: true });

  const modelConfig = pickModelConfig(payload.model_config || {});
  const missing = Object.entries(modelConfig)
    .filter(([key, value]) => key !== 'MIDSCENE_MODEL_FAMILY' && !value)
    .map(([key]) => key);
  if (missing.length > 0) {
    throw new Error(`Missing Midscene model config: ${missing.join(', ')}`);
  }

  const [{ chromium }, { PlaywrightAgent }] = await Promise.all([
    importFromProject('playwright'),
    importFromProject('@midscene/web/playwright'),
  ]);

  const executablePath = browserExecutablePath();
  const browser = await chromium.launch({
    headless: process.env.MIDSCENE_HEADLESS !== '0',
    executablePath,
    args: ['--no-sandbox', '--disable-setuid-sandbox'],
  });

  const phaseResults = [];
  const actions = [];
  const artifacts = { artifact_dir: artifactDir };
  let agent;
  let stopReason = 'completed';

  try {
    const page = await browser.newPage({
      viewport: {
        width: Number(process.env.MIDSCENE_VIEWPORT_WIDTH || 1280),
        height: Number(process.env.MIDSCENE_VIEWPORT_HEIGHT || 720),
      },
      deviceScaleFactor: 1,
    });

    if (payload.base_url) {
      await page.goto(payload.base_url, { waitUntil: 'domcontentloaded' });
    }

    const reportFileName = path.join(artifactDir, 'midscene-report.html');
    agent = new PlaywrightAgent(page, {
      modelConfig,
      reportFileName,
      generateReport: process.env.MIDSCENE_GENERATE_REPORT !== '0',
      autoPrintReportMsg: false,
      waitAfterAction: Number(process.env.MIDSCENE_WAIT_AFTER_ACTION_MS || 500),
      waitForNetworkIdleTimeout: Number(process.env.MIDSCENE_NETWORK_IDLE_TIMEOUT_MS || 1000),
      waitForNavigationTimeout: Number(process.env.MIDSCENE_NAVIGATION_TIMEOUT_MS || 3000),
      aiActContext: process.env.MIDSCENE_AI_ACT_CONTEXT || payload.spec?.intent || '',
    });
    artifacts.report = reportFileName;
    artifacts.initial_screenshot = await screenshot(page, artifactDir, 'initial.png');

    const phases = payload.spec?.phases || [];
    for (const [phaseIndex, phase] of phases.entries()) {
      const steps = phase.steps || [];
      const expected = phase.expected || '';
      const startedAt = Date.now();
      try {
        for (const [stepIndex, step] of steps.entries()) {
          log(`phase ${phaseIndex + 1}, step ${stepIndex + 1}: ${step}`);
          await agent.aiAct(step);
          actions.push({ phase_index: phaseIndex, step_index: stepIndex, instruction: step, status: 'done' });
        }
        if (expected) {
          log(`phase ${phaseIndex + 1}, assert: ${expected}`);
          await agent.aiAssert(expected);
        }
        const shot = await screenshot(page, artifactDir, `phase-${phaseIndex + 1}.png`);
        phaseResults.push({
          phase_index: phaseIndex,
          status: 'pass',
          expected,
          reason: 'Midscene aiAct/aiAssert completed',
          evidence: shot,
          query: { duration_ms: Date.now() - startedAt },
        });
      } catch (error) {
        stopReason = 'phase_failed';
        const shot = await screenshot(page, artifactDir, `phase-${phaseIndex + 1}-failed.png`).catch(() => '');
        phaseResults.push({
          phase_index: phaseIndex,
          status: 'fail',
          expected,
          reason: error && error.message ? error.message : String(error),
          evidence: shot,
          query: { duration_ms: Date.now() - startedAt },
        });
        break;
      }
    }

    return {
      passed: phaseResults.length === phases.length && phaseResults.every((item) => item.status === 'pass'),
      stop_reason: stopReason,
      phase_results: phaseResults,
      actions,
      artifacts,
    };
  } finally {
    if (agent && typeof agent.destroy === 'function') {
      await agent.destroy().catch((error) => log('agent.destroy failed:', error.message));
    }
    await browser.close();
  }
}

run()
  .then((result) => {
    process.stdout.write(`${JSON.stringify(result)}\n`);
  })
  .catch((error) => {
    const result = {
      passed: false,
      stop_reason: 'runner_exception',
      phase_results: [],
      actions: [],
      artifacts: {},
      error: error && error.stack ? error.stack : String(error),
    };
    process.stdout.write(`${JSON.stringify(result)}\n`);
  });