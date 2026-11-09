#!/usr/bin/env node
/**
 * 由 bench/results/summary.json 生成 docs/measurements.md（Markdown 表格）。
 * 用法：node scripts/report.mjs [--in=bench/results/summary.json] [--out=docs/measurements.md]
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..');
const args = process.argv.slice(2);
const arg = (name, fallback) => {
  const entry = args.find((item) => item.startsWith(`--${name}=`));
  return entry ? path.resolve(ROOT, entry.slice(name.length + 3)) : fallback;
};
const input = arg('in', path.join(ROOT, 'bench', 'results', 'summary.json'));
const output = arg('out', path.join(ROOT, 'docs', 'measurements.md'));

const summary = JSON.parse(fs.readFileSync(input, 'utf8'));
const configs = summary.configs;
const mb = (bytes) => (bytes / 1048576).toFixed(2);
const ms = (value) => (Math.round(value * 10) / 10).toFixed(1);
const pct = (value) => (value === null || value === undefined ? '-' : value.toFixed(3));

const first = configs[0];
const env = [
  first?.adapter ? `WebGPU 适配器：${[first.adapter.vendor, first.adapter.architecture].filter(Boolean).join(' / ') || '未知'}` : null,
  first?.ort ? `ORT：线程 ${first.ort.numThreads}、跨源隔离 ${first.ort.crossOriginIsolated}、hardwareConcurrency ${first.ort.hardwareConcurrency}` : null,
].filter(Boolean);

const lines = [];
lines.push('# 实测数据（由脚本生成）');
lines.push('');
lines.push(`生成时间：${summary.generatedAt} ｜ 每档 ${summary.rounds} 轮 ｜ 查询集 32 条（20 条关键词类 + 12 条改写类）`);
lines.push('');
for (const line of env) lines.push(`- ${line}`);
lines.push('- 计时口径：模型加载/预热、文档向量化、写入、索引刷新、查询向量化、seekdb 检索、端到端，均取中位数；P95 为端到端 P95。');
lines.push('');

lines.push('## 1. 模型体积与加载');
lines.push('');
lines.push('| 配置 | 精度 | 模型体积 | 相对 FP32 压缩比 | 加载(ms) | 预热(ms) | 实际后端 |');
lines.push('| --- | --- | ---: | ---: | ---: | ---: | --- |');
for (const config of configs) {
  const bytes = config.modelBytes;
  lines.push(
    `| ${config.label} | ${config.dtype} | ${mb(bytes.total)} MB | ${bytes.ratio.toFixed(2)}× | ${ms(config.loadMs)} | ${ms(config.warmupMs)} | ${config.device}${config.fallbackFrom ? `（从 ${config.fallbackFrom} 降级）` : ''} |`,
  );
}
lines.push('');
lines.push('> 体积口径：`config.json` + tokenizer 三个文件 + 对应精度的 ONNX 权重。仅算 ONNX 权重时 FP32 90.46 MB / FP16 45.30 MB / INT8 22.90 MB（3.95×）。');
lines.push('');

lines.push('## 2. 文档向量化（26 个片段）');
lines.push('');
lines.push('| 配置 | 向量化(ms) | 吞吐(块/s) | 写入(ms) | 索引刷新(ms) |');
lines.push('| --- | ---: | ---: | ---: | ---: |');
for (const config of configs) {
  const chunks = config.import.chunks;
  const rate = chunks / (config.import.embedMs / 1000);
  lines.push(`| ${config.label} | ${ms(config.import.embedMs)} | ${rate.toFixed(1)} | ${ms(config.import.writeMs)} | ${ms(config.import.refreshMs)} |`);
}
lines.push('');

lines.push('## 3. 检索质量与延迟（混合 / 向量 / 全文）');
lines.push('');
lines.push('| 配置 | 模式 | Recall@5 | Hit@1 | MRR | 查询向量化(ms) | seekdb(ms) | 端到端(ms) | P95(ms) |');
lines.push('| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |');
for (const config of configs) {
  for (const row of config.bench) {
    lines.push(
      `| ${config.label} | ${row.mode} | ${pct(row.recallAt5)} | ${pct(row.hitAt1)} | ${pct(row.mrr)} | ${ms(row.queryEmbedMs)} | ${ms(row.dbMs)} | ${ms(row.e2eMs)} | ${ms(row.e2eP95)} |`,
    );
  }
}
lines.push('');

lines.push('## 4. 增量导入与刷新可见性');
lines.push('');
lines.push('| 配置 | 新增片段 | 向量化(ms) | 写入(ms) | 刷新(ms) | 新文档可检索 |');
lines.push('| --- | ---: | ---: | ---: | ---: | :---: |');
for (const config of configs) {
  const incremental = config.incremental;
  lines.push(
    `| ${config.label} | ${incremental.chunks} | ${ms(incremental.embedMs)} | ${ms(incremental.writeMs)} | ${ms(incremental.refreshMs)} | ${config.incrementalProbe.found ? '✅' : '❌'} |`,
  );
}
lines.push('');
lines.push('> 增量测试：在已有 26 个片段的知识库里再导入 2 篇新文档（29 个片段），刷新后用只出现在新文档里的事实提问，检查是否命中。');
lines.push('');

fs.mkdirSync(path.dirname(output), { recursive: true });
fs.writeFileSync(output, `${lines.join('\n')}\n`);
console.log(`wrote ${path.relative(ROOT, output)}（${configs.length} 个配置）`);
