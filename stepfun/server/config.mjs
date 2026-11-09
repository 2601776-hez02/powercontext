import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const ROOT = path.resolve(HERE, '..');

const env = process.env;

/**
 * Server 配置。全部可用环境变量覆盖，默认值面向“本机一键跑起来”。
 */
const settings = {
  host: env.HOST || '127.0.0.1',
  port: Number(env.PORT || 5173),

  /** `embedded`（默认，嵌入式，无需单独部署）或 `server`（连接已运行的 seekdb，MySQL 协议）。 */
  seekdbMode: env.SEEKDB_MODE || (env.SEEKDB_HOST ? 'server' : 'embedded'),
  seekdbPath: path.resolve(ROOT, env.SEEKDB_PATH || 'data/seekdb'),
  seekdbHost: env.SEEKDB_HOST || '127.0.0.1',
  seekdbPort: Number(env.SEEKDB_PORT || 2881),
  seekdbUser: env.SEEKDB_USER || 'root',
  seekdbPassword: env.SEEKDB_PASSWORD || '',
  database: env.SEEKDB_DATABASE || 'edgekb',

  collection: env.SEEKDB_COLLECTION || 'kb_chunks',
  dimension: Number(env.EMBEDDING_DIMENSION || 512),
  distance: env.EMBEDDING_DISTANCE || 'cosine',
  ngramTokenSize: Number(env.FULLTEXT_NGRAM_SIZE || 2),

  /** 混合检索 RRF 参数（由 seekdb 内部的 DBMS_HYBRID_SEARCH 执行）。 */
  rankWindowSize: Number(env.RRF_WINDOW_SIZE || 40),
  rankConstant: Number(env.RRF_CONSTANT || 60),

  /** 模型目录（由 scripts/fetch-models.mjs 填充）。 */
  modelId: env.MODEL_ID || 'Xenova/bge-small-zh-v1.5',
  modelsRoot: path.resolve(ROOT, 'models'),
};

export default settings;
