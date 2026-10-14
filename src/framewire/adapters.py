"""Install, load and contain local Python adapters."""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Callable
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

from .adapter_api import ADAPTER_NAME, AdapterContext, home_dir
from .outputs import EventBus

EXAMPLE_PACKAGE = "framewire.examples.adapters"
EXAMPLES = ("hands", "voice", "lips", "actions")


def adapter_dir() -> Path:
    return home_dir() / "adapters"


def validate_name(name: str) -> str:
    if not ADAPTER_NAME.fullmatch(name):
        raise ValueError("adapter names use lowercase letters, numbers and underscores")
    return name


def list_installed(folder: Path | None = None) -> list[tuple[str, bool]]:
    folder = folder or adapter_dir()
    if not folder.is_dir():
        return []
    items = []
    for path in folder.iterdir():
        if path.suffix == ".py" and ADAPTER_NAME.fullmatch(path.stem):
            items.append((path.stem, True))
        elif path.name.endswith(".py.disabled"):
            name = path.name.removesuffix(".py.disabled")
            if ADAPTER_NAME.fullmatch(name):
                items.append((name, False))
    return sorted(items)


def install_example(name: str, folder: Path | None = None) -> Path:
    if name not in EXAMPLES:
        raise ValueError(f"unknown example {name}; choose from: {', '.join(EXAMPLES)}")
    folder = folder or adapter_dir()
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / f"{name}.py"
    if destination.exists() or destination.with_suffix(".py.disabled").exists():
        raise FileExistsError(f"{name} is already installed; edit it in {folder}")
    source = resources.files(EXAMPLE_PACKAGE).joinpath(f"{name}.py")
    destination.write_bytes(source.read_bytes())
    config = resources.files(EXAMPLE_PACKAGE).joinpath(f"{name}.toml")
    config_path = folder / f"{name}.toml"
    if config.is_file() and not config_path.exists():
        config_path.write_bytes(config.read_bytes())
    return destination


def create_adapter(name: str, folder: Path | None = None) -> Path:
    name = validate_name(name)
    folder = folder or adapter_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.py"
    if path.exists() or path.with_suffix(".py.disabled").exists():
        raise FileExistsError(f"{name} already exists")
    source = f'''"""{name}: a local Framewire adapter."""


def create(context):
    class {name.title().replace("_", "")}:
        def on_frame(self, frame, now):
            # frame is a BGR numpy array. Emit only JSON-safe values.
            # context.emit("my.signal", "value", confidence=0.9)
            pass

        def on_event(self, event):
            # React to events from other adapters or the camera.
            pass

        def close(self):
            pass

    return {name.title().replace("_", "")}()
'''
    path.write_text(source, encoding="utf-8")
    return path


def set_enabled(name: str, enabled: bool, folder: Path | None = None) -> Path:
    name = validate_name(name)
    folder = folder or adapter_dir()
    enabled_path = folder / f"{name}.py"
    disabled_path = folder / f"{name}.py.disabled"
    source, target = (disabled_path, enabled_path) if enabled else (enabled_path, disabled_path)
    if not source.exists():
        raise FileNotFoundError(f"{name} is not {'disabled' if enabled else 'enabled'}")
    source.rename(target)
    return target


def remove_adapter(name: str, folder: Path | None = None) -> None:
    name = validate_name(name)
    folder = folder or adapter_dir()
    paths = [folder / f"{name}.py", folder / f"{name}.py.disabled"]
    found = next((path for path in paths if path.exists()), None)
    if found is None:
        raise FileNotFoundError(f"{name} is not installed")
    found.unlink()
    (folder / f"{name}.toml").unlink(missing_ok=True)
    # Models and samples under home_dir()/data/name are intentionally retained.


@dataclass
class LoadedAdapter:
    name: str
    instance: Any
    context: AdapterContext


class AdapterManager:
    def __init__(
        self,
        bus: EventBus,
        *,
        folder: Path | None = None,
        selected: set[str] | None = None,
        warn: Callable[[str], None] | None = None,
    ) -> None:
        self.bus = bus
        self.folder = folder or adapter_dir()
        self.selected = selected
        self.warn = warn or (lambda message: None)
        self.loaded: dict[str, LoadedAdapter] = {}
        self.closed = False
        bus.subscribe(self.on_event)

    def _publish(self, name: str, signal: str, data: dict) -> None:
        if not self.closed and name in self.loaded:
            self.bus.emit("adapter.signal", f"adapter:{name}", data=data)

    def start(self) -> None:
        for name, enabled in list_installed(self.folder):
            if not enabled or (self.selected is not None and name not in self.selected):
                continue
            path = self.folder / f"{name}.py"
            try:
                settings_path = self.folder / f"{name}.toml"
                settings = (
                    tomllib.loads(settings_path.read_text(encoding="utf-8"))
                    if settings_path.exists()
                    else {}
                )
                context = AdapterContext(name, home_dir() / "data" / name, settings, self._publish)
                spec = importlib.util.spec_from_file_location(f"_framewire_adapter_{name}", path)
                if spec is None or spec.loader is None:
                    raise RuntimeError("could not load Python module")
                module = importlib.util.module_from_spec(spec)
                sys.modules[spec.name] = module
                spec.loader.exec_module(module)
                instance = module.create(context)
                self.loaded[name] = LoadedAdapter(name, instance, context)
                if hasattr(instance, "start"):
                    instance.start()
                self.bus.emit("adapter.ready", f"adapter:{name}", data={"adapter": name})
            except Exception as exc:
                self._fail(name, exc)

    def _fail(self, name: str, exc: Exception) -> None:
        item = self.loaded.pop(name, None)
        if item is not None:
            item.context._alive = False
            try:
                if hasattr(item.instance, "close"):
                    item.instance.close()
            except Exception as close_exc:
                self.warn(f"Adapter {name} cleanup failed: {type(close_exc).__name__}")
        self.warn(f"Adapter {name} disabled: {type(exc).__name__}: {exc}")
        self.bus.emit(
            "adapter.error",
            f"adapter:{name}",
            data={"adapter": name, "error": type(exc).__name__, "message": str(exc)},
        )

    def on_frame(self, frame, now: float) -> None:
        for name, item in tuple(self.loaded.items()):
            if not hasattr(item.instance, "on_frame"):
                continue
            try:
                item.instance.on_frame(frame, now)
            except Exception as exc:
                self._fail(name, exc)

    def on_event(self, event: dict) -> None:
        if self.closed:
            return
        for name, item in tuple(self.loaded.items()):
            if not hasattr(item.instance, "on_event"):
                continue
            try:
                item.instance.on_event(event)
            except Exception as exc:
                self._fail(name, exc)

    def close(self) -> None:
        self.closed = True
        for name, item in tuple(self.loaded.items()):
            item.context._alive = False
            try:
                if hasattr(item.instance, "close"):
                    item.instance.close()
            except Exception as exc:
                self.warn(f"Adapter {name} cleanup failed: {type(exc).__name__}")
        self.loaded.clear()
