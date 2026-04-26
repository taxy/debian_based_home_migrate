#!/usr/bin/env python3
import argparse
import pathlib
import subprocess
import sys
import re

SNAPSHOT_DIR = pathlib.Path.home() / "package_snapshots"

# ANSI color codes for terminal formatting
COLOR_GREEN = '\033[92m'
COLOR_RESET = '\033[0m'

def get_manual_packages():
    """Get manually installed packages using apt-mark."""
    try:
        result = subprocess.run(['apt-mark', 'showmanual'], capture_output=True, text=True, check=True)
        return set(filter(None, result.stdout.split('\n')))
    except subprocess.CalledProcessError as e:
        print(f"Error while querying apt: {e}", file=sys.stderr)
        sys.exit(1)

def get_dependencies_data():
    """
    Parse the dpkg status file and collect two separate sets:
    1. Strict dependencies (Depends, Pre-Depends)
    2. Recommended packages (Recommends)
    """
    strict_deps = set()
    recommends_deps = set()

    try:
        with open('/var/lib/dpkg/status', 'r', encoding='utf-8') as f:
            for line in f:
                if line.startswith(('Depends:', 'Pre-Depends:')):
                    deps_str = line.split(':', 1)[1]
                    for dep in re.split(r'[,|]', deps_str):
                        parts = dep.strip().split()
                        if parts:
                            strict_deps.add(parts[0])

                elif line.startswith('Recommends:'):
                    deps_str = line.split(':', 1)[1]
                    for dep in re.split(r'[,|]', deps_str):
                        parts = dep.strip().split()
                        if parts:
                            recommends_deps.add(parts[0])

    except FileNotFoundError:
        print("Error: Could not find /var/lib/dpkg/status.", file=sys.stderr)

    return strict_deps, recommends_deps

def format_pkg_output(pkg_name, recommended_set):
    """Color the package name green if it is in the recommended set."""
    if pkg_name in recommended_set:
        return f"{COLOR_GREEN}{pkg_name}{COLOR_RESET}"
    return pkg_name

def load_snapshot(snapshot_name):
    """Load a snapshot file by name and return it as a set of package names."""
    file_path = SNAPSHOT_DIR / f"{snapshot_name}.txt"
    if not file_path.exists():
        print(f"Error: No snapshot named '{snapshot_name}'. Looked in: '{SNAPSHOT_DIR}'", file=sys.stderr)
        return None
    with open(file_path, 'r') as f:
        return set(f.read().splitlines())

def print_changes_report(header, new_pkgs, rem_pkgs, clean_recommends, removed_label):
    """Print a formatted package diff report used by both diff modes."""
    print(header)
    print(f"Legend: {COLOR_GREEN}green{COLOR_RESET} packages were likely installed as recommendations.\n")

    if new_pkgs:
        print(f"New peak packages ({len(new_pkgs)}):")
        for p in new_pkgs:
            print(f"  + {format_pkg_output(p, clean_recommends)}")
    if rem_pkgs:
        print(f"\n{removed_label} ({len(rem_pkgs)}):")
        for p in rem_pkgs:
            print(f"  - {p}")

    if not new_pkgs and not rem_pkgs:
        print("No changes.")

def main():
    parser = argparse.ArgumentParser(description="Linux package tracker and Peak analyzer tool.")

    parser.add_argument("--create", metavar="NAME", help="Create a new snapshot with the given name.")
    parser.add_argument("--diff", metavar="NAME", help="Compare against a previous snapshot.")
    parser.add_argument("--base", nargs=2, metavar=("BASE_NAME", "TARGET_NAME"), help="Noise-filtered comparison: subtract base system packages.")
    parser.add_argument("name", nargs="?", help="Snapshot name for plain diff mode.")

    args = parser.parse_args()

    # 1. Collect data
    manual_packages = get_manual_packages()
    strict_deps, recommends_deps = get_dependencies_data()

    # 2. Set operations
    current_peak_packages = manual_packages - strict_deps
    clean_recommends = recommends_deps - strict_deps

    # --- LISTING (default mode) ---
    if not args.create and not args.diff and not args.base and not args.name:
        print(f"--- Installed Peak packages ({len(current_peak_packages)} total) ---")
        print(f"Legend: {COLOR_GREEN}green{COLOR_RESET} packages were likely installed as recommendations.\n")
        for pkg in sorted(current_peak_packages):
            print(f"  * {format_pkg_output(pkg, clean_recommends)}")
        return

    # --- SAVE SNAPSHOT ---
    if args.create:
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        file_path = SNAPSHOT_DIR / f"{args.create}.txt"
        with open(file_path, 'w') as f:
            for pkg in sorted(current_peak_packages):
                f.write(f"{pkg}\n")
        print(f"Saved successfully: {len(current_peak_packages)} packages recorded ({file_path}).")

    # --- NOISE-FILTERED COMPARISON (base mode) ---
    elif args.base:
        base_name, target_name = args.base[0], args.base[1]
        base_set = load_snapshot(base_name)
        if base_set is None:
            return
        target_set = load_snapshot(target_name)
        if target_set is None:
            return

        # Union of current packages and previous base-system packages
        combined_current = current_peak_packages | base_set

        new_pkgs = sorted(combined_current - target_set)
        rem_pkgs = sorted(target_set - combined_current)
        print_changes_report(
            f"--- Changes since [{target_name}] (base noise filter: [{base_name}]) ---",
            new_pkgs,
            rem_pkgs,
            clean_recommends,
            "Missing (removed) peak packages",
        )

    # --- PLAIN COMPARISON (diff mode) ---
    elif args.diff or args.name:
        target_name = args.diff if args.diff else args.name
        target_set = load_snapshot(target_name)
        if target_set is None:
            return

        new_pkgs = sorted(current_peak_packages - target_set)
        rem_pkgs = sorted(target_set - current_peak_packages)
        print_changes_report(
            f"--- Changes since [{target_name}] ---",
            new_pkgs,
            rem_pkgs,
            clean_recommends,
            "Removed peak packages",
        )

if __name__ == "__main__":
    main()
