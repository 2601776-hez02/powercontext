#!/usr/bin/env node
/**
 * 自动化基准：用 Edge/Chrome（CDP）驱动真实页面，按配置矩阵跑
 * 模型加载 → 语料导入（向量化 + 写入 + 索引刷新）→ 检索基准 → 增量导入验证，
 * 输出 JSON 与截图。
 *
 * 前置：
 *   1) 启动服务：npm start
 *   2) 启动浏览器（headless 即可）：
 *      msedge --headless=new --remote-debugging-port=9222 --user-data-dir=... about:blank
 *   3) node scripts/bench.mjs --rounds 5
 *
 * 输出：bench/results/<label>.json、bench/results/summary.json、bench/screenshots/<label>.png
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright-core';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..');
const RESULTS = path.join(ROOT, 'bench', 'results');
const SHOTS = path.join(ROOT, 'bench', 'screenshots');
const CDP = process.env.CDP || 'http://127.0.0.1:9222';
const APP = process.env.APP_URL || 'http://127.0.0.1:5173/';

const argv = process.argv.slice(2);
const flag = (name, fallback) => {
  const entry = argv.find((item) => item.startsWith(`--${name}=`));
  return entry ? entry.slice(name.length + 3) : fallback;
};
const rounds = Number(flag('rounds', '5'));
const only = flag('only', '');
const keepData = argv.includes('--keep-data');

const DEFAULT_CONFIGS = [
  { label: 'webgpu-fp16', device: 'webgpu', dtype: 'fp16', batchSize: 8 },
  { label: 'webgpu-fp32', device: 'webgpu', dtype: 'fp32', batchSize: 8 },
  { label: 'webgpu-fp16-batch1', device: 'webgpu', dtype: 'fp16', batchSize: 1 },
  { label: 'wasm-int8', device: 'wasm', dtype: 'q8', batchSize: 8 },
  { label: 'wasm-int8-batch1', device: 'wasm', dtype: 'q8', batchSize: 1 },
  { label: 'wasm-fp32', device: 'wasm', dtype: 'fp32', batchSize: 8 },
  { label: 'wasm-int8-threads1', device: 'wasm', dtype: 'q8', batchSize: 8, threads: 1 },
  { label: 'fallback-no-webgpu', device: 'auto', dtype: 'q8', batchSize: 8, nogpu: true },
];

const configs = only ? DEFAULT_CONFIGS.filter((config) => config.label === only) : DEFAULT_CONFIGS;

fs.mkdirSync(RESULTS, { recursive: true });
fs.mkdirSync(SHOTS, { recursive: true });

const browser = await chromium.connectOverCDP(CDP);
const context = browser.contexts()[0] ?? (await browser.newContext());
const summary = { generatedAt: new Date().toISOString(), rounds, configs: [] };

for (const config of configs) {
  const url = `${APP}?autoload=0${config.nogpu ? '&nogpu=1' : ''}${config.threads ? `&threads=${config.threads}` : ''}`;
  const page = await context.newPage();
  const consoleErrors = [];
  page.on('pageerror', (error) => consoleErrors.push(String(error.message)));
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text().slice(0, 300));
  });
  console.log(`\n=== ${config.label} (${config.device}/${config.dtype}/batch ${config.batchSize})`);
  await page.goto(url, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => Boolean(window.__kb), null, { timeout: 60_000 });
  await page.evaluate(() => window.__kb.waitReady());
  await page.evaluate((cfg) => window.__kb.setOptions(cfg), config);

  const record = { ...config, url, errors: consoleErrors };

  // 1) 模型加载
  record.model = await page.evaluate(
    (timeoutMs) =>
      Promise.race([
        window.__kb.loadModel(),
        new Promise((_, reject) => setTimeout(() => reject(new Error(`load timeout ${timeoutMs}ms`)), timeoutMs)),
      ]),
    Number(flag('loadTimeout', '240000')),
  );
  record.modelBytes = await page.evaluate(() => window.__kb.state.modelBytes);
  console.log(
    `  model   : ${record.model.device}/${record.model.dtype} load=${Math.round(record.model.loadMs)}ms warmup=${Math.round(record.model.warmupMs)}ms fallback=${record.model.fallbackFrom ?? 'no'}`,
  );

  // 2) 语料导入（每次先清空，保证每个配置的写入/刷新数据可比）
  if (!keepData) await page.evaluate(() => window.__kb.reset());
  record.import = await page.evaluate(() => window.__kb.importSample());
  console.log(
    `  import  : ${record.import.chunks} chunks · embed=${record.import.embedMs}ms · write=${record.import.writeMs}ms · refresh=${record.import.refreshMs}ms`,
  );

  // 3) 检索基准（混合 / 纯向量 / 纯全文）
  record.bench = await page.evaluate((r) => window.__kb.bench({ rounds: r }), rounds);
  for (const row of record.bench.results) {
    const recall = row.recallAt5 === null ? '-' : row.recallAt5.toFixed(3);
    const hit1 = row.hitAt1 === null ? '-' : row.hitAt1.toFixed(3);
    const mrr = row.mrr === null ? '-' : row.mrr.toFixed(3);
    console.log(
      `  ${row.mode.padEnd(9)}: recall@5=${recall} · hit@1=${hit1} · mrr=${mrr} · q-embed=${row.queryEmbedMs}ms · seekdb=${row.dbMs}ms · e2e=${row.e2eMs}ms · p95=${row.e2eP95}ms`,
    );
  }

  // 3b) 增量导入：只加两篇新文档，刷新后用只存在于新文档里的问题验证可见性
  record.incremental = await page.evaluate(() => window.__kb.importIncremental());
  record.incrementalProbe = await page.evaluate(async () => {
    const result = await window.__kb.search('EdgeKB 服务默认监听哪个端口，怎么清空知识库');
    return {
      searchMs: result.searchMs,
      e2eMs: result.e2eMs,
      hits: result.hits.slice(0, 3).map((hit) => ({ id: hit.id, docId: hit.metadata?.docId, score: hit.score })),
      found: result.hits.slice(0, 5).some((hit) => hit.metadata?.docId === 'edgekb-runbook'),
    };
  });
  console.log(
    `  increment: +${record.incremental.chunks} chunks · embed=${record.incremental.embedMs}ms · write=${record.incremental.writeMs}ms · refresh=${record.incremental.refreshMs}ms · probe hit=${record.incrementalProbe.found}`,
  );

  // 4) 截图 + 落盘
  const shot = path.join(SHOTS, `${config.label}.png`);
  await page.screenshot({ path: shot, fullPage: true });
  record.screenshot = path.relative(ROOT, shot);
  fs.writeFileSync(path.join(RESULTS, `${config.label}.json`), `${JSON.stringify(record, null, 2)}\n`);
  summary.configs.push({
    label: config.label,
    device: record.model.device,
    dtype: record.model.dtype,
    fallbackFrom: record.model.fallbackFrom ?? null,
    modelBytes: record.modelBytes,
    loadMs: record.model.loadMs,
    warmupMs: record.model.warmupMs,
    import: record.import,
    incremental: record.incremental,
    incrementalProbe: record.incrementalProbe,
    bench: record.bench.results,
    ort: record.model.ort,
    adapter: record.model.adapter,
  });
  await page.close();
}

fs.writeFileSync(path.join(RESULTS, 'summary.json'), `${JSON.stringify(summary, null, 2)}\n`);
console.log(`\nwrote ${path.relative(ROOT, path.join(RESULTS, 'summary.json'))}`);
process.exit(0);
