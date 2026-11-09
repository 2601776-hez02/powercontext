#!/usr/bin/env node
/**
 * 浏览器冒烟脚本：连接一个已启动的 Edge/Chrome（CDP），跑通
 * 模型加载 → 导入内置语料 → 混合检索 → 截图。
 *
 * 用法：
 *   1) 先启动浏览器：
 *      msedge.exe --headless=new --remote-debugging-port=9222 --user-data-dir=%TEMP%\edgekb-profile about:blank
 *   2) node scripts/smoke.mjs
 */
import { chromium } from 'playwright-core';

const endpoint = process.env.CDP || 'http://127.0.0.1:9222';
const appUrl = process.env.APP_URL || 'http://127.0.0.1:5173/';
const shot = process.env.SHOT || 'bench/screenshots/smoke.png';
const device = process.env.DEVICE || 'auto';
const dtype = process.env.DTYPE || '';
const verbose = process.env.VERBOSE === '1';

const browser = await chromium.connectOverCDP(endpoint);
const context = browser.contexts()[0] ?? (await browser.newContext());
const page = await context.newPage();
page.on('console', (message) => {
  const text = message.text();
  if (!verbose && !/error|warn|Transformers|onnx|webgpu/i.test(text)) return;
  console.log('[console]', message.type(), text.slice(0, 300));
});
page.on('pageerror', (error) => console.log('[pageerror]', error.message));

await page.goto(`${appUrl}?autoload=0`, { waitUntil: 'domcontentloaded' });
await page.waitForFunction(() => Boolean(window.__kb), null, { timeout: 60_000 });

const gpu = await page.evaluate(async () => {
  const hasGpu = 'gpu' in navigator;
  let adapter = null;
  if (hasGpu) {
    try {
      const a = await navigator.gpu.requestAdapter();
      adapter = a ? { ...(a.info ?? {}) } : null;
    } catch (error) {
      adapter = { error: String(error.message ?? error) };
    }
  }
  return { hasGpu, adapter, crossOriginIsolated: self.crossOriginIsolated, cores: navigator.hardwareConcurrency };
});
console.log('gpu:', JSON.stringify(gpu));

await page.evaluate(() => window.__kb.waitReady());
console.log('seekdb ready');

await page.evaluate(([d, p]) => window.__kb.setOptions({ device: d, dtype: p || undefined }), [device, dtype]);

const modelStarted = Date.now();
const model = await page.evaluate(
  (timeoutMs) =>
    Promise.race([
      window.__kb.loadModel(),
      new Promise((_, reject) => setTimeout(() => reject(new Error(`loadModel timeout after ${timeoutMs}ms`)), timeoutMs)),
    ]),
  Number(process.env.LOAD_TIMEOUT || 180_000),
);
console.log(`model(${Date.now() - modelStarted}ms):`, JSON.stringify(model));

const importStarted = Date.now();
const imported = await page.evaluate(() => window.__kb.importSample());
console.log(`import(${Date.now() - importStarted}ms):`, JSON.stringify(imported));

const queries = await page.evaluate(async () => (await window.__kb.corpus()).queries.slice(0, 3));
for (const item of queries) {
  const result = await page.evaluate((q) => window.__kb.search(q), item.q);
  const summary = result.hits.slice(0, 3).map((hit) => ({ id: hit.id, doc: hit.metadata?.docId, score: hit.score }));
  console.log(`search "${item.q}" ${result.searchMs}ms ->`, JSON.stringify(summary));
}

await page.screenshot({ path: shot, fullPage: false });
console.log('screenshot:', shot);
await page.close();
await browser.close?.();
process.exit(0);
