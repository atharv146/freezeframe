#!/bin/sh
# Freezeframe installer for macOS and Linux:
#
#   curl -fsSL https://raw.githubusercontent.com/atharv146/freezeframe/main/install.sh | sh
#
# Installs Freezeframe into its own Python environment, puts the `freezeframe`
# command in ~/.local/bin, and starts recording in the background.
# No administrator rights needed. Undo with:  freezeframe uninstall
#
# Options (environment variables):
#   FREEZEFRAME_SOURCE=...        install from this pip source instead of PyPI/GitHub
#   FREEZEFRAME_NO_AUTOSTART=1    install only; don't start background recording
set -eu

GITHUB_ZIP="https://github.com/atharv146/freezeframe/archive/refs/heads/main.zip"
BIN_DIR="$HOME/.local/bin"
case "$(uname -s)" in
  Darwin) APP_DIR="$HOME/Library/Application Support/Freezeframe/app" ;;
  *) APP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/freezeframe/app" ;;
esac

say() { printf '%s\n' "$*"; }
fail() { printf 'Freezeframe install failed: %s\n' "$*" >&2; exit 1; }

PY=""
for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
    PY="$candidate"
    break
  fi
done
if [ -z "$PY" ]; then
  case "$(uname -s)" in
    Darwin) fail "Python 3.10 or newer is needed. Install it from https://www.python.org/downloads/ (or: brew install python), then run this again." ;;
    *) fail "Python 3.10 or newer is needed. Install it with your package manager (for example: sudo apt install python3 python3-venv), then run this again." ;;
  esac
fi

say "Installing Freezeframe with $($PY --version 2>&1)..."
mkdir -p "$(dirname "$APP_DIR")"
"$PY" -m venv "$APP_DIR" 2>/dev/null || fail "couldn't create a Python environment. On Debian or Ubuntu, run: sudo apt install python3-venv"
VPY="$APP_DIR/bin/python"
"$VPY" -m pip install --quiet --disable-pip-version-check --upgrade pip
if [ -n "${FREEZEFRAME_SOURCE:-}" ]; then
  "$VPY" -m pip install --quiet --disable-pip-version-check --upgrade "$FREEZEFRAME_SOURCE"
elif ! "$VPY" -m pip install --quiet --disable-pip-version-check --upgrade freezeframe 2>/dev/null; then
  "$VPY" -m pip install --quiet --disable-pip-version-check --upgrade "$GITHUB_ZIP"
fi

mkdir -p "$BIN_DIR"
ln -sf "$APP_DIR/bin/freezeframe" "$BIN_DIR/freezeframe"

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *)
    case "${SHELL:-}" in
      */zsh) PROFILE="$HOME/.zprofile" ;;
      */bash) if [ "$(uname -s)" = Darwin ]; then PROFILE="$HOME/.bash_profile"; else PROFILE="$HOME/.bashrc"; fi ;;
      *) PROFILE="$HOME/.profile" ;;
    esac
    if ! grep -qs '\.local/bin' "$PROFILE"; then
      printf '\n# Added by the Freezeframe installer\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$PROFILE"
      say "Added ~/.local/bin to your PATH in $PROFILE (open a new terminal for it to take effect)."
    fi
    ;;
esac

if [ "${FREEZEFRAME_NO_AUTOSTART:-}" != "1" ]; then
  "$BIN_DIR/freezeframe" install
fi

say ""
say "Freezeframe $("$BIN_DIR/freezeframe" --version | cut -d' ' -f2) is installed."
say "  freezeframe why     explain the latest freeze"
say "  freezeframe open    open the timeline dashboard"
say "  freezeframe open --demo    explore a demo recording right now"
