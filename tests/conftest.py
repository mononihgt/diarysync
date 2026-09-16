from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:  # allow running tests without installing
    sys.path.insert(0, str(SRC))


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    (tmp_path / "diary").mkdir()
    return tmp_path


DIARY_WITH_SECTIONS = """---
weather: 晴
---

# 日程

- [x] 14:30 - 16:00 #运动 有氧
- [x] 16:11 - 16:16 #运动 跑步机
- [x] 16:18 - 16:53 #运动 跑步机 乳酸阈值
- [x] 17:05 - 19:25 散步+洗澡

# 打卡

- [x] 运动

# 日志

上午：睡觉
下午：锻炼
"""


DIARY_BARE = """---
weather: 晴
---

# 日志

上午：组会
"""
