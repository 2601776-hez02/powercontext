/**
 * 端侧 embedding Worker（ES module worker）。
 *
 * - 用 Transformers.js（ONNX Runtime Web）在浏览器里跑 bge-small-zh-v1.5。
 * - 后端优先级：WebGPU → WASM；显式选择时被强制，失败则自动降级并在 UI 提示。
 * - 量化精度：INT8（model_quantized.onnx）/ FP16（model_fp16.onnx）/ FP32（model.onnx）。
 * - 模型与 ORT WASM 都从本机读取（env.allowRemoteModels = false），不访问任何 CDN。
 */
import { pipeline, env } from '/vendor/transformers.min.js';

let extractor = null;
let state = { device: null, dtype: null, modelId: null, loadMs: 0, warmupMs: 0, adapter: null };

self.addEventListener('message', (event) => {
  const data = event.data ?? {};
  if (data.type === 'init') {
    initPipeline(data).catch((error) => {
      self.postMessage({ type: 'init-error', message: String(error?.message ?? error) });
    });
  } else if (data.type === 'embed') {
    embedBatch(data).catch((error) => {
      self.postMessage({ type: 'embed-error', reqId: data.reqId, message: String(error?.message ?? error) });
    });
  }
});

async function initPipeline(request) {
  const started = performance.now();

  // 只用本地模型：仓库里的 models/ 目录，没有远端请求。
  env.allowRemoteModels = false;
  env.allowLocalModels = true;
  env.localModelPath = '/models/';
  env.useBrowserCache = true;
  // ORT 的 wasm 产物全部本地加载。注意：WebGPU 执行器用的是 asyncify 这一支
  // （它的胶水模块导出 webgpuInit），jsep 那一支是纯 CPU 的 SIMD 构建。
  env.backends.onnx.wasm.wasmPaths = {
    mjs: '/vendor/ort-wasm-simd-threaded.asyncify.mjs',
    wasm: '/vendor/ort-wasm-simd-threaded.asyncify.wasm',
  };
  env.backends.onnx.wasm.proxy = false;
  env.backends.onnx.wasm.numThreads = request.threads ?? 0;

  const requestedDevice = request.device ?? 'auto';
  const adapter = request.simulateNoGpu ? null : await describeAdapter();
  const attempts = [];
  if (requestedDevice === 'auto') attempts.push('webgpu', 'wasm');
  else if (requestedDevice === 'webgpu') attempts.push('webgpu', 'wasm');
  else attempts.push('wasm');

  let lastError = null;
  for (const device of attempts) {
    if (device === 'webgpu' && !adapter) {
      lastError = new Error('navigator.gpu 不可用');
      continue;
    }
    try {
      const dtype = normalizeDtype(request.dtype, device);
      const createdAt = performance.now();
      const created = await pipeline('feature-extraction', request.modelId, {
        device,
        dtype,
        progress_callback: (progress) => {
          self.postMessage({ type: 'init-progress', progress });
        },
      });
      const loadMs = performance.now() - createdAt;
      // 预热：第一次推理包含显存/内存分配与 kernel 编译，单独计时，不混入检索指标。
      const warmStarted = performance.now();
      await created(['warmup'], { pooling: 'cls', normalize: true });
      const warmupMs = performance.now() - warmStarted;

      extractor = created;
      state = {
        device,
        dtype,
        modelId: request.modelId,
        loadMs: round(loadMs),
        warmupMs: round(warmupMs),
        adapter,
        fallbackFrom: device !== attempts[0] ? attempts[0] : null,
        ort: {
          numThreads: env.backends.onnx.wasm?.numThreads ?? null,
          simd: env.backends.onnx.wasm?.simd ?? null,
          proxy: env.backends.onnx.wasm?.proxy ?? null,
          hardwareConcurrency: self.navigator?.hardwareConcurrency ?? null,
          crossOriginIsolated: Boolean(self.crossOriginIsolated),
        },
      };
      self.postMessage({
        type: 'init-ok',
        ...state,
        totalMs: round(performance.now() - started),
      });
      return;
    } catch (error) {
      lastError = error;
      self.postMessage({ type: 'init-warn', device, message: String(error?.message ?? error) });
    }
  }
  throw lastError ?? new Error('no usable inference backend');
}

/** FP16 只给 WebGPU 用；WASM 上退化为 INT8。 */
function normalizeDtype(dtype, device) {
  if (!dtype) return device === 'webgpu' ? 'fp16' : 'q8';
  if (dtype === 'fp16' && device !== 'webgpu') return 'q8';
  return dtype;
}

async function describeAdapter() {
  if (typeof navigator === 'undefined' || !('gpu' in navigator)) return null;
  try {
    const adapter = await navigator.gpu.requestAdapter({ powerPreference: 'high-performance' });
    if (!adapter) return null;
    const info = adapter.info ?? (adapter.requestAdapterInfo ? await adapter.requestAdapterInfo() : null);
    const features = [...(adapter.features ?? [])].slice(0, 12);
    return {
      vendor: info?.vendor ?? '',
      architecture: info?.architecture ?? '',
      device: info?.device ?? '',
      description: info?.description ?? '',
      features,
      maxBufferSize: adapter.limits?.maxBufferSize ?? null,
    };
  } catch {
    return null;
  }
}

async function embedBatch(request) {
  if (!extractor) throw new Error('pipeline not initialised');
  const started = performance.now();
  const texts = request.texts ?? [];
  const batchSize = Math.max(1, Number(request.batchSize ?? 8));
  const vectors = [];
  const batchTimes = [];

  for (let offset = 0; offset < texts.length; offset += batchSize) {
    const slice = texts.slice(offset, offset + batchSize);
    const batchStarted = performance.now();
    const output = await extractor(slice, { pooling: 'cls', normalize: true });
    const list = output.tolist();
    for (const vector of list) vectors.push(Float32Array.from(vector));
    batchTimes.push(round(performance.now() - batchStarted));
    self.postMessage({
      type: 'embed-progress',
      reqId: request.reqId,
      done: Math.min(texts.length, offset + slice.length),
      total: texts.length,
    });
  }

  const dim = vectors[0]?.length ?? 0;
  const flat = new Float32Array(vectors.length * dim);
  vectors.forEach((vector, index) => flat.set(vector, index * dim));
  const ms = round(performance.now() - started);

  self.postMessage(
    {
      type: 'embed-ok',
      reqId: request.reqId,
      count: vectors.length,
      dim,
      ms,
      batchTimes,
      devices: state,
      vectors: flat,
    },
    [flat.buffer],
  );
}

function round(value) {
  return Math.round(value * 100) / 100;
}
