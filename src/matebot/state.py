"""Tiny atomic JSON state file (questionnaire defaults + resume data).

An unwritable state dir (classic case: a root-owned ``./data`` bind mount
while the Docker image runs as uid 1000) must not take the bot down — state
degrades to in-memory with a loud log line instead of every write raising.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class State:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._data: dict[str, Any] = {}
        self._warned = False
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

    @property
    def persistent(self) -> bool:
        """True when the state file is actually writable (probes once)."""
        try:
            self._write()
        except OSError:
            return False
        return True

    def _flush(self) -> None:
        try:
            self._write()
        except OSError as exc:
            if not self._warned:
                self._warned = True
                log.error(
                    "state dir %s is not writable (%s) - keeping state in memory only. "
                    "If this runs in Docker, make the mounted state dir writable by "
                    "uid 1000: chown -R 1000:1000 <host dir>",
                    self.path.parent, exc,
                )

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".state-")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(self._data, fh, indent=1)
            os.replace(tmp, self.path)
        except BaseException:
            os.unlink(tmp)
            raise
