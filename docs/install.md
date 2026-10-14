# Install once. Add capabilities when you need them.

## macOS and Linux

```sh
curl -fsSL https://raw.githubusercontent.com/IamAngusU/framewire/main/install.sh | sh
```

Open a new terminal after installation. The installer prints a full executable path you can use immediately in the current terminal.

## Windows PowerShell

```powershell
irm https://raw.githubusercontent.com/IamAngusU/framewire/main/install.ps1 | iex
```

`framewire` is available in the current terminal after installation. A corporate execution or download policy may require an administrator to allow the installer.

## What is installed

The scripts download Astral's standalone **uv**, a managed **Python 3.12**, the released Framewire wheel and its runtime dependencies. The wheel is checked against the release's SHA-256 manifest. No Git checkout, compiler, account, virtualenv activation or sudo is needed for the normal webcam workflow. Internet is required for installation and the first model download. The installer uses prebuilt packages; unsupported platforms fail with a clear package error rather than starting a source build.

Runtime files live under `FRAMEWIRE_HOME/runtime`. The default home is `%APPDATA%/Framewire` on Windows, `~/Library/Application Support/Framewire` on macOS, and `$XDG_CONFIG_HOME/framewire` or `~/.config/framewire` on Linux. The command is placed in `%LOCALAPPDATA%/Framewire/bin` on Windows and `~/.local/bin` on macOS/Linux. `FRAMEWIRE_HOME` and `FRAMEWIRE_BIN_DIR` can override these paths.

The Unix installer uses `uv tool update-shell` to add the command folder to shell startup configuration. The Windows installer adds its command folder to the user's PATH. It does not require machine-wide PATH changes. [uv installer documentation](https://docs.astral.sh/uv/reference/installer/) · [Managed Python](https://docs.astral.sh/uv/guides/install-python/).

## Add recognition support

```sh
framewire setup hands actions
framewire setup voice
framewire setup lips
```

Setup installs packages inside Framewire's environment, copies example adapters and fetches models. Existing adapter code and personal samples are kept. `framewire adapters add NAME` is an alias for preparing one example. `--no-install` skips package installation; `--no-download` defers model fetching. Both flags together provide the original copy-only workflow.

Recognition can run offline after the relevant model is cached. Model-download failures can be retried with the same setup command. Optional packages and hardware access depend on the OS: a Pi CSI camera needs libcamera/Picamera2 from Raspberry Pi OS, and Linux microphone capture may need `libportaudio2`. See the [Pi instructions](../README.md#raspberry-pi-setup) and [sounddevice installation](https://python-sounddevice.readthedocs.io/en/latest/installation.html).

## Update or repair

Run the installer again. Recognition examples, including disabled ones, retain their optional dependencies. Requirements declared by your own adapters and prepared with `framewire setup NAME` are retained as well. User adapters and samples are stored outside the installed package, so reinstalling Framewire keeps them. Run `framewire doctor` afterward. A repaired runtime does not repair invalid custom Python code or damaged personal data; those receive their own diagnostic.

## Develop from a checkout

Python 3.10+ is supported for source installs:

```sh
git clone https://github.com/IamAngusU/framewire.git
cd framewire
python -m venv .venv
# Linux/macOS: . .venv/bin/activate
# Windows:    .\.venv\Scripts\Activate.ps1
python -m pip install -e '.[dev]'
```

For installer testing, `FRAMEWIRE_PACKAGE` can point to a locally built wheel, `FRAMEWIRE_UV` to a local uv executable, and `FRAMEWIRE_NO_PATH=1` disables PATH changes. The CI workflow uses isolated temporary folders for these checks.
