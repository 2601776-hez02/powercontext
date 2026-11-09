/**
 * 迷你知识库 Web 应用的服务端：
 *   - 静态资源（web/、models/、web/vendor/）
 *   - JSON API（导入、检索、重置、状态）
 *
 * 服务端不参与任何推理：向量全部由浏览器在本地算好后提交。
 */
import http from 'node:http';
import fs from 'node:fs';
import fsp from 'node:fs/promises';
import path from 'node:path';
import settings, { ROOT } from './config.mjs';
import { SeekdbStore } from './seekdb-store.mjs';

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.wasm': 'application/wasm',
  '.onnx': 'application/octet-stream',
  '.txt': 'text/plain; charset=utf-8',
  '.md': 'text/markdown; charset=utf-8',
  '.png': 'image/png',
  '.svg': 'image/svg+xml',
  '.ico': 'image/x-icon',
  '.map': 'application/json; charset=utf-8',
};

const MAX_BODY = 64 * 1024 * 1024; // 64 MB：一次导入大量 512 维向量也够用

const store = new SeekdbStore();
let ready = false;
let bootError = null;

/** 跨源隔离头：让 WASM 后端能用 SharedArrayBuffer 多线程（Chrome/Edge 要求）。 */
const isolationHeaders = {
  'Cross-Origin-Opener-Policy': 'same-origin',
  'Cross-Origin-Embedder-Policy': 'require-corp',
  'Cross-Origin-Resource-Policy': 'same-origin',
};

function sendJson(res, status, payload) {
  const body = Buffer.from(JSON.stringify(payload));
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': body.length,
    'Cache-Control': 'no-store',
    ...isolationHeaders,
  });
  res.end(body);
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    req.on('data', (chunk) => {
      size += chunk.length;
      if (size > MAX_BODY) {
        reject(new Error(`request body too large (> ${MAX_BODY} bytes)`));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on('end', () => {
      if (chunks.length === 0) return resolve({});
      try {
        resolve(JSON.parse(Buffer.concat(chunks).toString('utf8')));
      } catch (error) {
        reject(new Error(`invalid JSON body: ${error.message}`));
      }
    });
    req.on('error', reject);
  });
}

const STATIC_ROOTS = [
  { prefix: '/models/', dir: path.join(ROOT, 'models') },
  { prefix: '/vendor/', dir: path.join(ROOT, 'web', 'vendor') },
  { prefix: '/corpus/', dir: path.join(ROOT, 'data', 'corpus') },
  { prefix: '/', dir: path.join(ROOT, 'web') },
];

async function serveStatic(req, res, pathname) {
  const root = STATIC_ROOTS.find((entry) => pathname.startsWith(entry.prefix));
  if (!root) return false;
  const relative = decodeURIComponent(pathname.slice(root.prefix.length));
  const target = path.resolve(root.dir, relative === '' ? 'index.html' : relative);
  if (!target.startsWith(root.dir)) return false;

  let stat;
  try {
    stat = await fsp.stat(target);
  } catch {
    return false;
  }
  if (stat.isDirectory()) return serveStatic(req, res, path.posix.join(pathname, 'index.html'));

  const ext = path.extname(target).toLowerCase();
  const headers = {
    'Content-Type': MIME[ext] ?? 'application/octet-stream',
    'Content-Length': stat.size,
    'Cache-Control': ext === '.wasm' || ext === '.onnx' ? 'public, max-age=86400' : 'no-cache',
    ...isolationHeaders,
  };
  res.writeHead(200, headers);
  if (req.method === 'HEAD') return res.end();
  fs.createReadStream(target).pipe(res);
  return true;
}

async function handleApi(req, res, pathname) {
  if (pathname === '/api/health') {
    return sendJson(res, 200, { ok: true, ready, error: bootError });
  }
  if (!ready) {
    return sendJson(res, 503, { error: bootError ?? 'database is still starting' });
  }

  if (pathname === '/api/status' && req.method === 'GET') {
    const stats = await store.stats();
    return sendJson(res, 200, { ...stats, model: settings.modelId, dimension: settings.dimension });
  }

  if (pathname === '/api/docs' && req.method === 'GET') {
    return sendJson(res, 200, { docs: await store.listDocs() });
  }

  if (pathname === '/api/import' && req.method === 'POST') {
    const body = await readBody(req);
    const chunks = body.chunks ?? [];
    const result = await store.addChunks(chunks, {
      batchSize: Number(body.batchSize ?? 64),
      refresh: body.refresh !== false,
    });
    const stats = await store.stats();
    return sendJson(res, 200, { ...result, chunks: stats.chunks, docs: stats.docs });
  }

  if (pathname === '/api/search' && req.method === 'POST') {
    const body = await readBody(req);
    if (!Array.isArray(body.vector) || body.vector.length !== settings.dimension) {
      return sendJson(res, 400, { error: `vector must be an array of ${settings.dimension} numbers` });
    }
    const result = await store.search({
      text: body.text ?? '',
      vector: body.vector,
      mode: body.mode ?? 'hybrid',
      topK: Number(body.topK ?? 10),
      rankWindowSize: body.rankWindowSize ? Number(body.rankWindowSize) : undefined,
      rankConstant: body.rankConstant ? Number(body.rankConstant) : undefined,
      includeSql: body.includeSql !== false,
    });
    return sendJson(res, 200, result);
  }

  if (pathname === '/api/reset' && req.method === 'POST') {
    const stats = await store.reset();
    return sendJson(res, 200, stats);
  }

  return sendJson(res, 404, { error: `unknown api: ${pathname}` });
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://${req.headers.host ?? 'localhost'}`);
  const pathname = url.pathname;
  try {
    if (req.method === 'OPTIONS') {
      res.writeHead(204, { ...isolationHeaders, 'Access-Control-Allow-Origin': '*' });
      return res.end();
    }
    if (pathname.startsWith('/api/')) return await handleApi(req, res, pathname);
    if (await serveStatic(req, res, pathname === '/' ? '/' : pathname)) return;
    sendJson(res, 404, { error: `not found: ${pathname}` });
  } catch (error) {
    const message = String(error?.message ?? error);
    console.error(`[server] ${req.method} ${pathname} -> ${message}`);
    if (!res.headersSent) sendJson(res, 500, { error: message });
    else res.end();
  }
});

async function main() {
  console.log(`[server] seekdb mode   : ${settings.seekdbMode}`);
  if (settings.seekdbMode === 'embedded') console.log(`[server] seekdb path   : ${settings.seekdbPath}`);
  else console.log(`[server] seekdb remote : ${settings.seekdbHost}:${settings.seekdbPort}/${settings.database}`);

  store
    .open()
    .then((info) => {
      ready = true;
      console.log(`[server] seekdb ready  : ${info.version ?? 'unknown'} · collection ${info.collection} (${info.dimension}d ${info.distance})`);
    })
    .catch((error) => {
      bootError = String(error?.message ?? error);
      console.error(`[server] seekdb init failed: ${bootError}`);
    });

  server.listen(settings.port, settings.host, () => {
    console.log(`[server] listening on http://${settings.host}:${settings.port}`);
  });
}

async function shutdown(signal) {
  console.log(`\n[server] ${signal} received, shutting down...`);
  await store.close().catch(() => {});
  server.close();
  // 嵌入式 seekdb 的原生库可能阻止事件循环退出：先礼后兵。
  const timer = setTimeout(() => process.kill(process.pid, 'SIGTERM'), 1500);
  timer.unref();
}

process.on('SIGINT', () => shutdown('SIGINT'));
process.on('SIGTERM', () => shutdown('SIGTERM'));

main();
