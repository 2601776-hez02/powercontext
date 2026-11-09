from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from zhixu.store import Store  # noqa: E402


def pytest_configure(config: pytest.Config) -> None:
    """允许把临时目录指到大磁盘：seekdb 需要目标盘有足够剩余空间。"""

    override = os.environ.get("ZHIXU_TEST_TMPDIR", "").strip()
    if override:
        target = Path(override)
        target.mkdir(parents=True, exist_ok=True)
        config.option.basetemp = str(target)


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    handle = Store(tmp_path / "seekdb-data")
    handle.open()
    try:
        yield handle
    finally:
        handle.close()


@pytest.fixture()
def corpus_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "corpus"
    directory.mkdir()
    (directory / "01-hnsw.md").write_text(
        """---
title: HNSW 参数笔记
kind: reading
tags: HNSW, 召回率
---

# HNSW 参数笔记

## efSearch

HNSW 的 efSearch 控制候选队列长度，值越大召回率越高，查询延迟也随之上升。

## 建图参数

efConstruction 影响建图质量，M 控制每个节点的邻居数。
""",
        encoding="utf-8",
    )
    (directory / "02-weekly.md").write_text(
        """---
title: 周报模板
kind: workflow
tags: 周报
---

# 周报模板

## 四段结构

周报包含概览、本周完成、进行中与风险、下周计划四段，每条完成项都要带证据链接。
""",
        encoding="utf-8",
    )
    (directory / "03-tiny.md").write_text(
        """---
title: 极短资料
kind: note
---

# 极短资料

## 空小节

## 只有一句

一句话。
""",
        encoding="utf-8",
    )
    return directory
