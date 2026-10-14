"""Bounded, validated JSON files and atomic replacement for personal commands."""

from __future__ import annotations

import json
import math
import os
import tempfile
from pathlib import Path


def read_document(path: Path, schema: str) -> dict | None:
    if not path.exists():
        return None
    if path.stat().st_size > 16_000_000:
        raise ValueError(f"Data file is too large: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (ValueError, UnicodeError) as exc:
        raise ValueError(
            f"Could not read {path}. Restore a valid JSON copy or rename it to start fresh."
        ) from exc
    if not isinstance(data, dict) or data.get("schema") != schema:
        raise ValueError(f"Unsupported data format in {path}. Restore a compatible copy.")
    return data


def write_document(path: Path, data: dict) -> None:
    serialized = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)
    write_text_atomic(path, serialized)


def write_text_atomic(path: Path, serialized: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.stem}-",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(serialized)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def valid_label(value) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= 80
        and bool(value.strip())
        and not any(ord(c) < 32 for c in value)
    )


def valid_vector(value, dimensions: int) -> bool:
    def finite(number) -> bool:
        try:
            return (
                isinstance(number, (int, float))
                and not isinstance(number, bool)
                and math.isfinite(number)
            )
        except OverflowError:
            return False

    return (
        isinstance(value, list)
        and len(value) == dimensions
        and all(finite(number) for number in value)
    )
