On previous system:
# Remove any old snapshot with the same name to avoid stale comparisons.
$ rm ~/package_snapshots/prev_install.txt

# If old system python < 3.10:
$ source  ./setup_python.sh 

# Capture a snapshot of the current manually installed peak packages.
$ python3 pkg_tracker.py --create prev_system
Saved successfully: 365 packages recorded (/home/user/package_snapshots/prev_system.txt).
# Archive and migrate your home directory data.
$ /bin/bash migrate_home.sh /home/user

On next system:
# Restore the previously created backup archive.
$ sudo /bin/bash restore_home.sh backup_taxy_20260419_100000.tar.gz
# Install pkg-tracker and helper scripts on the new machine.
$ sudo /bin/bash install_pkg_tracker.sh
# Keep the old base snapshot as prev_install for base filtering.
$ mv ~/package_snapshots/base_install.txt ~/package_snapshots/prev_install.txt
# Create a fresh baseline snapshot on the new system.
$ pkg-tracker --create base_install

# Compare previous snapshot against current state while ignoring base packages.
$ pkg-tracker --base prev_install prev_system
or
# Quick compare against a named snapshot.
$ pkg-tracker prev_system


Install from git:
# Clone the repository and enter it.
$ gh repo clone taxy/debian_based_home_migrate
$ cd debian_based_home_migrate
# Install as a normal end-user tool.
$ python3 -m pipx install .
or for development:
# Editable install for local development.
$ python3 -m pipx install -e .


# Enable shell tab-completion for pkg-tracker in bash:
$ echo 'eval "$(register-python-argcomplete pkg-tracker)"' >> ~/.bashrc
# Reload your shell config in the current terminal.
$ source ~/.bashrc

After installation the environment exposes this command:
$ pkg-tracker --help

