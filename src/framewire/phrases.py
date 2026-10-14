"""CLI-managed phrase mappings for the optional voice adapter."""

from __future__ import annotations

import json
import re
from pathlib import Path

from .adapter_api import home_dir
from .commands import normalize_phrase


def preferences_path() -> Path:
    return home_dir() / "data" / "voice" / "phrases.json"


def load_preferences(path: Path | None = None) -> dict:
    path = path or preferences_path()
    if not path.exists():
        return {"language": None, "commands": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != "framewire.phrases.v1" or not isinstance(data.get("commands"), dict):
        raise ValueError("phrase file has an unsupported schema")
    return {"language": data.get("language"), "commands": data["commands"]}


def save_preferences(data: dict, path: Path | None = None) -> None:
    path = path or preferences_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(
        json.dumps({"schema": "framewire.phrases.v1", **data}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp.replace(path)


def add_phrase(phrase: str, action: str) -> None:
    if not normalize_phrase(phrase) or len(phrase) > 120:
        raise ValueError("phrase must contain words and at most 120 characters")
    if not action or len(action) > 80:
        raise ValueError("action must contain 1–80 characters")
    data = load_preferences()
    normalized = normalize_phrase(phrase)
    for existing in tuple(data["commands"]):
        if normalize_phrase(existing) == normalized:
            del data["commands"][existing]
    data["commands"][phrase] = action
    save_preferences(data)


def remove_phrase(phrase: str) -> bool:
    data = load_preferences()
    for existing in tuple(data["commands"]):
        if normalize_phrase(existing) == normalize_phrase(phrase):
            del data["commands"][existing]
            save_preferences(data)
            return True
    return False


def set_language(language: str) -> None:
    if language != "auto" and not re.fullmatch(r"[a-z]{2,3}", language):
        raise ValueError("use 'auto' or an ISO language code such as de, en or es")
    data = load_preferences()
    data["language"] = language
    save_preferences(data)
