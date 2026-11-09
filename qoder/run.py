#!/usr/bin/env python3
"""零安装入口：``python run.py <command>``。

不安装包、不设置 PYTHONPATH，也能从仓库目录直接运行知序：

    python run.py init
    python run.py ask "HNSW 的 efSearch 怎么影响召回率"
    python run.py serve
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from zhixu.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
