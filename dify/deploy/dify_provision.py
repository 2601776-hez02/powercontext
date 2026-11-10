#!/usr/bin/env python3
"""忘川酒馆 · Dify 侧的一键配置（只用标准库）。

做的事（每一步都可以单独跑）：

    login        初始化管理员（首次）并登录，把 cookie 落到 .cache/dify.json
    plugin       从 Marketplace 安装模型供应商插件
    credentials  写入模型供应商凭据（API Key / Base URL）
    model-add    给供应商加一个自定义模型名
    models       列出当前可用的 LLM
    import       导入 dsl/ 下的 DSL（新建应用或覆盖已有应用）
    publish      发布工作流
    apikey       创建 API Key（供 /v1/chat-messages 调用）
    chat         用 API Key 打一句话，验证应用能跑
    export       把应用导出成 YAML（对比/归档用）

鉴权说明：Dify 1.19 的 console 接口用 **Cookie + X-CSRF-Token**，登录密码还要先做
一层 Base64（见镜像里的 `libs/encryption.py`）。这些细节都封在这里，评审只要填环境变量。

环境变量（都有默认值，默认值只用于本地体验）：
    DIFY_BASE              http://127.0.0.1:8080
    DIFY_EMAIL             tavern@example.com
    DIFY_PASSWORD          DifyTavern2026      ← 管理员密码
    DIFY_INIT_PASSWORD     DifyTavern2026      ← security.env 里的 INIT_PASSWORD
    DIFY_NAME              酒馆掌柜
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
CACHE = ROOT / ".cache"
CACHE.mkdir(exist_ok=True)
STATE = CACHE / "dify.json"

DEFAULT_BASE = os.environ.get("DIFY_BASE", "http://127.0.0.1:8080")
EMAIL = os.environ.get("DIFY_EMAIL", "tavern@example.com")
PASSWORD = os.environ.get("DIFY_PASSWORD", "DifyTavern2026")
INIT_PASSWORD = os.environ.get("DIFY_INIT_PASSWORD", "DifyTavern2026")
NAME = os.environ.get("DIFY_NAME", "酒馆掌柜")

COOKIE_NAMES = ("csrf_token", "access_token", "refresh_token")


# --------------------------------------------------------------------- 状态


def load_state() -> dict:
    if STATE.exists():
        try:
            return json.loads(STATE.read_text(encoding="utf-8"))
        except ValueError:
            return {}
    return {}


def save_state(**kwargs) -> dict:
    state = load_state()
    state.update({k: v for k, v in kwargs.items() if v is not None})
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return state


# --------------------------------------------------------------------- HTTP


def _cookie_header(cookies: dict[str, str]) -> str:
    return "; ".join(f"{name}={value}" for name, value in cookies.items() if value)


def request(
    base: str,
    path: str,
    *,
    method: str = "GET",
    body: dict | None = None,
    headers: dict | None = None,
    auth: bool = True,
    timeout: int = 120,
) -> tuple[int, dict | str, dict[str, str]]:
    """调用 console API；返回 (状态码, 解析后的响应, 响应头)。"""

    state = load_state()
    url = base.rstrip("/") + "/console/api" + path
    payload = json.dumps(body).encode() if body is not None else None
    request_headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if headers:
        request_headers.update(headers)
    if auth:
        cookies = state.get("cookies") or {}
        cookie = _cookie_header(cookies)
        if cookie:
            request_headers["Cookie"] = cookie
        if cookies.get("csrf_token"):
            request_headers["X-CSRF-Token"] = cookies["csrf_token"]
    req = urllib.request.Request(url, data=payload, method=method, headers=request_headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            text = response.read().decode("utf-8", "replace")
            out_headers = {k.lower(): v for k, v in response.headers.items()}
            out_headers["set-cookie"] = "\n".join(response.headers.get_all("Set-Cookie") or [])
            try:
                return response.status, json.loads(text), out_headers
            except ValueError:
                return response.status, text, out_headers
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", "replace")
        out_headers = {"set-cookie": "\n".join(exc.headers.get_all("Set-Cookie") or [])}
        try:
            return exc.code, json.loads(text), out_headers
        except ValueError:
            return exc.code, text, out_headers


def _extract_cookies(headers: dict[str, str], previous: dict[str, str] | None = None) -> dict[str, str]:
    cookies = dict(previous or {})
    for line in (headers.get("set-cookie") or "").split("\n"):
        first = line.split(";", 1)[0].strip()
        if "=" not in first:
            continue
        name, value = first.split("=", 1)
        if name.strip() in COOKIE_NAMES and value.strip():
            cookies[name.strip()] = value.strip()
    return cookies


# --------------------------------------------------------------------- 登录


def cmd_login(args: argparse.Namespace) -> int:
    base = args.base
    cookies = dict((load_state().get("cookies") or {}))

    # 1) 初始化口令（Dify 1.19 在 /setup 之前要求先验证 INIT_PASSWORD）
    status, init, headers = request(base, "/init", auth=False)
    cookies = _extract_cookies(headers, cookies)
    if isinstance(init, dict) and init.get("status") == "not_started":
        status, payload, headers = request(
            base,
            "/init",
            method="POST",
            body={"password": INIT_PASSWORD},
            auth=False,
            headers={"Cookie": _cookie_header(cookies)} if cookies else None,
        )
        cookies = _extract_cookies(headers, cookies)
        print("[init]", status, json.dumps(payload, ensure_ascii=False)[:200] if isinstance(payload, dict) else payload)
        if status not in (200, 201):
            print("  初始化口令不对：把 DIFY_INIT_PASSWORD 设成 deploy/dify/envs/security.env 里的 INIT_PASSWORD")
            return 1

    # 2) 创建管理员
    status, setup, headers = request(base, "/setup", auth=False, headers={"Cookie": _cookie_header(cookies)} if cookies else None)
    cookies = _extract_cookies(headers, cookies)
    if isinstance(setup, dict) and setup.get("step") == "not_started":
        status, payload, headers = request(
            base,
            "/setup",
            method="POST",
            body={"email": EMAIL, "name": NAME, "password": PASSWORD, "language": "zh-Hans"},
            auth=False,
            headers={"Cookie": _cookie_header(cookies)} if cookies else None,
        )
        cookies = _extract_cookies(headers, cookies)
        print("[setup]", status, json.dumps(payload, ensure_ascii=False)[:200] if isinstance(payload, dict) else payload)

    # 3) 登录：password 先 Base64，之后整个 console 都靠 cookie
    status, payload, headers = request(
        base,
        "/login",
        method="POST",
        body={
            "email": EMAIL,
            "password": base64.b64encode(PASSWORD.encode()).decode(),
            "language": "zh-Hans",
            "remember_me": True,
        },
        auth=False,
        headers={"Cookie": _cookie_header(cookies)} if cookies else None,
    )
    cookies = _extract_cookies(headers, cookies)
    print("[login]", status, json.dumps(payload, ensure_ascii=False)[:200] if isinstance(payload, dict) else payload)
    if status != 200:
        return 1
    save_state(base=base, cookies=cookies, email=EMAIL)
    print("[ok] 登录状态已写入", STATE)
    return 0


def require_auth(base: str) -> None:
    state = load_state()
    if not (state.get("cookies") or {}).get("access_token") or state.get("base") != base:
        if cmd_login(argparse.Namespace(base=base)) != 0:
            raise SystemExit("需要先登录：python deploy/dify_provision.py login")


# ------------------------------------------------------------- 插件与模型


def cmd_plugin(args: argparse.Namespace) -> int:
    base = args.base
    require_auth(base)
    status, payload, _ = request(
        base,
        "/workspaces/current/plugin/install/marketplace",
        method="POST",
        body={"plugin_unique_identifiers": [args.identifier]},
    )
    print("[plugin]", status, json.dumps(payload, ensure_ascii=False)[:300] if isinstance(payload, dict) else payload)
    if status not in (200, 201):
        return 1
    task_id = (payload or {}).get("task_id")
    if task_id:
        for _ in range(120):
            status, task, _ = request(base, f"/workspaces/current/plugin/tasks/{task_id}")
            if isinstance(task, dict) and task.get("status") in ("success", "failed"):
                print("[plugin-task]", task.get("status"), str(task.get("plugins") or task.get("error"))[:200])
                return 0 if task.get("status") == "success" else 1
            time.sleep(2)
    return 0


def cmd_plugins(args: argparse.Namespace) -> int:
    base = args.base
    require_auth(base)
    status, payload, _ = request(base, "/workspaces/current/plugin/list")
    if isinstance(payload, dict):
        for plugin in payload.get("plugins", []):
            print(plugin.get("plugin_id"), plugin.get("version"), plugin.get("installation_status"))
    else:
        print(status, payload)
    return 0


def cmd_credentials(args: argparse.Namespace) -> int:
    """给「自定义模型」写凭据。

    openai_api_compatible 这类插件没有 provider 级凭据，凭据挂在每个模型上，
    所以走的是 /model-providers/<p>/models/credentials，字段名是 endpoint_url
    （不是 base_url），这是 1.19 的插件 schema 决定的。
    """

    base = args.base
    require_auth(base)
    body = {
        "model": args.model,
        "model_type": args.model_type,
        "credentials": json.loads(args.json),
    }
    status, payload, _ = request(
        base,
        f"/workspaces/current/model-providers/{args.provider}/models/credentials",
        method="POST",
        body=body,
    )
    print("[credentials]", status, json.dumps(payload, ensure_ascii=False)[:400] if isinstance(payload, dict) else payload)
    return 0 if status in (200, 201) else 1


def cmd_model_add(args: argparse.Namespace) -> int:
    base = args.base
    require_auth(base)
    status, payload, _ = request(
        base,
        f"/workspaces/current/model-providers/{args.provider}/models",
        method="POST",
        body={
            "model": args.model,
            "model_type": args.model_type,
            "credentials": json.loads(args.credentials or "{}"),
        },
    )
    print("[model-add]", status, json.dumps(payload, ensure_ascii=False)[:400] if isinstance(payload, dict) else payload)
    return 0 if status in (200, 201) else 1


def cmd_models(args: argparse.Namespace) -> int:
    base = args.base
    require_auth(base)
    status, payload, _ = request(base, "/workspaces/current/models/model-types/llm")
    if isinstance(payload, dict):
        for provider in payload.get("data", []):
            for model in provider.get("models", []):
                print(provider.get("provider"), "|", model.get("model"), "|", model.get("model_type"), "|", model.get("status"))
    else:
        print(status, payload)
    return 0


# --------------------------------------------------------------- 应用管理


def cmd_apps(args: argparse.Namespace) -> int:
    base = args.base
    require_auth(base)
    status, payload, _ = request(base, "/apps?page=1&limit=30")
    if isinstance(payload, dict):
        for item in payload.get("data", []):
            print(item.get("id"), item.get("mode"), item.get("name"), "published:", item.get("status"))
    else:
        print(status, payload)
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    base = args.base
    require_auth(base)
    path = pathlib.Path(args.file)
    if not path.is_absolute():
        path = ROOT / path
    content = path.read_text(encoding="utf-8")
    body: dict = {"mode": "yaml-content", "yaml_content": content}
    app_id = "" if getattr(args, "new", False) else (args.app_id or load_state().get("app_id"))
    if app_id:
        body["app_id"] = app_id
    status, payload, _ = request(base, "/apps/imports", method="POST", body=body)
    if status not in (200, 201):
        print("[import-failed]", status, json.dumps(payload, ensure_ascii=False)[:900] if isinstance(payload, dict) else payload)
        return 1
    if isinstance(payload, dict) and payload.get("status") == "failed":
        print("[import-failed]", json.dumps(payload, ensure_ascii=False)[:1400])
        return 1
    app_id = payload.get("app_id") if isinstance(payload, dict) else None
    if not app_id and isinstance(payload, dict) and payload.get("id"):
        task_id = payload["id"]
        for _ in range(60):
            status, task, _ = request(base, f"/apps/imports/{task_id}")
            if isinstance(task, dict) and task.get("status") in ("completed", "failed"):
                payload = task
                app_id = task.get("app_id")
                break
            time.sleep(1)
    save_state(app_id=app_id, app_mode=(payload or {}).get("app_mode") if isinstance(payload, dict) else None)
    print("[import] app_id=", app_id, "mode=", (payload or {}).get("app_mode") if isinstance(payload, dict) else None)
    return 0 if app_id else 1


def cmd_publish(args: argparse.Namespace) -> int:
    base = args.base
    require_auth(base)
    app_id = args.app_id or load_state().get("app_id")
    if not app_id:
        raise SystemExit("不知道 app_id：先 import")
    status, payload, _ = request(base, f"/apps/{app_id}/workflows/publish", method="POST", body={})
    print("[publish]", status, json.dumps(payload, ensure_ascii=False)[:300] if isinstance(payload, dict) else payload)
    return 0 if status in (200, 201) else 1


def cmd_apikey(args: argparse.Namespace) -> int:
    base = args.base
    require_auth(base)
    app_id = args.app_id or load_state().get("app_id")
    if not app_id:
        raise SystemExit("不知道 app_id：先 import")
    status, payload, _ = request(base, f"/apps/{app_id}/api-keys", method="POST", body={})
    if status not in (200, 201) or not isinstance(payload, dict):
        print("[apikey-failed]", status, json.dumps(payload, ensure_ascii=False)[:400] if isinstance(payload, dict) else payload)
        return 1
    key = payload.get("token")
    save_state(api_key=key)
    print("[apikey]", key)
    return 0


"""给 `dify_provision.py doctor` 用的实现片段（由 patch 脚本注入）。

依次检查：Dify 起来了没 / 初始化没 / 登录没 / 模型插件装没 / 模型配没 /
引擎通不通 / seekdb 表建没 / 应用导入了没 / API Key 能不能真的跑一回合。
每一步失败都直接给「怎么修」，免得评审卡在某个环节只能猜。
"""

def _probe_chat(base: str, api_key: str, guest: str = "体检客") -> tuple[bool, str]:
    """真的打一回合：能拿到带「【第 N 回合 · 判定：」的回答才算通过。"""

    import json as _json
    import urllib.error as _urlerror
    import urllib.request as _urlrequest

    body = _json.dumps(
        {"inputs": {"guest": guest}, "query": "要一壶桂花酿。", "response_mode": "blocking", "user": "doctor"}
    ).encode("utf-8")
    req = _urlrequest.Request(
        base.rstrip("/") + "/v1/chat-messages",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
    )
    try:
        with _urlrequest.urlopen(req, timeout=180) as response:
            payload = _json.loads(response.read().decode("utf-8"))
    except _urlerror.HTTPError as exc:
        return False, f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:160]}"
    except Exception as exc:  # pragma: no cover - 网络诊断
        return False, f"{type(exc).__name__}: {exc}"
    answer = str(payload.get("answer") or "")
    ok = "【第" in answer and "判定" in answer
    return ok, answer.replace("\n", " ")[:120]


def cmd_doctor(args) -> int:  # noqa: PLR0912, PLR0915
    base = args.base
    rows: list[tuple[str, str, str]] = []

    def add(name: str, ok: bool, detail: str, fix: str = "") -> None:
        mark = "PASS" if ok else "FAIL"
        rows.append((name, mark, detail if ok else (detail + (f"｜修：{fix}" if fix else ""))))

    # 1. Dify 起来了吗
    try:
        status, init, _ = request(base, "/init", auth=False)
        up = status == 200
        detail = f"{status} {str(init)[:60]}"
    except Exception as exc:  # pragma: no cover
        up, detail = False, f"{type(exc).__name__}: {exc}"
    add("Dify 可达", up, detail, "docker compose up -d（见 scripts/02_dify.sh）")
    if not up:
        return _print_doctor(rows)

    # 2. 初始化了吗
    init_text = json.dumps(init, ensure_ascii=False) if isinstance(init, dict) else str(init)
    initialized = "finished" in init_text
    add("Dify 已初始化", initialized, str(init)[:80], "浏览器打开 :8080 走一遍初始化向导")

    # 3. 登录了吗
    state = load_state()
    cookies = state.get("cookies") or {}
    add(
        "命令行已登录",
        bool(cookies.get("access_token") or cookies.get("csrf_token")),
        f"cookie 数={len(cookies)}",
        "python3 deploy/dify_provision.py login",
    )

    # 4. 模型插件
    plugins_text = ""
    try:
        status, payload, _ = request(base, "/workspaces/current/plugin/list")
        plugins_text = json.dumps(payload, ensure_ascii=False)
    except Exception:  # pragma: no cover
        pass
    plugin_ok = "openai_api_compatible" in plugins_text
    add("模型插件已装", plugin_ok, "openai_api_compatible" if plugin_ok else "未找到",
        "python3 deploy/dify_provision.py plugin")

    # 5. 模型
    models_text = ""
    try:
        status, payload, _ = request(base, "/workspaces/current/models/model-types/llm")
        models_text = json.dumps(payload, ensure_ascii=False)
    except Exception:  # pragma: no cover
        pass
    model_ok = args.model in models_text
    add(f"模型 {args.model} 已配", model_ok, args.model if model_ok else "未找到",
        f"python3 deploy/dify_provision.py credentials --provider {args.provider} "
        f"--model {args.model} --json '{{\"endpoint_url\":\"…\",\"api_key\":\"…\",\"mode\":\"chat\"}}'")

    # 6. 引擎
    engine = args.engine.rstrip("/")
    engine_ok, engine_detail = False, "未响应"
    try:
        with urllib.request.urlopen(engine + "/health", timeout=10) as response:
            health = json.loads(response.read().decode("utf-8"))
        engine_ok = bool(health.get("ok"))
        tables = (health.get("seekdb") or {}).get("tables") or {}
        engine_detail = f"seekdb={((health.get('seekdb') or {}).get('version'))} 表={len(tables)} powercontext={'on' if ((health.get('powercontext') or {}).get('enabled')) else 'off'}"
    except Exception as exc:  # pragma: no cover
        engine_detail = f"{type(exc).__name__}: {exc}"
    add("引擎可达并已建表", engine_ok, engine_detail, "bash scripts/03_engine.sh")

    # 7. 应用 + 发布
    app_id = state.get("app_id") or ""
    app_ok, app_detail = False, "未导入"
    if app_id:
        try:
            status, app, _ = request(base, f"/apps/{app_id}")
            app_ok = status == 200 and bool(app.get("id"))
            app_detail = f"{app.get('name')}（{app_id[:8]}…）"
        except Exception as exc:  # pragma: no cover
            app_detail = f"{type(exc).__name__}: {exc}"
    add("DSL 已导入", app_ok, app_detail,
        "python3 deploy/dify_provision.py import --file dsl/wangchuan-tavern.chatflow.yaml")

    # 8. 真打一回合
    api_key = state.get("api_key") or ""
    if api_key:
        chat_ok, chat_detail = _probe_chat(base, api_key)
        add("应用能跑通一回合（回答含引擎判定）", chat_ok, chat_detail,
            "先看上面几项；回答里没有判定行说明 /facts 节点没跑到")
    else:
        add("应用 API Key", False, "没有 key", "python3 deploy/dify_provision.py apikey")

    return _print_doctor(rows)


def _print_doctor(rows: list[tuple[str, str, str]]) -> int:
    width = max((len(name) for name, _, _ in rows), default=10)
    passed = 0
    print("\n== 忘川酒馆 · 环境体检 ==\n")
    for name, mark, detail in rows:
        passed += mark == "PASS"
        print(f"  [{mark}] {name.ljust(width)}  {detail}")
    print(f"\n  {passed}/{len(rows)} 通过\n")
    return 0 if passed == len(rows) else 1


def cmd_export(args: argparse.Namespace) -> int:
    """导出应用 DSL。Dify 返回的是一个 zip（内含 app.yaml / manifest.yaml），
    所以这里按二进制落盘，不要当文本解码。"""

    base = args.base
    require_auth(base)
    app_id = args.app_id or load_state().get("app_id")
    state = load_state()
    cookies = state.get("cookies") or {}
    url = base.rstrip("/") + f"/console/api/apps/{app_id}/export?include_secret=false"
    req = urllib.request.Request(
        url,
        headers={
            "Cookie": "; ".join(f"{k}={v}" for k, v in cookies.items() if v),
            "X-CSRF-Token": cookies.get("csrf_token", ""),
        },
    )
    out = pathlib.Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            data = response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        print("[export-failed]", exc.code, exc.read().decode("utf-8", "replace")[:300])
        return 1
    out.write_bytes(data)
    print("[export]", status, "->", out, f"({len(data)} bytes)")
    return 0 if status == 200 else 1


# ------------------------------------------------------------------ 调用


def cmd_chat(args: argparse.Namespace) -> int:
    state = load_state()
    key = args.api_key or state.get("api_key") or os.environ.get("DIFY_API_KEY", "")
    if not key:
        raise SystemExit("没有 API Key：先跑 apikey，或传 --api-key")
    body = {
        "inputs": {"guest": args.guest},
        "query": args.message,
        "response_mode": "blocking",
        "conversation_id": args.conversation_id or "",
        "user": args.user,
    }
    url = args.base.rstrip("/") + "/v1/chat-messages"
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        print("[chat-failed]", exc.code, exc.read().decode("utf-8", "replace")[:800])
        return 1
    print(json.dumps(
        {
            "conversation_id": payload.get("conversation_id"),
            "message_id": payload.get("message_id"),
            "answer": payload.get("answer"),
            "usage": (payload.get("metadata") or {}).get("usage"),
        },
        ensure_ascii=False,
        indent=2,
    ))
    save_state(conversation_id=payload.get("conversation_id"))
    return 0


# ------------------------------------------------------------------ 入口


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dify 侧一键配置（忘川酒馆）")
    parser.add_argument("--base", default=DEFAULT_BASE)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("login", help="初始化管理员并登录").set_defaults(func=cmd_login)
    sub.add_parser("apps", help="列出应用").set_defaults(func=cmd_apps)
    sub.add_parser("models", help="列出可用 LLM").set_defaults(func=cmd_models)
    sub.add_parser("plugins", help="列出已安装插件").set_defaults(func=cmd_plugins)

    p_plugin = sub.add_parser("plugin", help="从 Marketplace 安装插件")
    p_plugin.add_argument("--identifier", required=True)
    p_plugin.set_defaults(func=cmd_plugin)

    p_cred = sub.add_parser("credentials", help="写入模型凭据")
    p_cred.add_argument("--provider", required=True)
    p_cred.add_argument("--model", required=True, help="例如 deepseek-flash")
    p_cred.add_argument("--model-type", default="llm")
    p_cred.add_argument("--json", required=True, help='例如 {"endpoint_url":"https://api.deepseek.com/v1","api_key":"sk-...","mode":"chat","context_size":"65536","max_tokens_to_sample":"8192"}')
    p_cred.set_defaults(func=cmd_credentials)

    p_model = sub.add_parser("model-add", help="添加自定义模型名")
    p_model.add_argument("--provider", required=True)
    p_model.add_argument("--model", required=True)
    p_model.add_argument("--model-type", default="llm")
    p_model.add_argument("--credentials", default="{}")
    p_model.set_defaults(func=cmd_model_add)

    p_import = sub.add_parser("import", help="导入 DSL")
    p_import.add_argument("--file", required=True)
    p_import.add_argument("--new", action="store_true", help="强制新建应用，不覆盖已有 app_id")
    p_import.add_argument("--app-id", default="")
    p_import.set_defaults(func=cmd_import)

    p_pub = sub.add_parser("publish", help="发布工作流")
    p_pub.add_argument("--app-id", default="")
    p_pub.set_defaults(func=cmd_publish)

    p_key = sub.add_parser("apikey", help="创建应用 API Key")
    p_key.add_argument("--app-id", default="")
    p_key.set_defaults(func=cmd_apikey)

    p_doctor = sub.add_parser("doctor", help="环境体检：一层层检查到「能跑通一回合」")
    p_doctor.add_argument("--engine", default="http://127.0.0.1:9100")
    p_doctor.add_argument("--provider", default="langgenius/openai_api_compatible/openai_api_compatible")
    p_doctor.add_argument("--model", default="deepseek-flash")
    p_doctor.set_defaults(func=cmd_doctor)

    p_export = sub.add_parser("export", help="导出应用 DSL")
    p_export.add_argument("--app-id", default="")
    p_export.add_argument("--out", default=str(CACHE / "export.yaml"))
    p_export.set_defaults(func=cmd_export)

    p_chat = sub.add_parser("chat", help="用 API Key 打一句话")
    p_chat.add_argument("--message", required=True)
    p_chat.add_argument("--guest", default="无名客")
    p_chat.add_argument("--user", default="verify-script")
    p_chat.add_argument("--conversation-id", default="")
    p_chat.add_argument("--api-key", default="")
    p_chat.set_defaults(func=cmd_chat)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
