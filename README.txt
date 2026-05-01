On previous system:
$ rm ~/package_snapshots/prev_install.txt
$ python3 pkg_tracker.py --create prev_system
Saved successfully: 365 packages recorded (/home/user/package_snapshots/prev_system.txt).
$ /bin/bash migrate_home.sh /home/user

On next system:
$ sudo /bin/bash restore_home.sh backup_taxy_20260419_100000.tar.gz
$ sudo /bin/bash install_pkg_tracker.sh
$ mv ~/package_snapshots/base_install.txt ~/package_snapshots/prev_install.txt
$ pkg-tracker --create base_install

$ pkg-tracker --base prev_install prev_system
or
$ pkg-tracker prev_system


Install from git:
$ gh repo clone taxy/debian_based_home_migrate
$ cd debian_based_home_migrate
$ python3 -m pipx install .
or for development:
$ python3 -m pipx install -e .

After installation the environment exposes this command:
$ pkg-tracker --help

