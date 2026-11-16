from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any


_REPLACE_LOCK = threading.Lock()


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as stream:
        return json.load(stream)


def write_json(path: Path, value: Any) -> None:
    write_text(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        # A unique sibling file keeps concurrent writers from sharing a staging file.
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         dir=path.parent, prefix=path.name + ".", suffix=".tmp",
                                         delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        # Windows may reject simultaneous replacements of the same destination.
        with _REPLACE_LOCK:
            os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)

