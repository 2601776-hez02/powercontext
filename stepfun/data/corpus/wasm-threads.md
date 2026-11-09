# WASM 多线程与跨源隔离

ONNX Runtime Web 的 WASM 后端支持多线程，但前提是页面处于“跨源隔离”状态：WASM 线程需要通过 `SharedArrayBuffer` 共享内存，而浏览器只在 `crossOriginIsolated === true` 时暴露这个 API。要让页面进入跨源隔离，服务端必须同时返回两个响应头：

```
Cross-Origin-Opener-Policy: same-origin
Cross-Origin-Embedder-Policy: require-corp
```

加上之后，所有跨源资源都必须带 `Cross-Origin-Resource-Policy` 或被 CORS 允许，否则会加载失败。对“所有资源都来自同一个本地服务”的应用来说这不是问题，反而顺便堵住了外部脚本注入。

线程数由 `env.backends.onnx.wasm.numThreads` 控制：设为 0 表示交给运行时按 `navigator.hardwareConcurrency` 自动决定，设为 1 表示强制单线程。核数多时多线程能明显提升 WASM 推理吞吐，但在只有 2 到 4 核的轻薄本上收益有限，还可能因为线程调度拖慢交互响应。

实践中建议：默认自动，遇到 `SharedArrayBuffer is not defined` 或线程创建失败时退回单线程，并在界面或日志里记录实际使用的线程数，这样性能数据才有可比性。
