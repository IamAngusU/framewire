import io
import json

from framewire.adapters import (
    AdapterManager,
    create_adapter,
    install_example,
    list_installed,
    remove_adapter,
    set_enabled,
)
from framewire.outputs import EventBus


def test_example_install_manage_and_keep_user_data(tmp_path, monkeypatch):
    monkeypatch.setenv("FRAMEWIRE_HOME", str(tmp_path))
    folder = tmp_path / "adapters"
    path = install_example("actions", folder)
    assert path.exists()
    assert (folder / "actions.toml").exists()
    assert list_installed(folder) == [("actions", True)]
    set_enabled("actions", False, folder)
    assert list_installed(folder) == [("actions", False)]
    set_enabled("actions", True, folder)
    (tmp_path / "data" / "actions").mkdir(parents=True)
    remove_adapter("actions", folder)
    assert list_installed(folder) == []
    assert (tmp_path / "data" / "actions").exists()


def test_adapter_signal_and_fault_isolation(tmp_path, monkeypatch):
    monkeypatch.setenv("FRAMEWIRE_HOME", str(tmp_path))
    folder = tmp_path / "adapters"
    path = create_adapter("custom", folder)
    path.write_text(
        "def create(context):\n"
        "    class Adapter:\n"
        "        def on_frame(self, frame, now):\n"
        "            context.emit('custom.point', 'hello', x=0.5)\n"
        "    return Adapter()\n",
        encoding="utf-8",
    )
    broken = create_adapter("broken", folder)
    broken.write_text(
        "def create(context):\n"
        "    class Adapter:\n"
        "        def on_frame(self, frame, now):\n"
        "            raise RuntimeError('broken detector')\n"
        "    return Adapter()\n",
        encoding="utf-8",
    )
    output = io.StringIO()
    bus = EventBus(stream=output)
    manager = AdapterManager(bus, folder=folder)
    manager.start()
    manager.on_frame(None, 1.0)
    manager.on_frame(None, 2.0)
    manager.close()
    bus.close()
    events = [json.loads(line) for line in output.getvalue().splitlines()]
    assert [event["type"] for event in events].count("adapter.error") == 1
    points = [event for event in events if event["type"] == "adapter.signal"]
    assert len(points) == 2
    assert points[0]["data"] == {
        "adapter": "custom",
        "signal": "custom.point",
        "value": "hello",
        "x": 0.5,
    }
