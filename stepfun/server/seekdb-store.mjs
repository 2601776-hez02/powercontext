/**
 * seekdb 数据层：建库建集合、批量写入、索引刷新、混合检索。
 *
 * 设计要点
 * - 通过 seekdb-js 的 `SeekdbClient` 接入 seekdb；默认嵌入式模式（一条命令启动，无需部署服务端），
 *   也可用 SEEKDB_MODE=server 连接已有的 seekdb / OceanBase。
 * - 向量由浏览器算好后随写入请求一起送上来，服务端只负责写库与检索，不做任何推理。
 * - 检索走 `collection.hybridSearch()`，它由 seekdb 内部的 DBMS_HYBRID_SEARCH 生成并执行**一条** SQL，
 *   在数据库内完成全文候选、向量候选与 RRF 融合；客户端不做两路结果的拼合。
 * - 每条 SQL 都会被记录（statement recorder），UI 可以原样展示检索实际执行的语句与耗时。
 */
import { AdminClient, SeekdbClient, Schema, FulltextIndexConfig, VectorIndexConfig } from 'seekdb';
import settings from './config.mjs';

const now = () => Number(process.hrtime.bigint() / 1000n) / 1000; // ms, monotonic

export class SeekdbStore {
  constructor() {
    this.client = null;
    this.collection = null;
    this.mode = settings.seekdbMode;
    this.statements = [];
    this.replaying = false;
    this.info = {
      mode: this.mode,
      database: settings.database,
      collection: settings.collection,
      path: this.mode === 'embedded' ? settings.seekdbPath : null,
      host: this.mode === 'server' ? `${settings.seekdbHost}:${settings.seekdbPort}` : null,
      version: null,
      dimension: settings.dimension,
      distance: settings.distance,
      ngramTokenSize: settings.ngramTokenSize,
      createdAt: null,
    };
  }

  async open() {
    await this.#connect();
    await this.#ensureCollection();
    this.info.version = await this.#version();
    this.info.createdAt = new Date().toISOString();
    return this.info;
  }

  async #connect() {
    if (this.mode === 'embedded') {
      const admin = new AdminClient({ path: settings.seekdbPath });
      try {
        await admin.createDatabase(settings.database);
      } catch (error) {
        const message = String(error?.message ?? error);
        // 已存在即可，其它错误照常抛出
        if (!/exist/i.test(message)) throw error;
      } finally {
        await admin.close().catch(() => {});
      }
      this.client = new SeekdbClient({ path: settings.seekdbPath, database: settings.database });
    } else {
      this.client = new SeekdbClient({
        host: settings.seekdbHost,
        port: settings.seekdbPort,
        user: settings.seekdbUser,
        password: settings.seekdbPassword,
        database: settings.database,
      });
    }
    this.#instrument();
  }

  /**
   * 记录每次 execute 的 SQL 与耗时，供 UI 展示“检索确实发生在数据库里”。
   * 注意：Collection 内部持有的是 internal client，因此要在这一层埋点，
   * 否则 hybridSearch 内部发出的语句不会被记录到。
   */
  #instrument() {
    const internal = this.client?._delegate?._internal;
    const target = internal ?? this.client;
    const rawExecute = target.execute.bind(target);
    target.execute = async (sql, params) => {
      const started = now();
      try {
        const rows = await rawExecute(sql, params);
        if (!this.replaying) this.#record(sql, params, rows, started);
        return rows;
      } catch (error) {
        if (!this.replaying) this.#record(sql, params, error, started);
        throw error;
      }
    };
  }

  #record(sql, params, result, started) {
    const entry = {
      sql: prettifySql(sql),
      params: previewParams(params),
      ms: round(now() - started),
    };
    if (result instanceof Error) entry.error = String(result.message ?? result);
    else entry.rows = Array.isArray(result) ? result.length : 0;
    this.statements.push(entry);
    if (this.statements.length > 40) this.statements.splice(0, this.statements.length - 40);
  }

  schema() {
    return new Schema({
      fulltextIndex: new FulltextIndexConfig('ngram', { ngram_token_size: settings.ngramTokenSize }),
      vectorIndex: new VectorIndexConfig({
        hnsw: { dimension: settings.dimension, distance: settings.distance, type: 'hnsw' },
        embeddingFunction: null, // 向量全部由浏览器端计算
      }),
    });
  }

  async #ensureCollection() {
    const exists = await this.client.hasCollection(settings.collection);
    if (exists) {
      this.collection = await this.client.getCollection({ name: settings.collection, embeddingFunction: null });
      return;
    }
    this.collection = await this.client.createCollection({ name: settings.collection, schema: this.schema() });
  }

  async #version() {
    try {
      const rows = await this.client.execute('SELECT VERSION() AS version');
      return rows?.[0]?.version ?? null;
    } catch {
      return null;
    }
  }

  /** 写入一批片段（同 id 覆盖写，天然支持增量重导）。 */
  async addChunks(chunks, { batchSize = 64, refresh = true } = {}) {
    if (!Array.isArray(chunks) || chunks.length === 0) {
      return { inserted: 0, batches: 0, writeMs: 0, refreshMs: 0 };
    }
    const started = now();
    let batches = 0;
    for (let i = 0; i < chunks.length; i += batchSize) {
      const slice = chunks.slice(i, i + batchSize);
      // upsert：同一片段 id 重复导入时覆盖，避免 “Duplicated primary key”
      await this.collection.upsert({
        ids: slice.map((chunk) => chunk.id),
        documents: slice.map((chunk) => chunk.text),
        metadatas: slice.map((chunk) => ({
          docId: chunk.docId,
          title: chunk.title,
          source: chunk.source ?? '',
          chunkIndex: chunk.chunkIndex ?? 0,
          chars: chunk.text.length,
        })),
        embeddings: slice.map((chunk) => chunk.vector),
      });
      batches += 1;
    }
    const writeMs = now() - started;

    let refreshMs = 0;
    if (refresh) {
      const refreshStarted = now();
      await this.collection.refresh_index();
      refreshMs = now() - refreshStarted;
    }
    return { inserted: chunks.length, batches, writeMs: round(writeMs), refreshMs: round(refreshMs) };
  }

  /**
   * 检索入口。
   * mode=hybrid  : 单条 SQL，在 seekdb 内部完成全文 + 向量 + RRF（DBMS_HYBRID_SEARCH）
   * mode=vector  : 纯向量（近邻索引）
   * mode=fulltext: 纯全文（MATCH ... AGAINST + 相关度排序，单条 SQL）
   */
  async search({ text = '', vector, mode = 'hybrid', topK = 10, rankWindowSize, rankConstant, includeSql = true }) {
    if (includeSql) this.statements = [];
    const started = now();
    let hits = [];
    let rows = [];

    if (mode === 'hybrid') {
      const result = await this.collection.hybridSearch({
        query: text ? { whereDocument: { $contains: text } } : undefined,
        knn: { queryEmbeddings: [vector], nResults: rankWindowSize ?? settings.rankWindowSize },
        rank: {
          rrf: {
            rankWindowSize: rankWindowSize ?? settings.rankWindowSize,
            rankConstant: rankConstant ?? settings.rankConstant,
          },
        },
        nResults: topK,
        include: ['documents', 'metadatas', 'distances'],
      });
      rows = repack(result);
      hits = rows.map((row) => toHit(row, 'rrf'));
    } else if (mode === 'vector') {
      const result = await this.collection.query({
        queryEmbeddings: [vector],
        nResults: topK,
        approximate: true,
        include: ['documents', 'metadatas', 'distances'],
      });
      rows = repack(result);
      hits = rows.map((row) => toHit(row, 'distance'));
    } else if (mode === 'fulltext') {
      hits = await this.#fulltextOnly(text, topK);
    } else {
      throw new Error(`unknown search mode: ${mode}`);
    }

    const searchMs = round(now() - started);
    return {
      mode,
      hits,
      searchMs,
      sql: includeSql ? this.statements.map((statement) => statement.sql) : undefined,
      statements: includeSql ? this.statements.slice() : undefined,
      rank: mode === 'hybrid' ? { rankWindowSize: rankWindowSize ?? settings.rankWindowSize, rankConstant: rankConstant ?? settings.rankConstant } : undefined,
    };
  }

  /** 纯全文检索：单条 SQL，排序由 seekdb 用 MATCH 相关度完成。 */
  async #fulltextOnly(text, topK) {
    if (!text) return [];
    const table = await this.#tableName();
    const sql = `SELECT _id, document, metadata, MATCH(document) AGAINST (? IN NATURAL LANGUAGE MODE) AS relevance
       FROM \`${table}\`
      WHERE MATCH(document) AGAINST (? IN NATURAL LANGUAGE MODE) > 0
      ORDER BY relevance DESC
      LIMIT ?`;
    const rows = (await this.client.execute(sql, [text, text, topK])) ?? [];
    return rows.map((row) => ({
      id: String(row._id),
      score: Number(row.relevance ?? 0),
      scoreKind: 'relevance',
      text: row.document ?? '',
      metadata: typeof row.metadata === 'string' ? safeJson(row.metadata) : row.metadata ?? {},
    }));
  }

  async #tableName() {
    // seekdb-js 的集合表名为 `c$v2$<collectionId>`
    if (this.collection?.collectionId) return `c$v2$${this.collection.collectionId}`;
    const rows = await this.client.execute(
      `SELECT table_name FROM information_schema.tables WHERE table_schema = ? AND table_name LIKE ? LIMIT 1`,
      [settings.database, 'c$v2$%'],
    );
    const name = rows?.[0]?.table_name ?? rows?.[0]?.TABLE_NAME;
    if (name) return name;
    throw new Error('collection table not found');
  }

  /** 文档清单（按 docId 聚合），用于 UI 展示当前知识库内容。 */
  async listDocs(limit = 2000) {
    const result = await this.collection.get({ limit, include: ['metadatas'] });
    const docs = new Map();
    for (const metadata of result.metadatas ?? []) {
      if (!metadata) continue;
      const key = metadata.docId ?? 'unknown';
      const entry = docs.get(key) ?? { docId: key, title: metadata.title ?? key, chunks: 0, chars: 0 };
      entry.chunks += 1;
      entry.chars += Number(metadata.chars ?? 0);
      docs.set(key, entry);
    }
    return [...docs.values()].sort((a, b) => String(a.docId).localeCompare(String(b.docId)));
  }

  async stats() {
    const chunks = await this.collection.count();
    const docs = await this.listDocs();
    return { ...this.info, chunks, docs: docs.length, docList: docs };
  }

  /** 清空知识库：删除集合并按同一 schema 重建。 */
  async reset() {
    await this.client.deleteCollection(settings.collection);
    this.collection = await this.client.createCollection({ name: settings.collection, schema: this.schema() });
    this.statements = [];
    return this.stats();
  }

  /**
   * 关闭：嵌入式 seekdb 的原生库在 Windows 上会 hold 住进程退出，
   * 这里先尝试正常关闭，再兜底强制结束，保证 Ctrl+C 立刻生效。
   */
  async close() {
    try {
      await this.client?.close?.();
    } catch {
      /* ignore */
    }
  }
}

function repack(result) {
  const ids = result.ids?.[0] ?? [];
  const documents = result.documents?.[0] ?? [];
  const metadatas = result.metadatas?.[0] ?? [];
  const distances = result.distances?.[0] ?? [];
  return ids.map((id, index) => ({
    id: String(id),
    text: documents[index] ?? '',
    metadata: metadatas[index] ?? {},
    distance: distances[index] ?? null,
  }));
}

function toHit(row, scoreKind) {
  return {
    id: row.id,
    score: row.distance ?? 0,
    scoreKind,
    text: row.text,
    metadata: row.metadata,
  };
}

function prettifySql(sql) {
  return String(sql).replace(/\s+/g, ' ').trim();
}

function previewParams(params) {
  if (!Array.isArray(params)) return undefined;
  return params.map((value) => {
    if (Array.isArray(value)) return `[${value.length} dims]`;
    if (typeof value === 'string' && value.length > 120) return `${value.slice(0, 117)}...`;
    return value;
  });
}

function safeJson(text) {
  try {
    return JSON.parse(text);
  } catch {
    return {};
  }
}

function round(value) {
  return Math.round(value * 1000) / 1000;
}

export default SeekdbStore;
