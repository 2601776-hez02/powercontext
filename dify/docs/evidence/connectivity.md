# Dify → 引擎 的连通性（自动生成）

> 本题的 HTTP 节点由 Dify 的 api/worker 容器发出，默认经 ssrf_proxy 出网。
> 这里在**同一台机器**上，从 Dify 容器内部实测三条路径。

## 1) 容器里直连默认地址

```
direct  172.17.0.1:9100 -> 200
```

## 2) 经 ssrf_proxy（Dify HTTP 节点的真实路径）

```
proxy   172.17.0.1:9100 -> 200
```

## 3) 容器里访问公网 demo 后端

```
tunnel  -> 200
```

## 4) 引擎日志里来自容器的请求

```
来自 172.19.0.x（Dify 容器网段）的请求数：897
INFO:     172.19.0.11:53324 - "GET /health HTTP/1.1" 200 OK
INFO:     172.19.0.11:51384 - "GET /health HTTP/1.1" 200 OK
INFO:     172.19.0.8:45072 - "GET /health HTTP/1.1" 200 OK
```

## 结论

* 默认值 `http://172.17.0.1:9100` 在「Dify 与引擎同机、引擎跑在宿主」这种部署下**直连和经代理都是 200**；
* 引擎监听 `0.0.0.0:9100`，容器网段可达；
* 若你的 Dify 跑在 **macOS / Windows 的 Docker Desktop**，`172.17.0.1` 不通，
  把环境变量改成 `http://host.docker.internal:9100`；跑在 Kubernetes 里则用服务名。
* 完全不想起后端时，用 `dsl/wangchuan-tavern.demo.yaml`（连公网，容器里也测过 200）。
