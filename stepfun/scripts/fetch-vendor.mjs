#!/usr/bin/env node
/**
 * Vendor the browser runtime (Transformers.js + ONNX Runtime Web) into web/vendor/.
 *
 * Why: the demo must work without any CDN access. Only two npm tarballs are
 * downloaded and only the few files the browser actually executes are extracted
 * (flat layout, ~57 MB). No native Node package is installed, so `npm install`
 * stays small and portable.
 *
 * Layout produced:
 *   web/vendor/transformers.min.js                  Transformers.js (self-contained ESM)
 *   web/vendor/ort.webgpu.bundle.min.mjs            ONNX Runtime Web (WebGPU bundle, loaded on demand)
 *   web/vendor/ort-wasm-simd-threaded.jsep.{mjs,wasm}      WebGPU execution provider
 *   web/vendor/ort-wasm-simd-threaded.asyncify.{mjs,wasm}  WASM execution provider
 *   web/vendor/MANIFEST.json
 *
 * Usage:
 *   node scripts/fetch-vendor.mjs [--force]
 */
import fs from 'node:fs';
import path from 'node:path';
import zlib from 'node:zlib';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..');
const VENDOR = path.join(ROOT, 'web', 'vendor');

const TRANSFORMERS_VERSION = '4.3.2';
// 必须与 dist/transformers.min.js 内部记录的 ORT 版本一致（见文件里的 versions.web），
// 否则 WebGPU 胶水模块的导出名对不上，会报 `webgpuInit is not a function`。
const ORT_VERSION = '1.31.0-dev.20260911-2a43ec07e';
const REGISTRIES = (process.env.NPM_REGISTRY || 'https://registry.npmjs.org,https://registry.npmmirror.com')
  .split(',')
  .map((s) => s.trim().replace(/\/$/, ''))
  .filter(Boolean);

const TRANSFORMERS_FILES = {
  'package/dist/transformers.min.js': 'transformers.min.js',
};
const ORT_FILES = {
  'package/dist/ort.webgpu.bundle.min.mjs': 'ort.webgpu.bundle.min.mjs',
  'package/dist/ort-wasm-simd-threaded.jsep.mjs': 'ort-wasm-simd-threaded.jsep.mjs',
  'package/dist/ort-wasm-simd-threaded.jsep.wasm': 'ort-wasm-simd-threaded.jsep.wasm',
  'package/dist/ort-wasm-simd-threaded.asyncify.mjs': 'ort-wasm-simd-threaded.asyncify.mjs',
  'package/dist/ort-wasm-simd-threaded.asyncify.wasm': 'ort-wasm-simd-threaded.asyncify.wasm',
};

/** Minimal tar reader for gzipped npm tarballs (ustar only). */
function readTar(buf) {
  const files = new Map();
  let offset = 0;
  while (offset + 512 <= buf.length) {
    const header = buf.subarray(offset, offset + 512);
    const name = header.subarray(0, 100).toString('utf8').replace(/\0.*$/, '');
    if (!name) break;
    const sizeField = header.subarray(124, 136).toString('utf8').replace(/\0.*$/, '').trim();
    const size = parseInt(sizeField, 8) || 0;
    const prefix = header.subarray(345, 500).toString('utf8').replace(/\0.*$/, '');
    const type = header.subarray(156, 157).toString('utf8');
    const fullName = prefix ? `${prefix}/${name}` : name;
    const dataStart = offset + 512;
    if (type === '0' || type === '') files.set(fullName, buf.subarray(dataStart, dataStart + size));
    offset = dataStart + Math.ceil(size / 512) * 512;
  }
  return files;
}

async function downloadTarball(pkg, version) {
  const shortName = pkg.includes('/') ? pkg.split('/')[1] : pkg;
  const file = `${shortName}-${version}.tgz`;
  const errors = [];
  for (const registry of REGISTRIES) {
    const url = `${registry}/${pkg}/-/${file}`;
    try {
      const res = await fetch(url, { redirect: 'follow' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const buf = Buffer.from(await res.arrayBuffer());
      console.log(`[ok]   ${pkg}@${version} ${(buf.length / 1048576).toFixed(1)} MB <- ${registry}`);
      return buf;
    } catch (error) {
      errors.push(`${url}: ${error.message}`);
    }
  }
  throw new Error(`cannot download ${pkg}@${version}\n${errors.join('\n')}`);
}

function digest(buf) {
  return { bytes: buf.length, sha256: crypto.createHash('sha256').update(buf).digest('hex') };
}

function extract(files, mapping, manifest) {
  for (const [entry, name] of Object.entries(mapping)) {
    const data = files.get(entry);
    if (!data) throw new Error(`${entry} missing from tarball`);
    fs.writeFileSync(path.join(VENDOR, name), data);
    manifest.files[name] = digest(data);
    console.log(`[ok]   web/vendor/${name} ${(data.length / 1048576).toFixed(2)} MB`);
  }
}

async function main() {
  const required = [...Object.values(TRANSFORMERS_FILES), ...Object.values(ORT_FILES)];
  const ready = required.every((name) => fs.existsSync(path.join(VENDOR, name)));
  if (ready && !process.argv.includes('--force')) {
    console.log('[skip] web/vendor already populated (use --force to re-fetch)');
    return;
  }
  fs.mkdirSync(VENDOR, { recursive: true });
  console.log(`fetching vendor assets from ${REGISTRIES.join(', ')}`);
  const [tfTar, ortTar] = await Promise.all([
    downloadTarball('@huggingface/transformers', TRANSFORMERS_VERSION),
    downloadTarball('onnxruntime-web', ORT_VERSION),
  ]);
  const manifest = { generatedAt: new Date().toISOString(), packages: {}, files: {} };
  extract(readTar(zlib.gunzipSync(tfTar)), TRANSFORMERS_FILES, manifest);
  extract(readTar(zlib.gunzipSync(ortTar)), ORT_FILES, manifest);
  manifest.packages = { '@huggingface/transformers': TRANSFORMERS_VERSION, 'onnxruntime-web': ORT_VERSION };
  fs.writeFileSync(path.join(VENDOR, 'MANIFEST.json'), `${JSON.stringify(manifest, null, 2)}\n`);
  console.log('[ok]   web/vendor/MANIFEST.json');
  console.log('done');
}

main().catch((error) => {
  console.error(error.message ?? error);
  process.exit(1);
});
