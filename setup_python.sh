#!/bin/bash

# 1. Find the directory where this script is located
# This works even if the USB drive is mounted elsewhere
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMP_EXEC_DIR="/tmp/py_exec_$(whoami)"
PYTHON_DIR="$TEMP_EXEC_DIR/python_portable"
GITHUB_FOLDER="https://github.com/astral-sh/python-build-standalone/releases/download/20260414"
GITHUB_FILENAME="cpython-3.12.13+20260414-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz"
SHA_SUMS_FILE="SHA256SUMS"
SHA_SUMS_URL="$GITHUB_FOLDER/$SHA_SUMS_FILE"
URL="$GITHUB_FOLDER/$GITHUB_FILENAME"
ARCHIVE_NAME="cpython_portable.tar.gz"
PYTHON_ARCHIVE_DIR="$SCRIPT_DIR/python_portable"

download_with_fallback() {
    local src_url="$1"
    local dst_path="$2"
    local download_ok=0

    if command -v curl >/dev/null 2>&1; then
        if curl -fL "$src_url" -o "$dst_path"; then
            download_ok=1
        else
            echo "curl failed for $src_url. Retrying curl with --insecure..."
            if curl -kfL "$src_url" -o "$dst_path"; then
                download_ok=1
            fi
        fi
    fi

    if [ "$download_ok" -ne 1 ] && command -v wget >/dev/null 2>&1; then
        echo "Trying wget for $src_url..."
        if wget -O "$dst_path" "$src_url"; then
            download_ok=1
        else
            echo "wget failed for $src_url. Retrying wget with --no-check-certificate..."
            if wget --no-check-certificate -O "$dst_path" "$src_url"; then
                download_ok=1
            fi
        fi
    fi

    if [ "$download_ok" -ne 1 ]; then
        if ! command -v curl >/dev/null 2>&1 && ! command -v wget >/dev/null 2>&1; then
            echo "Error: Neither curl nor wget is available on this system!"
        else
            echo "Error: Download failed for $src_url with both curl and wget (including insecure fallback options)."
        fi
        return 1
    fi

    return 0
}

# Check system architecture
SYSTEM_ARCH=$(uname -m)
EXPECTED_ARCH="x86_64"

if [ "$SYSTEM_ARCH" != "$EXPECTED_ARCH" ]; then
    echo "Error: System architecture mismatch!"
    echo "Expected: $EXPECTED_ARCH"
    echo "Detected: $SYSTEM_ARCH"
    echo "This Python binary is only compatible with x86_64-unknown-linux-gnu systems."
    return 1 2>/dev/null || exit 1
fi

# 2. Check whether the extracted directory already exists
if [ ! -d "$PYTHON_DIR" ]; then
    echo "--- Portable Python not found. Starting installation... ---"
    
    # Ensure archive directory exists
    mkdir -p "$PYTHON_ARCHIVE_DIR"
    
    # Check if archive file already exists
    if [ ! -f "$PYTHON_ARCHIVE_DIR/$ARCHIVE_NAME" ]; then
        # Download to the USB drive
        echo "Downloading: $URL"
        if ! download_with_fallback "$URL" "$PYTHON_ARCHIVE_DIR/$ARCHIVE_NAME"; then
            return 1 2>/dev/null || exit 1
        fi
    else
        echo "--- Archive already exists. Skipping download... ---"
    fi

    if [ ! -f "$PYTHON_ARCHIVE_DIR/$SHA_SUMS_FILE" ]; then
        echo "Downloading: $SHA_SUMS_URL"
        if ! download_with_fallback "$SHA_SUMS_URL" "$PYTHON_ARCHIVE_DIR/$SHA_SUMS_FILE"; then
            return 1 2>/dev/null || exit 1
        fi
    else
        echo "--- Checksum file already exists. Skipping checksum download... ---"
    fi

    echo "Verifying file integrity..."
    EXPECTED_SHA=$(grep "$GITHUB_FILENAME" "$PYTHON_ARCHIVE_DIR/$SHA_SUMS_FILE" | awk '{print $1}')
    
    if [ -z "$EXPECTED_SHA" ]; then
        echo "Error: Hash not found for this file in SHA256SUMS!"
        return 1 2>/dev/null || exit 1
    fi

    # 2. Calculate the actual hash of the downloaded file
    ACTUAL_SHA=$(sha256sum "$PYTHON_ARCHIVE_DIR/$ARCHIVE_NAME" | awk '{print $1}')

    # 3. Comparison
    if [ "$EXPECTED_SHA" == "$ACTUAL_SHA" ]; then
        echo "Verification successful: SHA256 checksums match."
    else
        echo "Critical error: SHA256 checksum mismatch!"
        echo "Expected: $EXPECTED_SHA"
        echo "Got: $ACTUAL_SHA"
        echo "The file is likely corrupted. Deleting..."
        rm "$PYTHON_ARCHIVE_DIR/$ARCHIVE_NAME" "$PYTHON_ARCHIVE_DIR/$SHA_SUMS_FILE"
        return 1 2>/dev/null || exit 1
    fi

    # Extract into a dedicated folder
    echo "Extracting..."
    mkdir -p "$PYTHON_DIR"
    tar -xzf "$PYTHON_ARCHIVE_DIR/$ARCHIVE_NAME" --strip-components=1 -C "$PYTHON_DIR"
    
    echo "--- Installation complete. ---"
else
    echo "--- Portable Python detected. ---"
fi

export PATH="$PYTHON_DIR/bin:$PATH"

# Verification
echo "Current Python path: $(which python3)"
python3 --version
