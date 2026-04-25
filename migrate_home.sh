#!/bin/bash

# 1. Validate parameters
if [ "$#" -ne 1 ]; then
    echo "Usage: $0 <home_directory_path>"
    echo "Example: $0 /home/kollarlaszlo"
    exit 1
fi

SOURCE_DIR="$1"

# 2. Check if the provided directory exists
if [ ! -d "$SOURCE_DIR" ]; then
    echo "Error: Directory '$SOURCE_DIR' does not exist."
    exit 1
fi

# 3. Set variables
# This command gets the path of the folder where this script runs (USB root)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
USER_NAME="$(basename "$SOURCE_DIR")"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_FILE="$SCRIPT_DIR/backup_${USER_NAME}_${TIMESTAMP}.tar.gz"

# 4. List of directories/files to exclude (filter out "junk")
EXCLUDES=(
    "--exclude=.cache"                  # Largest space consumer (browser cache, etc.)
    "--exclude=.local/share/Trash"      # Trash contents
    "--exclude=.thumbnails"             # Image thumbnails
    "--exclude=.gvfs"                   # Virtual filesystem (often causes tar read errors)
    "--exclude=.npm"                    # Node.js package cache
    "--exclude=.nv"                     # Nvidia shader cache
    "--exclude=.xsession-errors*"       # Desktop environment error logs
    "--exclude=.var/app/*/cache"        # Flatpak app cache
    "--exclude=snap/*/*/.cache"         # Snap app cache
    
    # Optional: If you do not want to migrate Downloads, uncomment the line below:
    # "--exclude=Downloads"
)

echo "=== Home Directory Migrator ==="
echo "Source: $SOURCE_DIR"
echo "Target: $BACKUP_FILE"
echo "Excluded items: cache, trash, and unnecessary temp files."
echo "Compression in progress, this may take a while..."
echo "---------------------------------------------------"

# 5. Run the tar command
# With -C we switch to the parent of the home directory (usually /home),
# so extraction creates a clean "kollarlaszlo" folder, not a full /home/... path.
PARENT_DIR="$(dirname "$SOURCE_DIR")"
DIR_TO_BACKUP="$(basename "$SOURCE_DIR")"

tar -czvf "$BACKUP_FILE" "${EXCLUDES[@]}" -C "$PARENT_DIR" "$DIR_TO_BACKUP"

# 6. Check result
if [ $? -eq 0 ]; then
    echo "---------------------------------------------------"
    echo "Backup completed successfully!"
    echo "File: $BACKUP_FILE"
    # Print final file size
    ls -lh "$BACKUP_FILE" | awk '{print "Size: " $5}'
else
    echo "---------------------------------------------------"
    echo "An error occurred during compression. Check the error messages above."
    exit 1
fi

