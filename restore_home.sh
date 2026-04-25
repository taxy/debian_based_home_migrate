#!/bin/bash

# 1. Check root privileges
# Writing to /home and modifying ownership requires admin rights.
if [ "$EUID" -ne 0 ]; then
    echo "Error: This script must be run with root privileges (sudo)!"
    exit 1
fi

# 2. Validate parameters
if [ "$#" -ne 1 ]; then
    echo "Usage: sudo $0 <backup_file.tar.gz>"
    echo "Example: sudo $0 backup_kollarlaszlo_20260419_100000.tar.gz"
    exit 1
fi

BACKUP_FILE="$1"

# 3. Check if file exists
if [ ! -f "$BACKUP_FILE" ]; then
    echo "Error: File '$BACKUP_FILE' was not found."
    exit 1
fi

echo "=== Home Directory Restorer ==="
echo "File: $BACKUP_FILE"

# 4. Determine directory (and user) name from archive
echo "Analyzing archive contents..."
# Extract the first entry from the archive (e.g., "kollarlaszlo/")
TARGET_USER=$(tar -tzf "$BACKUP_FILE" | head -1 | cut -f1 -d"/")

if [ -z "$TARGET_USER" ]; then
    echo "Error: Could not identify user name from archive."
    exit 1
fi

TARGET_DIR="/home/$TARGET_USER"
echo "Target directory: $TARGET_DIR"

# 5. Safety confirmation if directory already exists on target machine
if [ -d "$TARGET_DIR" ]; then
    echo "---------------------------------------------------"
    echo "WARNING: Directory $TARGET_DIR already exists on the target machine!"
    read -p "Are you sure you want to merge/overwrite its contents? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Restore aborted."
        exit 1
    fi
fi

echo "---------------------------------------------------"
# 6. Extract into /home
echo "Extraction in progress..."
# Redirect standard output to /dev/null to avoid flooding the terminal with file names,
# but keep error output (stderr) visible if something goes wrong.
tar -xzf "$BACKUP_FILE" -C /home/

# 7. Fix ownership (critical step)
# Check whether this user already exists on the new machine
if id "$TARGET_USER" &>/dev/null; then
    echo "Restoring ownership ($TARGET_USER:$TARGET_USER)..."
    chown -R "$TARGET_USER:$TARGET_USER" "$TARGET_DIR"
    echo "---------------------------------------------------"
    echo "Restore completed successfully! Files are in place."
else
    echo "---------------------------------------------------"
    echo "Extraction succeeded, BUT:"
    echo "User '$TARGET_USER' does not exist yet on this new system!"
    echo "1. Create the user in the GUI, or from command line:"
    echo "   sudo adduser $TARGET_USER"
    echo "2. Then run this command so file ownership is assigned correctly:"
    echo "   sudo chown -R $TARGET_USER:$TARGET_USER $TARGET_DIR"
fi

