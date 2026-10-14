# Contributing

Framewire is a small headless bridge. Keep integrations at the source, tracker or output boundary and preserve the `framewire.event.v1` contract. Prefer a tested recovery path over a happy-path-only feature.

```sh
python -m pip install -e '.[dev]'
pytest -q
ruff check src tests examples
ruff format --check src tests examples
```

For camera adapters, include a test that simulates a failed read and a reconnect. For GPIO changes, verify inactive startup, release on target loss and source failure, and cleanup on shutdown. CI has no physical camera or Pi; mention the hardware you tested in a pull request.

Please open an issue describing the camera type, OS, Python/OpenCV versions and `framewire doctor --json` output for reproducible bugs. Remove private URLs and credentials before posting diagnostics.
