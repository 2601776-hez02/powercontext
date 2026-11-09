#!/usr/bin/env node
/**
 * 生成文档用的界面截图：导入内置语料 → 跑一条检索 → 视口截图 + 整页截图。
 * 前置：npm start 与一个带 CDP 的 Edge（默认 127.0.0.1:9222）。
 *
 * 用法：node scripts/shots.mjs [--dtype=fp16] [--device=webgpu]
 */
import path from 'node:path';
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright-core';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const OUT = path.join(ROOT, 'bench', 'screenshots');
const CDP = process.env.CDP || 'http://127.0.0.1:9222';
const APP = process.env.APP_URL || 'http://127.0.0.1:5173/';
const argv = process.argv.slice(2);
const arg = (name, fallback) => {
  const entry = argv.find((item) => item.startsWith(`--${name}=`));
  return entry ? entry.slice(name.length + 3) : fallback;
};

fs.mkdirSync(OUT, { recursive: true });
const browser = await chromium.connectOverCDP(CDP);
const context = browser.contexts()[0] ?? (await browser.newContext());
const page = await context.newPage();
await page.setViewportSize({ width: 1440, height: 960 });
page.on('pageerror', (error) => console.log('[pageerror]', error.message));

await page.goto(`${APP}?autoload=0`, { waitUntil: 'domcontentloaded' });
await page.waitForFunction(() => Boolean(window.__kb), null, { timeout: 60_000 });
await page.evaluate(() => window.__kb.waitReady());
await page.evaluate(
  (cfg) => window.__kb.setOptions(cfg),
  { device: arg('device', 'webgpu'), dtype: arg('dtype', 'fp16'), batchSize: 8 },
);
await page.evaluate(() => window.__kb.loadModel());
await page.evaluate(() => window.__kb.reset());
await page.evaluate(() => window.__kb.importSample());
await page.evaluate(() => window.__kb.search('seekdb 的向量索引和全文检索是怎么配合的？'));
await page.waitForTimeout(400);

const viewportShot = path.join(OUT, 'demo-viewport.png');
await page.screenshot({ path: viewportShot });
const fullShot = path.join(OUT, 'demo-full.png');
await page.screenshot({ path: fullShot, fullPage: true });
console.log('wrote', path.relative(ROOT, viewportShot), 'and', path.relative(ROOT, fullShot));
await page.close();
process.exit(0);
