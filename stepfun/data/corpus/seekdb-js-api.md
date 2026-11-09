# 用 seekdb-js 读写知识库

seekdb-js 是 seekdb 的 JavaScript/TypeScript SDK，两种连接方式共用同一套 API：

```js
import { SeekdbClient, Schema, FulltextIndexConfig, VectorIndexConfig } from 'seekdb';

// 嵌入式：path 指向数据目录；服务端模式改成 host/port/user/password
const client = new SeekdbClient({ path: './data/seekdb', database: 'edgekb' });

const schema = new Schema({
  fulltextIndex: new FulltextIndexConfig('ngram', { ngram_token_size: 2 }),
  vectorIndex: new VectorIndexConfig({
    hnsw: { dimension: 512, distance: 'cosine' },
    embeddingFunction: null,      // 向量由浏览器算好再写入
  }),
});

const collection = await client.createCollection({ name: 'kb_chunks', schema });

await collection.add({
  ids: chunkIds,
  documents: chunkTexts,
  metadatas: chunkMetadata,
  embeddings: chunkVectors,
});

await collection.refresh_index();   // 实际执行 CALL dbms_index_manager.refresh()

const result = await collection.hybridSearch({
  query: { whereDocument: { $contains: '向量数据库' } },
  knn: { queryEmbeddings: [queryVector], nResults: 40 },
  rank: { rrf: { rankWindowSize: 40, rankConstant: 60 } },
  nResults: 10,
});
```

几个容易踩的坑：

- `embeddingFunction: null` 必须显式传，否则 SDK 会去找默认的 embedding 函数，而端侧场景里向量已经算好了。
- 读取已有集合时要写 `client.getCollection({ name, embeddingFunction: null })`，不然会报 `Embedding function 'default-embed' is not registered`。
- 嵌入式模式下数据库要先创建（`AdminClient.createDatabase`），再连上去建集合。
- 同一数据目录只能被一个进程打开，命令行脚本和常驻服务不要同时跑。
