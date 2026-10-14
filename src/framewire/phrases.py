"""CLI-managed phrase mappings for the optional voice adapter."""

from __future__ import annotations

import re
from pathlib import Path

from .adapter_api import home_dir
from .commands import normalize_phrase
from .data import read_document, valid_label, write_document


def preferences_path() -> Path:
    return home_dir() / "data" / "voice" / "phrases.json"


def load_preferences(path: Path | None = None) -> dict:
    path = path or preferences_path()
    data = read_document(path, "framewire.phrases.v1")
    if data is None:
        return {"language": None, "commands": {}}
    if not isinstance(data.get("commands"), dict) or any(
        not isinstance(phrase, str) or not normalize_phrase(phrase) or not valid_label(action)
        for phrase, action in data["commands"].items()
    ):
        raise ValueError(f"Invalid phrase mappings in {path}. Restore a valid copy or rename it.")
    language = data.get("language")
    if language is not None and (
        not isinstance(language, str)
        or (language != "auto" and not re.fullmatch(r"[a-z]{2,3}", language))
    ):
        raise ValueError(f"Invalid language in {path}. Use auto or a language code such as de.")
    return {"language": data.get("language"), "commands": data["commands"]}


def save_preferences(data: dict, path: Path | None = None) -> None:
    path = path or preferences_path()
    write_document(path, {"schema": "framewire.phrases.v1", **data})


def add_phrase(phrase: str, action: str) -> None:
    if not normalize_phrase(phrase) or len(phrase) > 120:
        raise ValueError("phrase must contain words and at most 120 characters")
    if not valid_label(action):
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
