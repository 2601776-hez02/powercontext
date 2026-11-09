# 浏览器里跑 embedding：Transformers.js 与 ONNX Runtime Web

要把“文档向量化”搬到用户设备上，最直接的路径是把 ONNX 模型放进浏览器执行。Transformers.js 提供与 Python transformers 对齐的分词器、模型加载与任务流水线；真正做矩阵乘法的后端是 ONNX Runtime Web（ORT Web）。

一次 embedding 的完整链路是：

1. 分词器把文本编码成 `input_ids` 与 `attention_mask`；
2. ORT Web 用选定的执行后端（WebGPU 或 WASM）跑一遍 BERT 前向；
3. 取最后一层隐状态，做池化（BGE 系列用 CLS 池化）与 L2 归一化，得到 512 维向量。

工程细节：

- 推理放在 Web Worker 里，主线程只负责 UI，长文本导入时页面不会卡住；
- `env.allowRemoteModels = false` 配合 `env.localModelPath` 让模型文件全部来自本机静态目录，断网也能用；
- ORT 的 WASM 二进制默认从 CDN 拉取，离线部署必须把 `wasmPaths` 指到本地文件；
- 第一次推理包含算子编译与内存分配，必须单独预热，否则会把“首次延迟”算进检索指标里。
