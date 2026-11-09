#!/usr/bin/env node
/**
 * Download the embedded model (bge-small-zh-v1.5, ONNX) from a Hugging Face mirror.
 *
 * Usage:
 *   node scripts/fetch-models.mjs                 # fetch the default model set
 *   node scripts/fetch-models.mjs --only int8     # fetch one precision only
 *   HF_ENDPOINT=https://hf-mirror.com node scripts/fetch-models.mjs
 *
 * The script is resumable and idempotent: files that already exist with the
 * expected byte size are skipped. Run `npm run setup` to fetch everything the
 * demo needs (int8 + fp16 + fp32).
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..');
const MODEL_ID = process.env.MODEL_ID || 'Xenova/bge-small-zh-v1.5';
const REVISION = process.env.MODEL_REVISION || '75c43b069aac4d136ba6bc1122f995fedcfd2781';
const ENDPOINT = (process.env.HF_ENDPOINT || 'https://hf-mirror.com').replace(/\/$/, '');
const LOCAL_DIR = path.join(ROOT, 'models', MODEL_ID);

const SMALL_FILES = ['config.json', 'tokenizer.json', 'tokenizer_config.json', 'special_tokens_map.json', 'vocab.txt'];
const ONNX_FILES = {
  fp32: 'onnx/model.onnx',
  fp16: 'onnx/model_fp16.onnx',
  int8: 'onnx/model_quantized.onnx',
};

const args = process.argv.slice(2);
const onlyArg = args.includes('--only') ? args[args.indexOf('--only') + 1] : null;
const wantedOnnx = onlyArg ? [onlyArg] : Object.keys(ONNX_FILES);

const files = [...SMALL_FILES, ...wantedOnnx.map((k) => ONNX_FILES[k])];

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const MIN_BYTES = 100;
const MIN_ONNX_BYTES = 1024 * 1024;

/** Download with retry/backoff; mirrors rate-limit aggressively (HTTP 429). */
async function fetchWithRetry(url, attempts = 6) {
  let lastError;
  for (let attempt = 1; attempt <= attempts; attempt++) {
    try {
      const res = await fetch(url, { redirect: 'follow' });
      if (res.status === 429 || res.status === 503) {
        const wait = Math.min(30, 4 * attempt);
        console.warn(`[wait] HTTP ${res.status}, retry ${attempt}/${attempts} in ${wait}s`);
        await sleep(wait * 1000);
        continue;
      }
      if (!res.ok) throw new Error(`HTTP ${res.status} for ${url}`);
      return Buffer.from(await res.arrayBuffer());
    } catch (error) {
      lastError = error;
      await sleep(2000 * attempt);
    }
  }
  throw lastError ?? new Error(`failed to download ${url}`);
}

async function download(relPath) {
  const dest = path.join(LOCAL_DIR, relPath);
  fs.mkdirSync(path.dirname(dest), { recursive: true });
  const minBytes = relPath.endsWith('.onnx') ? MIN_ONNX_BYTES : MIN_BYTES;
  if (fs.existsSync(dest) && fs.statSync(dest).size >= minBytes) {
    console.log(`[skip] ${relPath} (${(fs.statSync(dest).size / 1048576).toFixed(2)} MB)`);
    return;
  }
  const url = `${ENDPOINT}/${MODEL_ID}/resolve/${REVISION}/${relPath}`;
  const started = Date.now();
  const buf = await fetchWithRetry(url);
  if (buf.length < minBytes) throw new Error(`suspiciously small file: ${relPath} (${buf.length} bytes)`);
  fs.writeFileSync(dest, buf);
  console.log(
    `[ok]   ${relPath} ${(buf.length / 1048576).toFixed(2)} MB in ${((Date.now() - started) / 1000).toFixed(1)}s`,
  );
  await sleep(1500);
}

console.log(`model   : ${MODEL_ID}@${REVISION.slice(0, 12)}`);
console.log(`endpoint: ${ENDPOINT}`);
console.log(`dest    : ${LOCAL_DIR}`);
for (const rel of files) {
  await download(rel);
}
fs.writeFileSync(
  path.join(LOCAL_DIR, 'SOURCE.json'),
  JSON.stringify({ model: MODEL_ID, revision: REVISION, endpoint: ENDPOINT, fetchedAt: new Date().toISOString() }, null, 2),
);
console.log('done');
