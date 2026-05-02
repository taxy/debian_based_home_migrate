# 🚀 Debian-Based Home Migrate & Package Tracker

**Migration shouldn't feel like digital archaeology.** 

Most tools just dump a list of 2,000+ packages, leaving you to sift through the noise of automatic dependencies and library bloat. This project takes a different approach: **it finds the "Peaks."**

### 🧠 The "Peak Package" Philosophy
Mathematically speaking, this tool identifies the **Minimum Spanning Set** of your system's software. Instead of a flat list, it calculates the smallest possible collection of manually installed packages required to reconstruct your entire dependency tree.

*   **Leaf-to-Root Traversal:** To handle the "Recommends" field and those pesky circular references, the tracker crawls the dependency graph starting from the leaves. This ensures that even complex, self-referencing loops are captured without missing a beat.
*   **Alternative Dependencies:** The tool acknowledges that modern package management isn't always linear; it highlights potential alternatives, giving you full control over your system's reconstruction.
*   **Hermetic Rescue:** Designed to work even when your OS has "given up." If your local Python is ancient or broken, the included setup script spins up a **standalone, portable Python 3.12 environment**.

### 🛠️ Key Features
*   **Smart Snapshots:** Capture "Peak Packages" on the old system, baseline your new system, and surgically install only the differences.
*   **ExFAT-Friendly Execution:** Since non-Linux filesystems don't support execution bits or symlinks, the tool automatically unpacks its runtime into `/tmp` (RAM-disk) to bypass mount restrictions.

### 📋 Quick Workflow Overview
1.  **Old System:** Load the portable Python and capture a snapshot of your peak packages.
2.  **Home Migration:** Archive and move your actual home directory data using the provided scripts.
3.  **New System:** Restore your home directory, create a fresh baseline, and sync your missing "Peaks" via `apt-get`.
---
---

<img src="doc/demo.svg" width="600" alt="Debian Home Migrate Demo">

## On Previous System

1. Remove any old snapshot with the same name to avoid stale comparisons.

```bash
rm ~/package_snapshots/prev_install.txt
```

2. If the old system Python is older than 3.10, load the portable Python environment.

```bash
source ./setup_python.sh
```

3. Capture a snapshot of the current manually installed peak packages.

```bash
python3 pkg_tracker.py --create prev_system
# Example output:
# Saved successfully: 365 packages recorded (/home/user/package_snapshots/prev_system.txt).
```

4. Archive and migrate your home directory data.

```bash
/bin/bash migrate_home.sh /home/user
```

## On Next System

1. Restore the previously created backup archive.

```bash
sudo /bin/bash restore_home.sh backup_taxy_20260419_100000.tar.gz
```

2. Install pkg-tracker on the new machine.

```bash
/bin/bash install_pkg_tracker.sh
```

3. Keep the old base snapshot as `prev_install` for base filtering.

```bash
mv ~/package_snapshots/base_install.txt ~/package_snapshots/prev_install.txt
```

4. Create a fresh baseline snapshot on the new system.

```bash
pkg-tracker --create base_install
```

5. Compare the previous snapshot against current state while ignoring base packages.

```bash
pkg-tracker --base prev_install prev_system
```

Or do a quick compare against a named snapshot:

```bash
pkg-tracker prev_system
```

6. Install missing packages that were present in `prev_system`.

```bash
sudo apt-get install $(pkg-tracker --base prev_install prev_system --pipe-output-set removed)
```

7. Restore the fresh-install package state.

```bash
sudo apt-get autoremove $(pkg-tracker base_install --pipe-output-set new)
```

## Install From Git

Clone the repository and enter it:

```bash
gh repo clone taxy/debian_based_home_migrate
cd debian_based_home_migrate
```

Install as a normal end-user tool:

```bash
python3 -m pipx install .
```

Or for development (editable install):

```bash
python3 -m pipx install -e .
```

## Enable Bash Tab Completion

Enable shell tab-completion for pkg-tracker in bash:

```bash
echo 'eval "$(register-python-argcomplete pkg-tracker)"' >> ~/.bashrc
```

Reload shell config in the current terminal:

```bash
source ~/.bashrc
```

## Verify Installation

After installation, the environment exposes this command:

```bash
pkg-tracker --help
```
