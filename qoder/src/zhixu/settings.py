"""路径与环境配置。

所有路径都从项目根目录推导，可用环境变量覆盖，便于在任意目录运行：

- ``ZHIXU_DATA_DIR``：seekdb 数据目录（默认 ``<项目根>/.zhixu``）
- ``ZHIXU_CORPUS_DIR``：Markdown 语料目录（默认 ``<项目根>/data/corpus``）
- ``ZHIXU_SITE_DIR``：项目网站目录（默认 ``<项目根>/site``）
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# src/zhixu/settings.py -> 项目根
PROJECT_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_DB_NAME = "seekdb"
DEFAULT_DATA_DIR = PROJECT_ROOT / ".zhixu"
DEFAULT_CORPUS_DIR = PROJECT_ROOT / "data" / "corpus"
DEFAULT_SITE_DIR = PROJECT_ROOT / "site"
DEFAULT_REPORT_DIR_NAME = "reports"


def _env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name, "").strip()
    return Path(raw).expanduser().resolve() if raw else default


@dataclass(frozen=True)
class Settings:
    """一次运行所需的全部路径。"""

    project_root: Path
    data_dir: Path
    corpus_dir: Path
    site_dir: Path
    db_name: str = DEFAULT_DB_NAME

    @property
    def db_path(self) -> Path:
        return self.data_dir / self.db_name

    @property
    def report_dir(self) -> Path:
        return self.data_dir / DEFAULT_REPORT_DIR_NAME


def load_settings() -> Settings:
    return Settings(
        project_root=PROJECT_ROOT,
        data_dir=_env_path("ZHIXU_DATA_DIR", DEFAULT_DATA_DIR),
        corpus_dir=_env_path("ZHIXU_CORPUS_DIR", DEFAULT_CORPUS_DIR),
        site_dir=_env_path("ZHIXU_SITE_DIR", DEFAULT_SITE_DIR),
    )
