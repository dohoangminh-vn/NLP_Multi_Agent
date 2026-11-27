"""System/user prompt của các agent (TV3). Mỗi prompt là 1 file .md để GV/nhóm sư phạm sửa không cần đụng code."""

from functools import lru_cache
from pathlib import Path

_DIR = Path(__file__).resolve().parent


@lru_cache(maxsize=None)
def load_prompt(name: str) -> str:
    return (_DIR / f"{name}.md").read_text(encoding="utf-8").strip()
