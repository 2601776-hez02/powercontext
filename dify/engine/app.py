"""忘川酒馆引擎 —— Dify 通过 HTTP Request 节点调用的后端。

只暴露玩法所需的最小接口；每个响应都是可以直接塞进 LLM 提示词的 JSON。
"""

from __future__ import annotations

import json
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Form, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from . import game, recall
from .config import load
from .db import SeekDB, SeekDBError
from .powercontext import PowerContext

SETTINGS = load()
DB = SeekDB(SETTINGS)
PC = PowerContext(SETTINGS)
BOOTED_AT = time.time()


@asynccontextmanager
async def lifespan(app: FastAPI):
    seed = game.load_seed()
    DB.ensure_schema()
    game.ensure_seed(DB, seed)
    yield


app = FastAPI(
    title="忘川酒馆 · Tavern Engine",
    version="1.0.0",
    description="NPC 会记仇的文字酒馆：seekdb 存世界状态与 NPC 记忆，PowerContext 存跨会话传说。",
    lifespan=lifespan,
)


class StartBody(BaseModel):
    guest: str = Field(default="无名客", max_length=64, description="酒客的名字或代号")


class ActBody(BaseModel):
    guest: str = Field(default="无名客", max_length=64)
    utterance: str = Field(min_length=1, max_length=500, description="玩家这一回合说的话")
    # Dify 的 HTTP 节点会把 JSON 当字符串带上；这里两种都收，解析失败就当没给
    parsed: dict[str, Any] | str | None = Field(
        default=None, description="Dify LLM 节点解析出的结构化动作（对象或 JSON 字符串，可选）"
    )

    def parsed_hint(self) -> dict[str, Any] | None:
        value = self.parsed
        if isinstance(value, dict):
            return value
        if isinstance(value, str) and value.strip():
            text = value.strip()
            # 模型偶尔会裹一层 ```json 围栏，剥掉再解析
            if text.startswith("```"):
                text = text.strip("`")
                if text.lower().startswith("json"):
                    text = text[4:]
            try:
                loaded = json.loads(text.strip())
            except ValueError:
                return None
            return loaded if isinstance(loaded, dict) else None
        return None


class MindBody(BaseModel):
    guest: str = Field(default="无名客", max_length=64)
    npc: str = Field(default="aruo", max_length=32)
    q: str = Field(default="", max_length=200, description="可选：一句原话，引擎自己从里面认出要看谁")
    limit: int = Field(default=6, ge=1, le=20)


class ResetBody(BaseModel):
    guest: str = Field(min_length=1, max_length=64)


class CloseBody(BaseModel):
    guest: str = Field(default="无名客", max_length=64)


def _guard() -> None:
    try:
        DB.ensure_schema()
    except SeekDBError as exc:  # pragma: no cover
        raise HTTPException(status_code=503, detail=str(exc))


@app.get("/")
def index() -> dict[str, Any]:
    return {
        "name": "忘川酒馆 · Tavern Engine",
        "version": "1.0.0",
        "endpoints": [
            "GET  /health",
            "POST /session/start {guest}",
            "POST /act {guest, utterance, parsed?}",
            "POST /session/close {guest}",
            "GET  /mind?guest=&npc=",
            "GET  /ledger?guest=",
            "POST /admin/reset {guest}",
            "GET  /debug/explain?guest=&npc=&q=",
            "GET  /debug/tables",
        ],
    }


@app.get("/health")
def health() -> dict[str, Any]:
    info: dict[str, Any] = {"ok": False, "uptime_s": round(time.time() - BOOTED_AT, 1)}
    try:
        DB.ensure_schema()
        seed = game.load_seed()
        game.ensure_seed(DB, seed)
        info.update(
            {
                "ok": True,
                "seekdb": {"version": DB.version(), "dsn": SETTINGS.masked()["seekdb"], "tables": DB.table_counts()},
                "powercontext": PC.health(),
                "config": {
                    "start_coins": SETTINGS.start_coins,
                    "max_turns": SETTINGS.max_turns,
                    "gossip": SETTINGS.remote_tint,
                },
            }
        )
    except Exception as exc:  # pragma: no cover - 只用于诊断
        info["error"] = str(exc)
    return info


@app.post("/session/start")
def session_start(body: StartBody) -> dict[str, Any]:
    _guard()
    return game.start(body.guest, db=DB, pc=PC, settings=SETTINGS)


@app.post("/act")
def act(body: ActBody) -> dict[str, Any]:
    _guard()
    return game.act(body.guest, body.utterance, db=DB, pc=PC, settings=SETTINGS, hint=body.parsed_hint())


@app.post("/act/form")
def act_form(
    guest: str = Form("无名客"),
    utterance: str = Form(...),
    parsed: str = Form(""),
) -> dict[str, Any]:
    """表单版 /act：Dify 的 HTTP 节点用 x-www-form-urlencoded，玩家打引号也不会破坏 JSON。"""

    _guard()
    hint = None
    if parsed.strip():
        try:
            loaded = json.loads(parsed)
            hint = loaded if isinstance(loaded, dict) else None
        except ValueError:
            hint = None
    return game.act(guest, utterance, db=DB, pc=PC, settings=SETTINGS, hint=hint)


@app.post("/mind/form")
def mind_form(
    guest: str = Form("无名客"),
    npc: str = Form("aruo"),
    q: str = Form(""),
    limit: int = Form(6),
) -> dict[str, Any]:
    _guard()
    return game.read_mind(guest, npc, db=DB, settings=SETTINGS, limit=max(1, min(int(limit), 20)), text=q)


@app.post("/facts/form")
def facts_form(guest: str = Form("无名客")):
    """最近一回合的事实线（纯文本）。Dify 的 Answer 节点把它拼在叙事后面，
    这样"成功/失败 + 状态行"是引擎写的，模型改不了。"""

    _guard()
    text = game.facts(guest, db=DB, pc=PC)
    return PlainTextResponse(text + ("\n" if text else ""))


@app.post("/session/close")
def session_close(body: CloseBody) -> dict[str, Any]:
    _guard()
    return game.close_session(body.guest, db=DB, pc=PC, settings=SETTINGS)


@app.get("/mind")
def mind(guest: str = "无名客", npc: str = "aruo", limit: int = 8, q: str = "") -> dict[str, Any]:
    """读心：q 可以是一句原话，引擎自己从里面认出要看谁。"""

    _guard()
    return game.mind(guest, npc, db=DB, limit=max(1, min(limit, 20)), text=q)


@app.post("/mind")
def mind_post(body: MindBody) -> dict[str, Any]:
    """读心（POST 版）：Dify 的 HTTP 节点用它，中文参数不用自己拼 URL。"""

    _guard()
    return game.read_mind(body.guest, body.npc, db=DB, settings=SETTINGS, limit=body.limit, text=body.q)


@app.get("/ledger")
def ledger(guest: str = "无名客", limit: int = 10) -> dict[str, Any]:
    _guard()
    return game.ledger(guest, db=DB, limit=max(1, min(limit, 50)))


@app.post("/admin/reset")
def admin_reset(body: ResetBody) -> dict[str, Any]:
    _guard()
    return game.reset(body.guest, db=DB)


@app.get("/debug/explain")
def debug_explain(guest: str = "无名客", npc: str = "aruo", q: str = "偷玉佩") -> dict[str, Any]:
    _guard()
    plan = recall.explain(DB, guest=guest, npc=npc, query=q)
    plan["guest"] = guest
    plan["npc"] = npc
    plan["query"] = q
    return plan


class SqlBody(BaseModel):
    query: str = Field(min_length=1, max_length=2000)


@app.post("/debug/sql")
def debug_sql(body: SqlBody) -> list[dict[str, Any]] | dict[str, Any]:
    """只读 SQL（自检用）：只放行 SELECT / SHOW / EXPLAIN。"""

    _guard()
    query = body.query.strip()
    if not query.lower().startswith(("select", "show", "explain")):
        raise HTTPException(status_code=400, detail="only read-only statements are allowed")
    try:
        rows = DB.query(query)
        DB.conn.commit()
        return rows
    except Exception as exc:  # pragma: no cover - 诊断
        return {"error": str(exc)}


@app.get("/debug/tables")
def debug_tables() -> dict[str, Any]:
    _guard()
    tables = ("tw_memory", "tw_event", "tw_player", "tw_npc", "tw_item", "tw_legend")
    return {
        "counts": DB.table_counts(),
        "ddl": {table: DB.show_create(table) for table in tables},
    }
