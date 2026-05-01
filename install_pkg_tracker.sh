#!/bin/bash
set -e

# Install the project into the user's pipx environment and expose the
# pkg-tracker console script from the package metadata.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"

if command -v pipx >/dev/null 2>&1; then
    pipx install --force "$SCRIPT_DIR"
    pipx ensurepath >/dev/null 2>&1 || true
    exit 0
fi

echo "pipx is not installed. Installing it requires sudo."

if command -v sudo >/dev/null 2>&1; then
    sudo apt-get install -y pipx
elif [ "$EUID" -eq 0 ]; then
    apt-get install -y pipx
else
    echo "Error: sudo is not available, and pipx is not installed."
    echo "Install pipx manually, then rerun this script."
    exit 1
fi

pipx install --force "$SCRIPT_DIR"
pipx ensurepath >/dev/null 2>&1 || true

