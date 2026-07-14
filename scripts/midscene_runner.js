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
  const reuseLLMConfig = process.env.MIDSCENE_REUSE_LLM_CONFIG === '1';
  const name =
    process.env.MIDSCENE_MODEL_NAME ||
    payloadConfig.MIDSCENE_MODEL_NAME ||
    payloadConfig.modelName ||
    (reuseLLMConfig ? (process.env.LLM_MODEL || '').replace(/^openai\//, '') : '');
  const baseURL =
    process.env.MIDSCENE_MODEL_BASE_URL ||
    payloadConfig.MIDSCENE_MODEL_BASE_URL ||
    payloadConfig.baseURL ||
    (reuseLLMConfig ? process.env.LLM_API_BASE : '') ||
    '';
  const apiKey =
    process.env.MIDSCENE_MODEL_API_KEY ||
    payloadConfig.MIDSCENE_MODEL_API_KEY ||
    payloadConfig.apiKey ||
    (reuseLLMConfig ? process.env.LLM_API_KEY : '') ||
    '';
  const family =
    process.env.MIDSCENE_MODEL_FAMILY ||
    payloadConfig.MIDSCENE_MODEL_FAMILY ||
    payloadConfig.family ||
    inferModelFamily(name);

  return {
    MIDSCENE_MODEL_NAME: name,
    MIDSCENE_MODEL_BASE_URL: baseURL,
    MIDSCENE_MODEL_API_KEY: apiKey,
    MIDSCENE_MODEL_FAMILY: family,
  };
}

function inferModelFamily(modelName = '') {
  const name = String(modelName).toLowerCase();
  if (name.includes('qwen3.6')) return 'qwen3.6';
  if (name.includes('qwen3.5')) return 'qwen3.5';
  if (name.includes('qwen3-vl') || name.includes('qwen3vl')) return 'qwen3-vl';
  if (name.includes('qwen3')) return 'qwen3';
  if (name.includes('qwen2.5-vl') || name.includes('qwen2_5_vl')) return 'qwen2.5-vl';
  if (name.includes('doubao')) return 'doubao-vision';
  if (name.includes('gemini')) return 'gemini';
  if (name.includes('glm')) return 'glm-v';
  if (name.includes('kimi')) return 'kimi';
  return '';
}

function applyModelConfigToEnv(modelConfig) {
  for (const [key, value] of Object.entries(modelConfig)) {
    if (value) process.env[key] = String(value);
  }
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

  const input = (await readStdin()).replace(/^\uFEFF/, '');
  const payload = JSON.parse(input || '{}');
  const artifactDir = path.resolve(payload.artifact_dir || path.join(repoRoot, 'storage', 'midscene'));
  fs.mkdirSync(artifactDir, { recursive: true });
  process.env.MIDSCENE_RUN_DIR =
    process.env.MIDSCENE_RUN_DIR || path.join(artifactDir, 'midscene_run');

  const modelConfig = pickModelConfig(payload.model_config || {});
  applyModelConfigToEnv(modelConfig);
  const missing = Object.entries(modelConfig)
    .filter(([, value]) => !value)
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
  const progressFile = path.join(artifactDir, 'midscene-result.json');
  let agent;
  let stopReason = 'completed';

  function writeProgress(extra = {}) {
    const snapshot = {
      passed: false,
      stop_reason: stopReason,
      phase_results: phaseResults,
      actions,
      artifacts,
      ...extra,
    };
    fs.writeFileSync(progressFile, `${JSON.stringify(snapshot)}\n`, 'utf8');
  }

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

    const reportFileName = 'midscene-report.html';
    agent = new PlaywrightAgent(page, {
      modelConfig,
      reportFileName,
      generateReport: process.env.MIDSCENE_GENERATE_REPORT !== '0',
      autoPrintReportMsg: false,
      waitAfterAction: Number(process.env.MIDSCENE_WAIT_AFTER_ACTION_MS || 500),
      waitForNetworkIdleTimeout: Number(process.env.MIDSCENE_NETWORK_IDLE_TIMEOUT_MS || 1000),
      waitForNavigationTimeout: Number(process.env.MIDSCENE_NAVIGATION_TIMEOUT_MS || 3000),
      aiActContext:
        process.env.MIDSCENE_AI_ACT_CONTEXT ||
        [payload.spec?.intent || '', payload.execution_context || ''].filter(Boolean).join('\n\n'),
    });
    artifacts.report = path.join(process.env.MIDSCENE_RUN_DIR, 'report', reportFileName);
    artifacts.initial_screenshot = await screenshot(page, artifactDir, 'initial.png');
    writeProgress();

    const phases = payload.spec?.phases || [];
    for (const [phaseIndex, phase] of phases.entries()) {
      const steps = phase.steps || [];
      const expected = phase.expected || '';
      const startedAt = Date.now();
      try {
        const instruction = buildPhaseInstruction(phaseIndex, steps, expected);
        if (instruction) {
          log(`phase ${phaseIndex + 1}, act: ${instruction}`);
          await agent.aiAct(instruction);
          actions.push({
            phase_index: phaseIndex,
            step_index: 0,
            instruction,
            status: 'done',
          });
        }
        if (expected) {
          const assertInstruction = buildAssertInstruction(expected, page.url());
          log(`phase ${phaseIndex + 1}, assert: ${assertInstruction}`);
          await agent.aiAssert(assertInstruction);
        }
        const shot = await screenshot(page, artifactDir, `phase-${phaseIndex + 1}.png`);
        phaseResults.push({
          phase_index: phaseIndex,
          status: 'pass',
          expected,
          reason: 'Midscene aiAct/aiAssert completed',
          evidence: shot,
          query: { duration_ms: Date.now() - startedAt, url: page.url() },
        });
        writeProgress();
      } catch (error) {
        stopReason = 'phase_failed';
        const shot = await screenshot(page, artifactDir, `phase-${phaseIndex + 1}-failed.png`).catch(() => '');
        phaseResults.push({
          phase_index: phaseIndex,
          status: 'fail',
          expected,
          reason: error && error.message ? error.message : String(error),
          evidence: shot,
          query: { duration_ms: Date.now() - startedAt, url: page.url() },
        });
        writeProgress({ error: error && error.message ? error.message : String(error) });
        break;
      }
    }

    const finalResult = {
      passed: phaseResults.length === phases.length && phaseResults.every((item) => item.status === 'pass'),
      stop_reason: stopReason,
      phase_results: phaseResults,
      actions,
      artifacts,
    };
    writeProgress(finalResult);
    return finalResult;
  } finally {
    if (agent && typeof agent.destroy === 'function') {
      await agent.destroy().catch((error) => log('agent.destroy failed:', error.message));
    }
    await browser.close();
  }
}

function buildPhaseInstruction(phaseIndex, steps, expected) {
  const normalizedSteps = (steps || []).map((step) => String(step || '').trim()).filter(Boolean);
  if (normalizedSteps.length === 0) return '';
  const lines = normalizedSteps.map((step, index) => `${index + 1}. ${step}`);
  const expectedLine = expected ? `\n阶段目标: ${expected}` : '';
  return `执行阶段 ${phaseIndex + 1}:\n${lines.join('\n')}${expectedLine}`;
}

function buildAssertInstruction(expected, currentUrl) {
  return [
    `当前页面 URL: ${currentUrl || '(unknown)'}`,
    `请验证以下阶段预期是否成立: ${expected}`,
    '如果预期包含 URL 条件,请以上面的当前页面 URL 作为判断依据。',
  ].join('\n');
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
