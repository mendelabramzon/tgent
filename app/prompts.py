from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Prompt:
    name: str
    role: str
    content: str


class PromptStore:
    """
    Loads JSON prompt files from disk and keeps them in-memory.

    File format (example):
      {
        "role": "system",
        "content": "..."
      }

    Name is the filename without extension.
    """

    def __init__(self, prompts_dir: Path):
        self.prompts_dir = prompts_dir
        self._cache: dict[str, Prompt] = {}

    def list(self) -> list[str]:
        return sorted(self._cache.keys())

    def reload(self) -> None:
        prompts: dict[str, Prompt] = {}
        if not self.prompts_dir.exists():
            raise FileNotFoundError(f"Prompts directory not found: {self.prompts_dir}")

        for path in sorted(self.prompts_dir.glob("*.json")):
            name = path.stem
            data = self._load_json(path)
            role = str(data.get("role", "")).strip()
            content = str(data.get("content", "")).strip()
            if role not in {"system", "user"}:
                raise ValueError(f"Invalid prompt role in {path.name}: {role!r} (expected 'system' or 'user')")
            if not content:
                raise ValueError(f"Empty prompt content in {path.name}")
            prompts[name] = Prompt(name=name, role=role, content=content)

        self._cache = prompts
        logger.info("Prompts loaded: %s", ", ".join(self.list()))

    def get(self, name: str) -> Prompt:
        if name not in self._cache:
            raise KeyError(f"Prompt not found: {name}")
        return self._cache[name]

    def render(self, name: str, **kwargs: Any) -> Prompt:
        base = self.get(name)
        content = base.content.format(**kwargs)
        return Prompt(name=base.name, role=base.role, content=content)

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any]:
        try:
            raw = path.read_text(encoding="utf-8")
            return json.loads(raw)
        except Exception as e:
            raise ValueError(f"Failed to load prompt JSON: {path}") from e


