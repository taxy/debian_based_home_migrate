#!/usr/bin/env python3
import argparse
import pathlib
import subprocess
import sys
import re
from collections import deque
from typing import Set, Dict, Tuple, List

SNAPSHOT_DIR = pathlib.Path.home() / "package_snapshots"

# ANSI color codes for terminal formatting
COLOR_GREEN = '\033[92m'
COLOR_RESET = '\033[0m'

def get_manual_packages() -> Set[str]:
    """Get manually installed packages using apt-mark."""
    try:
        result = subprocess.run(['apt-mark', 'showmanual'], capture_output=True, text=True, check=True)
        return set(filter(None, result.stdout.split('\n')))
    except subprocess.CalledProcessError as e:
        print(f"Error while querying apt: {e}", file=sys.stderr)
        sys.exit(1)

def get_dependencies_data() -> Tuple[Set[str], Dict[str, Set[str]], Set[str]]:
    """
    Parse the dpkg status file and collect:
    1. Strict dependencies (Depends, Pre-Depends)
    2. Recommended packages map: recommended package -> package that recommends it
    3. Recommender packages: package names that have a Recommends field
    """
    strict_deps: Set[str] = set()
    recommends_deps: Dict[str, Set[str]] = {}
    recommender_packages: Set[str] = set()
    current_package: str | None = None

    try:
        with open('/var/lib/dpkg/status', 'r', encoding='utf-8') as f:
            for line in f:
                if line.startswith('Package:'):
                    current_package = line.split(':', 1)[1].strip()

                elif line.startswith(('Depends:', 'Pre-Depends:')):
                    deps_str = line.split(':', 1)[1]
                    for dep in re.split(r'[,|]', deps_str):
                        parts = dep.strip().split()
                        if parts:
                            strict_deps.add(parts[0])

                elif line.startswith('Recommends:'):
                    if current_package:
                        recommender_packages.add(current_package)
                    deps_str = line.split(':', 1)[1]
                    for dep in re.split(r'[,|]', deps_str):
                        parts = dep.strip().split()
                        if parts and current_package:
                            recommends_deps.setdefault(parts[0], set()).add(current_package)

    except FileNotFoundError:
        print("Error: Could not find /var/lib/dpkg/status.", file=sys.stderr)

    return strict_deps, recommends_deps, recommender_packages

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

def leaf_recommended_packages(
    recommends_deps: Dict[str, Set[str]], recommender_packages: Set[str]
) -> Set[str]:
    """Keep only leaf recommended packages (recommended, but not recommenders)."""
    return set(recommends_deps) - recommender_packages

def find_recommend_circles(
    recommends_deps: Dict[str, Set[str]], leaf_pkgs: Set[str]
) -> Tuple[List[List[str]], List[str]]:
    """
    Find connected circles among leaf packages.

    Two leaf packages are connected when they share at least one recommender,
    and circles are connected components in this graph.
    """
    if not leaf_pkgs:
        return [], []

    recommender_to_leafs: Dict[str, Set[str]] = {}
    for leaf in leaf_pkgs:
        for recommender in recommends_deps.get(leaf, set()):
            recommender_to_leafs.setdefault(recommender, set()).add(leaf)

    adjacency: Dict[str, Set[str]] = {pkg: set() for pkg in leaf_pkgs}
    for linked_leafs in recommender_to_leafs.values():
        if len(linked_leafs) < 2:
            continue
        for leaf in linked_leafs:
            adjacency[leaf].update(linked_leafs - {leaf})

    visited: Set[str] = set()
    circles: List[List[str]] = []
    singles: List[str] = []

    for root in sorted(leaf_pkgs):
        if root in visited:
            continue

        queue = deque([root])
        component: List[str] = []
        visited.add(root)

        while queue:
            cur = queue.popleft()
            component.append(cur)
            for nxt in adjacency[cur]:
                if nxt not in visited:
                    visited.add(nxt)
                    queue.append(nxt)

        if len(component) > 1:
            circles.append(sorted(component))
        else:
            singles.extend(component)

    circles.sort(key=len, reverse=True)
    singles.sort()
    return circles, singles

def print_recommend_circles(
    recommends_deps: Dict[str, Set[str]],
    clean_recommenders: Set[str]
) -> None:
    """Compute and display recommendation circles only for leaf packages."""
    leaf_pkgs = leaf_recommended_packages(recommends_deps, clean_recommenders)
    circles, singles = find_recommend_circles(recommends_deps, leaf_pkgs)


    print("--- Recommend circles (leaf recommended packages only) ---")
    print(f"Leaf set size: {len(leaf_pkgs)}")

    if not circles:
        print("No circles found.")
    else:
        print(f"Circles found: {len(circles)}")
        for idx, circle in enumerate(circles, start=1):
            print(f"\nCircle #{idx} ({len(circle)} leaf packages):")
            for pkg in circle:
                recommenders = sorted(recommends_deps.get(pkg, set()))
                preview = ', '.join(recommenders[:4])
                suffix = " ..." if len(recommenders) > 4 else ""
                print(f"  - {pkg}  <-  [{preview}{suffix}]")

    if singles:
        print(f"\nUnlinked leaf packages ({len(singles)}):")
        for pkg in singles[:50]:
            print(f"  - {pkg}")
        if len(singles) > 50:
            print(f"  ... and {len(singles) - 50} more")

def main():
    parser = argparse.ArgumentParser(description="Linux package tracker and Peak analyzer tool.")

    parser.add_argument("--create", metavar="NAME", help="Create a new snapshot with the given name.")
    parser.add_argument("--diff", metavar="NAME", help="Compare against a previous snapshot.")
    parser.add_argument("--base", nargs=2, metavar=("BASE_NAME", "TARGET_NAME"), help="Noise-filtered comparison: subtract base system packages.")
    parser.add_argument(
        "--print-recommend-circles",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Show recommend circles in default listing mode (use --no-print-recommend-circles to hide).",
    )
    parser.add_argument("name", nargs="?", help="Snapshot name for plain diff mode.")

    args = parser.parse_args()

    # 1. Collect data
    manual_packages = get_manual_packages()
    strict_deps, recommends_deps, recommender_packages = get_dependencies_data()

    # 2. Set operations
    current_peak_packages = manual_packages - strict_deps
    clean_recommenders = recommender_packages - strict_deps
    clean_recommends = {
        pkg: (recommenders - strict_deps)
        for pkg, recommenders in recommends_deps.items()
        if pkg not in strict_deps and (recommenders - strict_deps)
    }
    clean_recommended_targets = set(clean_recommends)

    # --- LISTING (default mode) ---
    if not args.create and not args.diff and not args.base and not args.name:
        if args.print_recommend_circles:
            print_recommend_circles(
                clean_recommends,
                clean_recommenders,
            )
            print()
        print(f"--- Installed Peak packages ({len(current_peak_packages)} total) ---")
        print(f"Legend: {COLOR_GREEN}green{COLOR_RESET} packages were likely installed as recommendations.\n")
        for pkg in sorted(current_peak_packages):
            print(f"  * {format_pkg_output(pkg, clean_recommended_targets)}")
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
            clean_recommended_targets,
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
            clean_recommended_targets,
            "Removed peak packages",
        )

if __name__ == "__main__":
    main()
