#!/bin/sh
# Framewire: no existing Python, Git, sudo or virtualenv activation required.
set -eu

version=0.3.0
release="https://github.com/IamAngusU/framewire/releases/download/v$version"
wheel_name="framewire-$version-py3-none-any.whl"

case "$(uname -s)" in
  Darwin) default_home="$HOME/Library/Application Support/Framewire" ;;
  Linux) default_home="${XDG_CONFIG_HOME:-$HOME/.config}/framewire" ;;
  *) printf '%s\n' 'Use install.ps1 on Windows. This installer supports macOS and Linux.' >&2; exit 1 ;;
esac
fw_home="${FRAMEWIRE_HOME:-$default_home}"
bin_dir="${FRAMEWIRE_BIN_DIR:-$HOME/.local/bin}"
runtime="$fw_home/runtime"
uv="${FRAMEWIRE_UV:-$runtime/uv/uv}"
scratch="$(mktemp -d "${TMPDIR:-/tmp}/framewire.XXXXXXXX")"
cleanup() {
  result=$?
  rm -f "$scratch/uv-install.sh" "$scratch/$wheel_name" "$scratch/SHA256SUMS"
  rmdir "$scratch" 2>/dev/null || true
  if [ "$result" -ne 0 ]; then
    printf '\n%s\n' 'Framewire installation did not finish. Check the message above and run the same command again.' >&2
  fi
}
trap cleanup EXIT

fetch() {
  if command -v curl >/dev/null 2>&1; then
    curl --fail --location --silent --show-error --retry 2 --connect-timeout 10 --max-time 180 "$1" --output "$2"
  elif command -v wget >/dev/null 2>&1; then
    wget --quiet --timeout=30 --tries=3 "$1" -O "$2"
  else
    printf '%s\n' 'Install curl or wget, then run this installer again.' >&2
    return 1
  fi
}

printf '\n%s\n\n' 'Framewire · preparing your local environment'
mkdir -p "$runtime" "$bin_dir"
if [ ! -x "$uv" ]; then
  fetch https://astral.sh/uv/install.sh "$scratch/uv-install.sh"
  UV_UNMANAGED_INSTALL="$runtime/uv" sh "$scratch/uv-install.sh"
fi

package="${FRAMEWIRE_PACKAGE:-$scratch/$wheel_name}"
if [ -z "${FRAMEWIRE_PACKAGE:-}" ]; then
  fetch "$release/$wheel_name" "$package"
  fetch "$release/SHA256SUMS" "$scratch/SHA256SUMS"
  expected="$(awk -v name="$wheel_name" '$2 == name {print $1}' "$scratch/SHA256SUMS")"
  if command -v sha256sum >/dev/null 2>&1; then
    actual="$(sha256sum "$package" | awk '{print $1}')"
  else
    actual="$(shasum -a 256 "$package" | awk '{print $1}')"
  fi
  if [ -z "$expected" ] || [ "$actual" != "$expected" ]; then
    printf '%s\n' 'The package checksum did not match. Installation stopped; retry the download.' >&2
    exit 1
  fi
fi

export UV_TOOL_DIR="$runtime/tools"
export UV_TOOL_BIN_DIR="$bin_dir"
export UV_PYTHON_INSTALL_DIR="$runtime/python"
export UV_CACHE_DIR="$runtime/cache"
set -- tool install --python 3.12 --managed-python --no-build --reinstall "$package"
if [ -f "$fw_home/data/runtime-requirements.txt" ]; then
  set -- "$@" --with-requirements "$fw_home/data/runtime-requirements.txt"
fi
if [ -f "$fw_home/adapters/hands.py" ] || [ -f "$fw_home/adapters/lips.py" ] || [ -f "$fw_home/adapters/hands.py.disabled" ] || [ -f "$fw_home/adapters/lips.py.disabled" ]; then
  set -- "$@" --with 'mediapipe>=1.0,<2'
fi
if [ -f "$fw_home/adapters/voice.py" ] || [ -f "$fw_home/adapters/voice.py.disabled" ]; then
  set -- "$@" --with 'faster-whisper>=1.2,<2' --with 'sounddevice>=0.5,<1'
fi
"$uv" "$@"
if [ "${FRAMEWIRE_NO_PATH:-0}" != 1 ]; then
  "$uv" tool update-shell || printf '%s\n' "Add $bin_dir to PATH to use framewire by name."
fi
"$bin_dir/framewire"
printf '\n%s\n' 'Installed. Open a new terminal, then run: framewire demo'
printf '%s\n' "For this terminal: \"$bin_dir/framewire\" demo"
