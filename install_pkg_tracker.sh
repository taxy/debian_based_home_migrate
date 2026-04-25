#!/bin/bash

# 1. Check root privileges
# Installing to system paths requires admin rights.
if [ "$EUID" -ne 0 ]; then
    echo "Error: This script must be run with root privileges (sudo)!"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"

cp "$SCRIPT_DIR/pkg_tracker.py" /usr/local/bin/pkg_tracker
chmod +x /usr/local/bin/pkg_tracker

