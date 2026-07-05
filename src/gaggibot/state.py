"""Tiny atomic JSON state file (questionnaire defaults + resume data)."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


class State:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._data: dict[str, Any] = {}
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text())
            except (json.JSONDecodeError, OSError):
                self._data = {}

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value
        self._flush()

    def update(self, **kwargs: Any) -> None:
        self._data.update(kwargs)
        self._flush()

    def _flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".state-")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(self._data, fh, indent=1)
            os.replace(tmp, self.path)
        except BaseException:
            os.unlink(tmp)
            raise
