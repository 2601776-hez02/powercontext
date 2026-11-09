"""运行配置：全部来自环境变量，默认值对应仓库里的 docker compose。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str) -> str:
    """环境变量存在就用它（允许空串，例如空密码），不存在才用默认值。"""

    return os.environ[name] if name in os.environ else default


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    return _env(name, "1" if default else "0").lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    # seekdb（OceanBase 协议，默认指向 docker compose 里的 seekdb 容器）
    seekdb_host: str = field(default_factory=lambda: _env("SEEKDB_HOST", "127.0.0.1"))
    seekdb_port: int = field(default_factory=lambda: _env_int("SEEKDB_PORT", 2881))
    seekdb_user: str = field(default_factory=lambda: _env("SEEKDB_USER", "root"))
    seekdb_password: str = field(default_factory=lambda: _env("SEEKDB_PASSWORD", "difyai123456"))
    seekdb_database: str = field(default_factory=lambda: _env("SEEKDB_DATABASE", "test"))
    seekdb_charset: str = field(default_factory=lambda: _env("SEEKDB_CHARSET", "utf8mb4"))

    # PowerContext（记忆层，HTTP 接入；关闭时引擎仍可独立运行）
    powercontext_url: str = field(default_factory=lambda: _env("POWERCONTEXT_URL", "http://127.0.0.1:8010"))
    powercontext_token: str = field(default_factory=lambda: _env("POWERCONTEXT_TOKEN", ""))
    powercontext_enabled: bool = field(default_factory=lambda: _env_bool("POWERCONTEXT_ENABLED", True))
    powercontext_timeout: float = field(default_factory=lambda: float(_env("POWERCONTEXT_TIMEOUT", "8")))
    powercontext_scope_prefix: str = field(
        default_factory=lambda: _env("POWERCONTEXT_SCOPE_PREFIX", "wangchuan-tavern")
    )

    # 玩法参数
    start_coins: int = field(default_factory=lambda: _env_int("TAVERN_START_COINS", 20))
    max_turns: int = field(default_factory=lambda: _env_int("TAVERN_MAX_TURNS", 40))
    recall_per_npc: int = field(default_factory=lambda: _env_int("TAVERN_RECALL_PER_NPC", 3))
    remote_tint: bool = field(default_factory=lambda: _env_bool("TAVERN_GOSSIP", True))

    def masked(self) -> dict[str, str]:
        return {
            "seekdb": f"{self.seekdb_user}@{self.seekdb_host}:{self.seekdb_port}/{self.seekdb_database}",
            "powercontext": self.powercontext_url if self.powercontext_enabled else "disabled",
        }


def load() -> Settings:
    return Settings()
