"""pytest 全局配置：让未安装的源码目录也能直接跑测试。"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

for candidate in (ROOT / "src", ROOT / "tests"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))
