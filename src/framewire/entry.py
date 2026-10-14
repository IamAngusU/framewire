"""Minimal entry point: even a damaged recognition install gets a useful message."""

from __future__ import annotations

import os
import sys
import traceback


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    try:
        from .cli import main as cli_main
    except (ImportError, OSError) as exc:
        if os.environ.get("FRAMEWIRE_DEBUG") == "1":
            traceback.print_exc()
        else:
            print(
                f"Framewire could not load its environment ({type(exc).__name__}).\n"
                "Run the Framewire installer again to repair it.",
                file=sys.stderr,
            )
        return 2
    return cli_main(argv)
