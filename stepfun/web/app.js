import { chunkAll } from './chunker.js';

const $ = (id) => document.getElementById(id);
const MODEL_ID = 'Xenova/bge-small-zh-v1.5';
const MODEL_FILES = {
  q8: 'onnx/model_quantized.onnx',
  fp16: 'onnx/model_fp16.onnx',
  fp32: 'onnx/model.onnx',
};
const DTYPE_LABEL = { q8: 'INT8', fp16: 'FP16', fp32: 'FP32' };

const state = {
  server: null,
  model: null,
  modelBytes: null,
  corpus: { docs: [], queries: [] },
  metrics: {},
  lastSearch: null,
  bench: null,
  busy: false,
  logs: [],
};

/* ------------------------------------------------------------------ worker */

let worker = null;
let seq = 0;
const pending = new Map();

function ensureWorker() {
  if (worker) return worker;
  worker = new Worker('/embed-worker.js', { type: 'module' });
  worker.addEventListener('message', (event) => handleWorkerMessage(event.data ?? {}));
  worker.addEventListener('error', (event) => {
    const entry = pending.get('init');
    if (entry) entry.reject(new Error(event.message ?? 'worker error'));
  });
  return worker;
}

function handleWorkerMessage(message) {
  switch (message.type) {
    case 'init-progress':
      updateMeter('model', progressFraction(message.progress), describeProgress(message.progress));
      break;
    case 'init-warn':
      note(`后端 ${message.device} 初始化失败：${message.message}`, 'warn');
      break;
    case 'init-ok':
      pending.get('init')?.resolve(message);
      pending.delete('init');
      break;
    case 'init-error':
      pending.get('init')?.reject(new Error(message.message));
      pending.delete('init');
      break;
    case 'embed-progress': {
      const entry = pending.get(message.reqId);
      entry?.onProgress?.(message);
      break;
    }
    case 'embed-ok': {
      const entry = pending.get(message.reqId);
      pending.delete(message.reqId);
      entry?.resolve({
        vectors: message.vectors instanceof Float32Array ? message.vectors : Float32Array.from(message.vectors),
        count: message.count,
        dim: message.dim,
        ms: message.ms,
        batchTimes: message.batchTimes,
      });
      break;
    }
    case 'embed-error': {
      const entry = pending.get(message.reqId);
      pending.delete(message.reqId);
      entry?.reject(new Error(message.message));
      break;
    }
    default:
      break;
  }
}

function callWorker(type, payload = {}, { onProgress } = {}) {
  ensureWorker();
  const reqId = type === 'init' ? 'init' : ++seq;
  return new Promise((resolve, reject) => {
    pending.set(reqId, { resolve, reject, onProgress });
    worker.postMessage({ type, reqId, ...payload });
  });
}

/* -------------------------------------------------------------- embedding */

async function loadModel({ device, dtype } = {}) {
  const chosen = {
    device: device ?? $('select-device').value,
    dtype: dtype ?? $('select-dtype').value,
  };
  if (
    state.model &&
    state.modelRequest &&
    state.modelRequest.device === chosen.device &&
    state.modelRequest.dtype === chosen.dtype
  ) {
    return state.model; // 已经是同一套配置，避免重复初始化
  }
  showMeter('model');
  updateMeter('model', 0, '准备加载模型…');
  const started = performance.now();
  try {
    const info = await callWorker('init', {
      modelId: MODEL_ID,
      device: chosen.device,
      dtype: chosen.dtype,
      threads: Number(new URLSearchParams(location.search).get('threads') ?? 0),
      simulateNoGpu: new URLSearchParams(location.search).get('nogpu') === '1',
    });
    state.model = info;
    state.modelRequest = chosen;
    state.modelBytes = await measureModelBytes(info.dtype);
    state.metrics.loadMs = Math.round(performance.now() - started);
    state.metrics.modelLoadMs = info.loadMs;
    state.metrics.warmupMs = info.warmupMs;
    renderModel();
    hideMeter('model');
    note(
      info.fallbackFrom
        ? `请求 ${labelDevice(info.fallbackFrom)} 不可用，已自动降级到 ${labelDevice(info.device)}；检索流程照常可用。`
        : `推理后端：${labelDevice(info.device)}，精度 ${DTYPE_LABEL[info.dtype] ?? info.dtype}。`,
      info.fallbackFrom ? 'warn' : '',
    );
    return info;
  } catch (error) {
    hideMeter('model');
    note(`模型加载失败：${error.message}`, 'warn');
    throw error;
  }
}

async function embedTexts(texts, { onProgress, bucket = true } = {}) {
  const batchSize = Number($('select-batch').value || 8);
  const run = (payload) =>
    callWorker('embed', payload, {
      onProgress: (message) => {
        onProgress?.(message.done, message.total);
      },
    });

  // 长度分桶：同一批内文本长度接近，避免为最长样本做过量 padding（WASM 上尤其明显）
  if (!bucket || batchSize <= 1 || texts.length <= batchSize) {
    return run({ texts, batchSize });
  }
  const order = texts.map((_, index) => index).sort((a, b) => texts[a].length - texts[b].length);
  const sorted = order.map((index) => texts[index]);
  const result = await run({ texts: sorted, batchSize });
  const dim = result.dim;
  const restored = new Float32Array(result.vectors.length);
  order.forEach((originalIndex, position) => {
    restored.set(result.vectors.subarray(position * dim, (position + 1) * dim), originalIndex * dim);
  });
  return { ...result, vectors: restored };
}

async function measureModelBytes(dtype) {
  const files = ['config.json', 'tokenizer.json', 'tokenizer_config.json', 'special_tokens_map.json', 'vocab.txt', MODEL_FILES[dtype], MODEL_FILES.fp32];
  const sizes = {};
  await Promise.all(
    files.map(async (file) => {
      try {
        const response = await fetch(`/models/${MODEL_ID}/${file}`, { method: 'HEAD' });
        sizes[file] = Number(response.headers.get('content-length') ?? 0);
      } catch {
        sizes[file] = 0;
      }
    }),
  );
  const base = sizes['config.json'] + sizes['tokenizer.json'] + sizes['tokenizer_config.json'] + sizes['special_tokens_map.json'] + sizes['vocab.txt'];
  const selected = base + (sizes[MODEL_FILES[dtype]] ?? 0);
  const fp32 = base + (sizes[MODEL_FILES.fp32] ?? 0);
  return { total: selected, base, onnx: sizes[MODEL_FILES[dtype]] ?? 0, fp32, ratio: selected > 0 ? fp32 / selected : 1, detail: sizes };
}

/* ---------------------------------------------------------------- import */

async function importDocuments(documents, { refresh } = {}) {
  if (!state.model) await loadModel();
  const docs = documents.map((doc, index) => ({
    docId: doc.docId ?? `doc-${index + 1}`,
    title: doc.title ?? null,
    text: doc.text,
    source: doc.source ?? doc.docId ?? `doc-${index + 1}`,
  }));
  const chunks = chunkAll(docs, { maxChars: 480, overlap: 80 });
  if (chunks.length === 0) throw new Error('没有可导入的文本');

  showMeter('import');
  updateMeter('import', 0, `向量化 0/${chunks.length} 块`);
  const embedStarted = performance.now();
  const embedded = await embedTexts(
    chunks.map((chunk) => chunk.text),
    { onProgress: (done, total) => updateMeter('import', done / total, `向量化 ${done}/${total} 块`) },
  );
  const embedMs = Math.round(performance.now() - embedStarted);
  const dim = embedded.dim;
  const payload = chunks.map((chunk, index) => ({
    ...chunk,
    vector: Array.from(embedded.vectors.subarray(index * dim, (index + 1) * dim)),
  }));

  updateMeter('import', 1, '写入 seekdb…');
  const response = await fetch('/api/import', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ chunks: payload, refresh: refresh ?? $('check-refresh').checked, batchSize: 64 }),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error ?? 'import failed');

  state.metrics.embedMs = embedMs;
  state.metrics.embedChunks = chunks.length;
  state.metrics.embedRate = chunks.length > 0 ? chunks.length / (embedMs / 1000) : 0;
  state.metrics.writeMs = result.writeMs;
  state.metrics.refreshMs = result.refreshMs;
  state.metrics.importTotalMs = embedMs + (result.writeMs ?? 0) + (result.refreshMs ?? 0);
  hideMeter('import');
  renderMetrics();
  await refreshStatus();
  return { chunks: chunks.length, ...result, embedMs };
}

async function loadSampleCorpus() {
  if (state.corpus.docs.length === 0) await loadCorpusManifest();
  const docs = [];
  for (const entry of state.corpus.docs) {
    const response = await fetch(`/corpus/${entry.file}`);
    docs.push({ docId: entry.docId, title: entry.title, text: await response.text(), source: entry.file });
  }
  return importDocuments(docs);
}

async function loadCorpusManifest() {
  const manifest = await (await fetch('/corpus/manifest.json')).json();
  const queries = await (await fetch('/corpus/queries.json')).json();
  state.corpus = { docs: manifest.docs ?? [], queries: queries.queries ?? [] };
  return state.corpus;
}

/** 增量导入：只导入 data/corpus/incremental 下的少量新文档，用于验证“刷新后新内容可检索”。 */
async function loadIncrementalCorpus() {
  const manifest = await (await fetch('/corpus/incremental/manifest.json')).json();
  const docs = [];
  for (const entry of manifest.docs ?? []) {
    const response = await fetch(`/corpus/incremental/${entry.file}`);
    docs.push({ docId: entry.docId, title: entry.title, text: await response.text(), source: `incremental/${entry.file}` });
  }
  return importDocuments(docs);
}

/* ---------------------------------------------------------------- search */

async function runSearch(query, options = {}) {
  if (!state.model) await loadModel();
  if (!query?.trim()) return null;
  const mode = options.mode ?? $('select-mode').value;
  const topK = Number(options.topK ?? $('select-topk').value ?? 10);
  const started = performance.now();

  const queryEmbedStarted = performance.now();
  const embedded = await embedTexts([query]);
  const queryEmbedMs = Math.round(performance.now() - queryEmbedStarted);
  const vector = Array.from(embedded.vectors.subarray(0, embedded.dim));

  const dbStarted = performance.now();
  const response = await fetch('/api/search', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({
      vector,
      text: query,
      mode,
      topK,
      rankWindowSize: Number($('input-window').value || 40),
      rankConstant: Number($('input-constant').value || 60),
    }),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error ?? 'search failed');
  const dbRoundTripMs = Math.round(performance.now() - dbStarted);
  const e2eMs = Math.round(performance.now() - started);

  state.metrics.queryEmbedMs = queryEmbedMs;
  state.metrics.searchMs = result.searchMs;
  state.metrics.searchRoundTripMs = dbRoundTripMs;
  state.metrics.e2eMs = e2eMs;
  state.lastSearch = { query, mode, topK, ...result };
  renderMetrics();
  renderResults();
  renderSql();
  return { ...result, queryEmbedMs, dbRoundTripMs, e2eMs };
}

/* ----------------------------------------------------------------- bench */

async function runBenchmark({ rounds = 5, modes = ['hybrid', 'vector', 'fulltext'], limit = null } = {}) {
  if (!state.model) await loadModel();
  if (state.corpus.queries.length === 0) await loadCorpusManifest();
  const queries = state.corpus.queries.slice(0, limit ?? state.corpus.queries.length);
  const output = { generatedAt: new Date().toISOString(), model: state.model, rounds, results: [] };

  for (const mode of modes) {
    const perQuery = [];
    for (const item of queries) {
      const samples = [];
      let hits = [];
      for (let round = 0; round < rounds; round += 1) {
        const result = await runSearch(item.q, { mode, topK: 10 });
        samples.push({
          queryEmbedMs: result.queryEmbedMs,
          dbMs: result.searchMs,
          e2eMs: result.e2eMs,
        });
        hits = result.hits;
      }
      const gold = new Set(item.gold ?? []);
      const topDocIds = [...new Set(hits.map((hit) => hit.metadata?.docId))];
      const rankedDocIds = hits.map((hit) => hit.metadata?.docId).filter(Boolean);
      const firstGoldRank = rankedDocIds.findIndex((docId) => gold.has(docId));
      const recall = gold.size === 0 ? null : firstGoldRank >= 0 && firstGoldRank < 5 ? 1 : 0;
      perQuery.push({
        q: item.q,
        kind: item.kind ?? 'easy',
        gold: [...gold],
        topDocIds,
        hitCount: hits.length,
        recallAt5: recall,
        hitAt1: gold.size === 0 ? null : rankedDocIds[0] && gold.has(rankedDocIds[0]) ? 1 : 0,
        mrr: firstGoldRank >= 0 ? 1 / (firstGoldRank + 1) : 0,
        queryEmbedMs: median(samples.map((s) => s.queryEmbedMs)),
        dbMs: median(samples.map((s) => s.dbMs)),
        e2eMs: median(samples.map((s) => s.e2eMs)),
        e2eP95: percentile(samples.map((s) => s.e2eMs), 95),
      });
    }
    const recalls = perQuery.map((item) => item.recallAt5).filter((value) => value !== null);
    const hit1s = perQuery.map((item) => item.hitAt1).filter((value) => value !== null);
    output.results.push({
      mode,
      queries: perQuery.length,
      recallAt5: recalls.length ? mean(recalls) : null,
      hitAt1: hit1s.length ? mean(hit1s) : null,
      mrr: perQuery.length ? mean(perQuery.map((item) => item.mrr)) : null,
      queryEmbedMs: median(perQuery.map((item) => item.queryEmbedMs)),
      dbMs: median(perQuery.map((item) => item.dbMs)),
      e2eMs: median(perQuery.map((item) => item.e2eMs)),
      e2eP95: percentile(perQuery.flatMap((item) => [item.e2eMs]), 95),
      perQuery,
    });
  }
  state.bench = output;
  renderBench();
  return output;
}

function renderBench() {
  const output = state.bench;
  if (!output) return;
  const lines = [];
  lines.push(`模型 ${output.model.modelId.replace('Xenova/', '')} · ${DTYPE_LABEL[output.model.dtype]} · ${labelDevice(output.model.device)} · 每档 ${output.rounds} 轮`);
  lines.push('');
  lines.push('模式         Recall@5   Hit@1   MRR    查询向量化  seekdb 检索  端到端(中位) 端到端(P95)');
  for (const row of output.results) {
    lines.push(
      [
        pad(row.mode, 12),
        pad(row.recallAt5 === null ? '—' : row.recallAt5.toFixed(3), 10),
        pad(row.hitAt1 === null ? '—' : row.hitAt1.toFixed(3), 7),
        pad(row.mrr === null ? '—' : row.mrr.toFixed(3), 7),
        pad(`${row.queryEmbedMs.toFixed(1)}ms`, 11),
        pad(`${row.dbMs.toFixed(1)}ms`, 12),
        pad(`${row.e2eMs.toFixed(1)}ms`, 13),
        pad(`${row.e2eP95.toFixed(1)}ms`, 10),
      ].join(' '),
    );
  }
  $('bench-output').textContent = lines.join('\n');
}

/* ------------------------------------------------------------------- UI */

function renderModel() {
  const model = state.model;
  if (!model) return;
  const bytes = state.modelBytes;
  $('kv-model').textContent = `${model.modelId.replace('Xenova/', '')} · ${DTYPE_LABEL[model.dtype] ?? model.dtype}`;
  $('kv-device').innerHTML = `<span class="badge ${model.device === 'webgpu' ? 'gpu' : 'cpu'}">${labelDevice(model.device)}</span>`;
  $('kv-size').textContent = bytes
    ? `${(bytes.total / 1048576).toFixed(2)} MB${bytes.ratio > 1.01 ? `（FP32 ${(bytes.fp32 / 1048576).toFixed(1)} MB，压缩比 ${bytes.ratio.toFixed(2)}×）` : ''}`
    : '—';
  $('kv-load').textContent = `${model.loadMs.toFixed(0)} ms（预热 ${model.warmupMs.toFixed(0)} ms）`;
  setPill('pill-backend', `后端：${labelDevice(model.device)}`, model.device === 'webgpu' ? 'ok' : 'warn');
  setPill('pill-model', `模型：${DTYPE_LABEL[model.dtype] ?? model.dtype}`, 'ok');
  if (model.adapter) {
    const adapter = model.adapter;
    $('device-note').textContent = `WebGPU 适配器：${[adapter.vendor, adapter.architecture, adapter.device].filter(Boolean).join(' / ') || '未知'}`;
  }
}

function renderMetrics() {
  const metrics = state.metrics;
  $('m-load').textContent = metrics.loadMs ? `${metrics.loadMs} ms` : '—';
  $('m-embed').textContent = metrics.embedMs
    ? `${metrics.embedMs} ms · ${metrics.embedChunks} 块 · ${metrics.embedRate?.toFixed(1)} 块/s`
    : '—';
  $('m-write').textContent = metrics.writeMs !== undefined ? `${metrics.writeMs} ms` : '—';
  $('m-refresh').textContent = metrics.refreshMs !== undefined ? `${metrics.refreshMs} ms` : '—';
  $('m-qembed').textContent = metrics.queryEmbedMs ? `${metrics.queryEmbedMs} ms` : '—';
  $('m-search').textContent = metrics.searchMs !== undefined ? `${metrics.searchMs} ms` : '—';
  $('m-e2e').textContent = metrics.e2eMs ? `${metrics.e2eMs} ms` : '—';
}

function renderResults() {
  const last = state.lastSearch;
  const container = $('results');
  if (!last) {
    container.innerHTML = '<div class="empty">还没有检索结果。导入语料后输入问题试试。</div>';
    return;
  }
  if (last.hits.length === 0) {
    container.innerHTML = '<div class="empty">没有命中，换个说法或补充语料试试。</div>';
    return;
  }
  const terms = tokenizeQuery(last.query);
  container.innerHTML = last.hits
    .map((hit, index) => {
      const metadata = hit.metadata ?? {};
      const score = hit.scoreKind === 'rrf' ? `RRF ${Number(hit.score).toFixed(4)}` : hit.scoreKind === 'relevance' ? `相关度 ${Number(hit.score).toFixed(3)}` : `距离 ${Number(hit.score).toFixed(4)}`;
      return `
        <article class="result">
          <div class="result-head">
            <span class="result-title">${escapeHtml(metadata.title ?? hit.id)}</span>
            <span class="result-score">#${index + 1} · ${score}</span>
          </div>
          <div class="result-meta">${escapeHtml(metadata.docId ?? '')} · 片段 ${metadata.chunkIndex ?? 0} · ${hit.id}</div>
          <div class="result-text">${highlight(hit.text, terms)}</div>
        </article>`;
    })
    .join('');
}

function renderSql() {
  const last = state.lastSearch;
  if (!last) return;
  const statements = last.statements ?? [];
  const queryStatement = statements[statements.length - 1];
  $('sql-view').textContent = queryStatement?.sql ?? '（没有捕获到 SQL）';
  $('stmt-list').innerHTML = statements
    .map(
      (statement, index) =>
        `<li><code>${index + 1}. ${escapeHtml(statement.sql.slice(0, 96))}${statement.sql.length > 96 ? '…' : ''}</code><span>${statement.ms} ms</span></li>`,
    )
    .join('');
}

async function refreshStatus() {
  try {
    const response = await fetch('/api/status');
    const status = await response.json();
    if (!response.ok) throw new Error(status.error ?? 'status failed');
    state.server = status;
    setPill('pill-db', `seekdb：${status.mode === 'embedded' ? '嵌入式' : status.host} · ${status.chunks} 块`, 'ok');
    $('corpus-summary').textContent =
      status.chunks > 0
        ? `知识库：${status.docs} 篇文档 / ${status.chunks} 个片段 · seekdb ${status.version ?? ''}`
        : '知识库为空';
  } catch (error) {
    setPill('pill-db', 'seekdb：未就绪', 'bad');
    $('corpus-summary').textContent = `seekdb 未就绪：${error.message}`;
  }
}

function setPill(id, text, kind) {
  const pill = $(id);
  pill.textContent = text;
  pill.className = `pill ${kind ?? ''}`;
}

function note(text, kind = '') {
  const element = $('device-note');
  element.textContent = text;
  element.className = `note ${kind}`;
  state.logs.push({ at: new Date().toISOString(), text, kind });
}

function showMeter(which) {
  $(`${which}-meter`).hidden = false;
}
function hideMeter(which) {
  $(`${which}-meter`).hidden = true;
}
function updateMeter(which, fraction, text) {
  $(`${which}-meter-fill`).style.width = `${Math.max(0, Math.min(1, fraction)) * 100}%`;
  $(`${which}-meter-text`).textContent = text;
}
function progressFraction(progress) {
  if (!progress) return 0;
  if (typeof progress.progress === 'number') return progress.progress / 100;
  if (progress.total && progress.loaded) return progress.loaded / progress.total;
  return 0;
}
function describeProgress(progress) {
  if (!progress) return '加载中…';
  const file = progress.file ?? progress.name ?? '文件';
  return progress.status === 'done' ? `${file} 完成` : `${file} ${Math.round(progressFraction(progress) * 100)}%`;
}

/* ------------------------------------------------------------- utilities */

function labelDevice(device) {
  return device === 'webgpu' ? 'WebGPU' : device === 'wasm' ? 'WASM' : String(device ?? '—');
}
function median(values) {
  const list = values.filter((value) => Number.isFinite(value)).sort((a, b) => a - b);
  if (list.length === 0) return 0;
  const mid = Math.floor(list.length / 2);
  return list.length % 2 ? list[mid] : (list[mid - 1] + list[mid]) / 2;
}
function percentile(values, p) {
  const list = values.filter((value) => Number.isFinite(value)).sort((a, b) => a - b);
  if (list.length === 0) return 0;
  const index = Math.min(list.length - 1, Math.ceil((p / 100) * list.length) - 1);
  return list[index];
}
function mean(values) {
  return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : 0;
}
function pad(text, width) {
  return String(text).padEnd(width, ' ');
}
function tokenizeQuery(query) {
  return String(query)
    .split(/[\s,，。.、;；:：!！?？"“”'‘’()（）[\]【】]+/)
    .filter((term) => term.length >= 2);
}
function escapeHtml(text) {
  return String(text ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
function highlight(text, terms) {
  const escaped = escapeHtml(text);
  if (terms.length === 0) return escaped;
  const pattern = terms
    .map((term) => term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
    .filter(Boolean)
    .join('|');
  if (!pattern) return escaped;
  return escaped.replace(new RegExp(`(${pattern})`, 'gi'), '<mark>$1</mark>');
}

/* -------------------------------------------------------------- bootstrapping */

function wire() {
  $('btn-load').addEventListener('click', () => guard(() => loadModel()));
  $('btn-sample').addEventListener('click', () => guard(() => loadSampleCorpus()));
  $('btn-paste').addEventListener('click', () =>
    guard(async () => {
      const raw = $('paste-area').value.trim();
      if (!raw) throw new Error('请先粘贴文本');
      const title = raw.match(/^#\s+(.+)$/m)?.[1] ?? `粘贴文本 ${new Date().toLocaleTimeString()}`;
      await importDocuments([{ docId: `paste-${Date.now()}`, title, text: raw, source: 'pasted' }]);
      $('paste-area').value = '';
    }),
  );
  $('file-input').addEventListener('change', (event) =>
    guard(async () => {
      const files = [...event.target.files];
      if (files.length === 0) return;
      const docs = [];
      for (const file of files) {
        docs.push({ docId: file.name.replace(/\.[^.]+$/, ''), title: file.name, text: await file.text(), source: file.name });
      }
      await importDocuments(docs);
      event.target.value = '';
    }),
  );
  $('btn-reset').addEventListener('click', () =>
    guard(async () => {
      await fetch('/api/reset', { method: 'POST' });
      state.lastSearch = null;
      state.metrics = {};
      renderResults();
      renderMetrics();
      $('sql-view').textContent = '（检索后显示）';
      $('stmt-list').innerHTML = '';
      await refreshStatus();
    }),
  );
  $('btn-search').addEventListener('click', () => guard(() => runSearch($('query-input').value)));
  $('query-input').addEventListener('keydown', (event) => {
    if (event.key === 'Enter') guard(() => runSearch($('query-input').value));
  });
  $('btn-bench').addEventListener('click', () =>
    guard(async () => {
      const rounds = Number($('input-rounds').value || 5);
      await runBenchmark({ rounds });
    }),
  );
  $('btn-export').addEventListener('click', () => exportBench());
}

async function guard(fn) {
  if (state.busy) return null;
  state.busy = true;
  setBusy(true);
  try {
    return await fn();
  } catch (error) {
    console.error(error);
    note(`操作失败：${error.message}`, 'warn');
    return null;
  } finally {
    state.busy = false;
    setBusy(false);
  }
}

function setBusy(busy) {
  document.body.style.cursor = busy ? 'progress' : '';
}

function exportBench() {
  if (!state.bench) {
    note('还没有基准结果', 'warn');
    return null;
  }
  const blob = new Blob([JSON.stringify(state.bench, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = `bench-${state.bench.model.dtype}-${state.bench.model.device}-${Date.now()}.json`;
  link.click();
  URL.revokeObjectURL(url);
  return link.download;
}

/** 供 scripts/bench.mjs（Edge + CDP）驱动的自动化入口。 */
function exposeAutomation() {
  window.__kb = {
    state,
    async waitReady() {
      for (let attempt = 0; attempt < 120; attempt += 1) {
        const response = await fetch('/api/health').catch(() => null);
        const health = response ? await response.json().catch(() => null) : null;
        if (health?.ready) return health;
        await new Promise((resolve) => setTimeout(resolve, 500));
      }
      throw new Error('seekdb not ready');
    },
    waitIdle: async () => {
      for (let attempt = 0; attempt < 2400; attempt += 1) {
        if (!state.busy) return true;
        await new Promise((resolve) => setTimeout(resolve, 250));
      }
      return false;
    },
    async setOptions({ dtype, device, batchSize }) {
      if (dtype) $('select-dtype').value = dtype;
      if (device) $('select-device').value = device;
      if (batchSize) $('select-batch').value = String(batchSize);
    },
    loadModel,
    importSample: loadSampleCorpus,
    importIncremental: loadIncrementalCorpus,
    importDocuments,
    search: runSearch,
    bench: runBenchmark,
    async status() {
      await refreshStatus();
      return state.server;
    },
    async reset() {
      await fetch('/api/reset', { method: 'POST' });
      await refreshStatus();
      return state.server;
    },
    metrics: () => JSON.parse(JSON.stringify(state.metrics)),
    logs: () => state.logs.slice(),
    benchResult: () => state.bench,
    exportBench,
    corpus: loadCorpusManifest,
  };
}

async function main() {
  wire();
  exposeAutomation();
  renderResults();
  await refreshStatus();
  if (new URLSearchParams(location.search).get('autoload') !== '0') {
    await guard(() => loadModel());
  }
  await loadCorpusManifest().catch(() => {});
}

main();
