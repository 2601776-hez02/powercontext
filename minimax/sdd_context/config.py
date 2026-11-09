"""Runtime configuration for the SDD context module.

Everything that differs between a developer machine, the graded environment and
CI is resolved here, so the rest of the code never reads ``os.environ``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SAMPLE_REPO = PACKAGE_ROOT / "sample-project"
DEFAULT_RECORDS_DIR = PACKAGE_ROOT / "records"
DEFAULT_ARTIFACTS_DIR = PACKAGE_ROOT / "artifacts"


def _env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name, "").strip()
    return Path(raw).expanduser().resolve() if raw else default


@dataclass(frozen=True)
class SeekDBTarget:
    """Where the engineering-material index lives.

    ``embedded`` opens a local seekdb instance through ``pylibseekdb`` (the
    default, and what the run instructions use). ``server`` talks to a running
    seekdb/MySQL endpoint through the same SQL surface, which is useful when the
    module is deployed next to a shared seekdb instance.
    """

    kind: str
    path: Path | None = None
    host: str | None = None
    port: int = 2881
    user: str = "root"
    password: str = ""
    database: str = "test"

    @property
    def label(self) -> str:
        if self.kind == "embedded":
            return f"embedded-seekdb:{self.path}"
        return f"seekdb-server:{self.host}:{self.port}/{self.database}"


@dataclass(frozen=True)
class Settings:
    home: Path
    records_dir: Path
    artifacts_dir: Path
    sample_repo: Path
    workdir: Path
    seekdb: SeekDBTarget
    powercontext_url: str
    powercontext_token: str | None
    minimax_api_key: str | None
    minimax_base_url: str
    minimax_model: str
    minimax_api_style: str
    minimax_group_id: str | None = None

    @property
    def index_dir(self) -> Path:
        return self.home / "seekdb"


def _seekdb_target(home: Path) -> SeekDBTarget:
    dsn = os.environ.get("SDD_SEEKDB_DSN", "").strip()
    if dsn:
        parsed = urlparse(dsn)
        if parsed.scheme not in {"mysql", "seekdb"}:
            raise ValueError(f"SDD_SEEKDB_DSN must start with mysql:// (got {dsn!r})")
        return SeekDBTarget(
            kind="server",
            host=parsed.hostname or "127.0.0.1",
            port=parsed.port or 2881,
            user=parsed.username or "root",
            password=parsed.password or "",
            database=(parsed.path or "/test").lstrip("/") or "test",
        )
    return SeekDBTarget(kind="embedded", path=_env_path("SDD_SEEKDB_PATH", home / "seekdb"))


def load_settings() -> Settings:
    """Resolve the effective configuration from environment variables."""
    home = _env_path("SDD_HOME", PACKAGE_ROOT / ".runtime")
    token = os.environ.get("SDD_POWERCONTEXT_TOKEN", "").strip() or None
    api_key = os.environ.get("MINIMAX_API_KEY", "").strip() or None
    group_id = os.environ.get("MINIMAX_GROUP_ID", "").strip() or None
    return Settings(
        home=home,
        records_dir=_env_path("SDD_RECORDS_DIR", DEFAULT_RECORDS_DIR),
        artifacts_dir=_env_path("SDD_ARTIFACTS_DIR", DEFAULT_ARTIFACTS_DIR),
        sample_repo=_env_path("SDD_SAMPLE_REPO", DEFAULT_SAMPLE_REPO),
        workdir=_env_path("SDD_WORKDIR", home / "workdir"),
        seekdb=_seekdb_target(home),
        powercontext_url=os.environ.get("SDD_POWERCONTEXT_URL", "http://127.0.0.1:8137").rstrip("/"),
        powercontext_token=token,
        minimax_api_key=api_key,
        minimax_base_url=os.environ.get("MINIMAX_BASE_URL", "https://api.minimaxi.com/v1").rstrip("/"),
        minimax_model=os.environ.get("MINIMAX_MODEL", "MiniMax-M3"),
        minimax_api_style=os.environ.get("MINIMAX_API_STYLE", "chatcompletion_v2").strip(),
        minimax_group_id=group_id,
    )
