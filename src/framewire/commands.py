"""Unicode-aware exact phrase matching for user-defined commands."""

from __future__ import annotations

import re


def normalize_phrase(text: str) -> str:
    return " ".join(re.findall(r"\w+", text.casefold(), flags=re.UNICODE))


def match_command(text: str, commands: dict[str, str]) -> str | None:
    heard = normalize_phrase(text)
    if not heard:
        return None
    for phrase, action in commands.items():
        if normalize_phrase(phrase) == heard:
            return action
    return None
