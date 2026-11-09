#!/usr/bin/env node
/**
 * 服务端自检：不依赖浏览器，验证 seekdb 接入、写入、混合检索与索引刷新。
 *
 * 用法：npm start 之后另开一个终端执行 `npm run verify`
 * 环境变量：APP_URL（默认 http://127.0.0.1:5173）
 */
const BASE = process.env.APP_URL || 'http://127.0.0.1:5173';
const DIM = 512;

const checks = [];
function check(name, passed, detail = '') {
  checks.push({ name, passed, detail });
  console.log(`${passed ? '[ok]  ' : '[FAIL]'} ${name}${detail ? ` — ${detail}` : ''}`);
}

const randVector = (seed) => {
  const vector = new Array(DIM).fill(0).map((_, index) => Math.sin(seed * 1.7 + index * 0.13));
  const norm = Math.hypot(...vector);
  return vector.map((value) => value / norm);
};

async function json(path, options) {
  const response = await fetch(`${BASE}${path}`, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(`${path} -> HTTP ${response.status}: ${body.error ?? ''}`);
  return body;
}

const health = await json('/api/health');
check('seekdb 就绪', health.ready === true, health.error ?? '');

const before = await json('/api/status');
check('状态接口返回集合信息', Boolean(before.collection), `集合 ${before.collection} · ${before.chunks} 块`);

const stamp = Date.now();
const chunks = [
  { id: `verify-${stamp}-a`, docId: `verify-${stamp}`, title: '自检片段 A', source: 'verify', chunkIndex: 0, text: 'seekdb 用 HNSW 做向量检索，用 ngram 倒排做中文全文检索。', vector: randVector(1) },
  { id: `verify-${stamp}-b`, docId: `verify-${stamp}`, title: '自检片段 B', source: 'verify', chunkIndex: 1, text: '浏览器端用 Transformers.js 跑 INT8 量化的 embedding 模型。', vector: randVector(2) },
];
const imported = await json('/api/import', {
  method: 'POST',
  headers: { 'content-type': 'application/json' },
  body: JSON.stringify({ chunks, refresh: true }),
});
check('写入 + 索引刷新', imported.inserted === 2, `write ${imported.writeMs}ms · refresh ${imported.refreshMs}ms`);

const after = await json('/api/status');
check('写入后片段数增加', after.chunks >= before.chunks + 2, `${before.chunks} -> ${after.chunks}`);

const hybrid = await json('/api/search', {
  method: 'POST',
  headers: { 'content-type': 'application/json' },
  body: JSON.stringify({ vector: randVector(1), text: 'HNSW 向量检索', mode: 'hybrid', topK: 5 }),
});
check('混合检索返回结果', Array.isArray(hybrid.hits) && hybrid.hits.length > 0, `${hybrid.hits.length} 条 · seekdb ${hybrid.searchMs}ms`);
check(
  '新写入的片段可被检索到（刷新生效）',
  hybrid.hits.some((hit) => hit.metadata?.docId === `verify-${stamp}`),
  hybrid.hits.map((hit) => hit.metadata?.docId).join(','),
);

const statements = hybrid.statements ?? [];
const generated = statements.map((statement) => statement.sql).sort((a, b) => b.length - a.length)[0] ?? '';
check(
  '检索由 seekdb 生成并执行单条 SQL',
  /DBMS_HYBRID_SEARCH/i.test(statements.map((statement) => statement.sql).join('\n')) && Boolean(generated),
  `${statements.length} 条语句，最长 ${generated.length} 字符`,
);
check(
  '该 SQL 内同时包含全文与向量候选',
  /match\s*\(/i.test(generated) && /cosine_distance/i.test(generated),
  generated.slice(0, 100),
);
check(
  '融合（RRF）也在同一条 SQL 里完成',
  /_keyword_rank/i.test(generated) && /_semantic_rank/i.test(generated) && /1 \/ \(/i.test(generated),
);

const fulltext = await json('/api/search', {
  method: 'POST',
  headers: { 'content-type': 'application/json' },
  body: JSON.stringify({ vector: randVector(2), text: '浏览器 embedding', mode: 'fulltext', topK: 3 }),
});
check('纯全文检索可用', fulltext.hits.length > 0, `${fulltext.hits.length} 条`);

const vectorOnly = await json('/api/search', {
  method: 'POST',
  headers: { 'content-type': 'application/json' },
  body: JSON.stringify({ vector: randVector(1), text: '', mode: 'vector', topK: 3 }),
});
check('纯向量检索可用', vectorOnly.hits.length > 0, `${vectorOnly.hits.length} 条`);

const failed = checks.filter((item) => !item.passed);
console.log(`\n${checks.length - failed.length}/${checks.length} 项通过`);
process.exit(failed.length === 0 ? 0 : 1);
