#!/usr/bin/env python3
import argparse
import pathlib
import subprocess
import sys
import re
from collections import deque
from typing import Dict, Tuple, List, Iterable, Iterator, cast

SNAPSHOT_DIR = pathlib.Path.home() / "package_snapshots"

# ANSI color codes for terminal formatting
COLOR_GREEN = '\033[92m'
COLOR_RESET = '\033[0m'

# Global context for package name ↔ integer mapping
class _PkgContext:
    """Global registry mapping package names to integer IDs for fast set operations."""
    def __init__(self):
        self.name_to_id: Dict[str, int] = {}
        self.id_to_name: Dict[int, str] = {}
        self.next_id = 0
    
    def get_id(self, pkg_name: str) -> int:
        """Get or create integer ID for package name."""
        if pkg_name not in self.name_to_id:
            self.name_to_id[pkg_name] = self.next_id
            self.id_to_name[self.next_id] = pkg_name
            self.next_id += 1
        return self.name_to_id[pkg_name]
    
    def get_name(self, pkg_id: int) -> str:
        """Get package name from integer ID."""
        return self.id_to_name[pkg_id]

_pkg_context = _PkgContext()

class PkgSet:
    """Set of package names using integer IDs internally for performance."""

    def _update_from_iterable(self, iterable: Iterable[str | int]) -> None:
        """Update from a homogeneous iterable of package IDs or names."""
        iterator = iter(iterable)
        try:
            first = next(iterator)
        except StopIteration:
            return

        if isinstance(first, int):
            self._ids.add(first)
            for item in cast(Iterable[int], iterator):
                self._ids.add(item)
            return

        self._ids.add(_pkg_context.get_id(first))
        for item in cast(Iterable[str], iterator):
            self._ids.add(_pkg_context.get_id(item))
    
    def __init__(self, iterable: Iterable[str | int] | None = None):
        self._ids: set[int] = set()
        if iterable is None:
            return
        if isinstance(iterable, PkgSet):
            self._ids = iterable._ids.copy()
        else:
            self._update_from_iterable(iterable)
    
    def add(self, pkg_name: str | int) -> None:
        """Add a package to the set by name (str) or ID (int)."""
        if isinstance(pkg_name, int):
            self._ids.add(pkg_name)
        else:
            self._ids.add(_pkg_context.get_id(pkg_name))
    
    def update(self, iterable: Iterable[str | int] | set[int]) -> None:
        """Update the set with multiple packages by name or ID."""
        if isinstance(iterable, PkgSet):
            self._ids.update(iterable._ids)
        else:
            self._update_from_iterable(iterable)
    
    def __contains__(self, pkg_name: str | int) -> bool:
        """Check if package is in the set by name or ID."""
        if isinstance(pkg_name, int):
            return pkg_name in self._ids
        if pkg_name not in _pkg_context.name_to_id:
            return False
        return _pkg_context.get_id(pkg_name) in self._ids
    
    def __iter__(self) -> Iterator[int]:
        """Iterate over package IDs (integers)."""
        return iter(self._ids)
    
    def names(self) -> Iterator[str]:
        """Iterate over package names (strings)."""
        return (_pkg_context.get_name(pkg_id) for pkg_id in self._ids)
    
    def __len__(self) -> int:
        """Return number of packages in the set."""
        return len(self._ids)
    
    def __sub__(self, other: 'PkgSet') -> 'PkgSet':
        """Set difference: self - other."""
        result = PkgSet()
        result._ids = self._ids - other._ids
        return result
    
    def __or__(self, other: 'PkgSet') -> 'PkgSet':
        """Set union: self | other."""
        result = PkgSet()
        result._ids = self._ids | other._ids
        return result
    
    def __and__(self, other: 'PkgSet') -> 'PkgSet':
        """Set intersection: self & other."""
        result = PkgSet()
        result._ids = self._ids & other._ids
        return result
    
    def __eq__(self, other: object) -> bool:
        """Check equality with another PkgSet."""
        if not isinstance(other, PkgSet):
            return False
        return self._ids == other._ids
    
    def __isub__(self, other: 'PkgSet') -> 'PkgSet':
        """In-place set difference: self -= other."""
        self._ids -= other._ids
        return self
    
    def __repr__(self) -> str:
        """String representation."""
        return f"PkgSet({sorted(self.names())})"

def get_manual_packages() -> PkgSet:
    """Get manually installed packages using apt-mark."""
    try:
        result = subprocess.run(['apt-mark', 'showmanual'], capture_output=True, text=True, check=True)
        return PkgSet(filter(None, result.stdout.split('\n')))
    except subprocess.CalledProcessError as e:
        print(f"Error while querying apt: {e}", file=sys.stderr)
        sys.exit(1)

def get_installed_packages() -> PkgSet:
    """Get currently installed package names using dpkg-query."""
    try:
        result = subprocess.run(
            ['dpkg-query', '-W', '-f=${Package}\n'],
            capture_output=True,
            text=True,
            check=True,
        )
        return PkgSet(filter(None, result.stdout.split('\n')))
    except subprocess.CalledProcessError as e:
        print(f"Error while querying installed packages: {e}", file=sys.stderr)
        sys.exit(1)

def get_dependencies_data(installed_packages: PkgSet) -> Tuple[PkgSet, Dict[int, PkgSet], PkgSet]:
    """
    Parse the dpkg status file and collect:
    1. Strict dependencies (Depends, Pre-Depends)
    2. Recommended packages map: recommended package -> package that recommends it
    3. Recommender packages: package names that have a Recommends field
    """
    strict_deps: PkgSet = PkgSet()
    recommends_deps: Dict[int, PkgSet] = {}
    recommender_packages: PkgSet = PkgSet()
    current_package: str | None = None

    try:
        with open('/var/lib/dpkg/status', 'r', encoding='utf-8') as f:
            for line in f:
                if line.startswith('Package:'):
                    current_package = line.split(':', 1)[1].strip()

                elif line.startswith(('Depends:', 'Pre-Depends:')):
                    deps_str = line.split(':', 1)[1]
                    for dep_group in deps_str.split(','):
                        alternatives = []
                        for dep in dep_group.split('|'):
                            parts = dep.strip().split()
                            if parts:
                                alternatives.append(parts[0])
                        if all(pkg in installed_packages for pkg in alternatives):
                            strict_deps.update(alternatives)

                elif line.startswith('Recommends:'):
                    if current_package:
                        recommender_packages.add(current_package)
                    deps_str = line.split(':', 1)[1]
                    for dep in re.split(r'[,|]', deps_str):
                        parts = dep.strip().split()
                        if parts and current_package:
                            recommends_deps.setdefault(_pkg_context.get_id(parts[0]), PkgSet()).add(current_package)

    except FileNotFoundError:
        print("Error: Could not find /var/lib/dpkg/status.", file=sys.stderr)

    return strict_deps, recommends_deps, recommender_packages

def format_pkg_output(pkg_name, recommended_set):
    """Color the package name green if it is in the recommended set."""
    if pkg_name in recommended_set:
        return f"{COLOR_GREEN}{pkg_name}{COLOR_RESET}"
    return pkg_name

def load_snapshot(snapshot_name):
    """Load a snapshot file by name and return it as a PkgSet of package names."""
    file_path = SNAPSHOT_DIR / f"{snapshot_name}.txt"
    if not file_path.exists():
        print(f"Error: No snapshot named '{snapshot_name}'. Looked in: '{SNAPSHOT_DIR}'", file=sys.stderr)
        return None
    with open(file_path, 'r') as f:
        return PkgSet(f.read().splitlines())

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
    recommends_deps: Dict[int, PkgSet], recommender_packages: PkgSet
) -> PkgSet:
    """Keep only leaf recommended packages (recommended, but not recommenders)."""
    return PkgSet(recommends_deps) - recommender_packages

def find_recommend_circles(
    recommends_deps: Dict[int, PkgSet], leaf_pkgs: PkgSet
) -> Tuple[List[PkgSet], PkgSet]:
    """
    Find connected circles among leaf packages.

    Two leaf packages are connected when they share at least one recommender,
    and circles are connected components in this graph.
    """
    if not leaf_pkgs:
        return [], PkgSet()

    recommender_to_leafs: Dict[int, PkgSet] = {}
    for leaf_id in leaf_pkgs:
        for recommender_id in recommends_deps.get(leaf_id, PkgSet()):
            recommender_to_leafs.setdefault(recommender_id, PkgSet()).add(leaf_id)

    adjacency: Dict[int, PkgSet] = {pkg_id: PkgSet() for pkg_id in leaf_pkgs}
    for linked_leaf_ids in recommender_to_leafs.values():
        if len(linked_leaf_ids) < 2:
            continue
        for leaf_id in linked_leaf_ids:
            adjacency[leaf_id].update(linked_leaf_ids - PkgSet({leaf_id}))

    visited: PkgSet = PkgSet()
    circles: List[PkgSet] = []
    singles: PkgSet = PkgSet()

    for leaf_id in leaf_pkgs:
        if leaf_id in visited:
            continue

        queue: deque[int] = deque([leaf_id])
        component: PkgSet = PkgSet()
        visited.add(leaf_id)

        while queue:
            cur = queue.popleft()
            component.add(cur)
            for nxt in adjacency[cur]:
                if nxt not in visited:
                    visited.add(nxt)
                    queue.append(nxt)

        if len(component) > 1:
            circles.append(component)
        else:
            singles.update(component)

    circles.sort(key=len, reverse=True)
    return circles, singles

def build_recommend_circle_data(
    recommends_deps: Dict[int, PkgSet], clean_recommenders: PkgSet
) -> Tuple[PkgSet, List[PkgSet], PkgSet]:
    """Build leaf set and connected circles derived from cleaned recommend data."""
    leaf_pkgs = leaf_recommended_packages(recommends_deps, clean_recommenders)
    circles, singles = find_recommend_circles(recommends_deps, leaf_pkgs)
    return leaf_pkgs, circles, singles

def collect_non_peak(
    circle_data: Tuple[PkgSet, List[PkgSet], PkgSet]) -> PkgSet:
    """Collect non-peak packages from circles that contain at least one peak package."""
    non_peak_packages: PkgSet = PkgSet()
    leaf_pkgs, circles, singles = circle_data
    for circle in circles:
        non_peak_packages.update(circle & leaf_pkgs)
    non_peak_packages.update(singles)
    return non_peak_packages

def print_recommend_circles(
    recommends_deps: Dict[int, PkgSet],
    circle_data: Tuple[PkgSet, List[PkgSet], PkgSet],
) -> None:
    """Compute and display recommendation circles only for leaf packages."""
    leaf_pkgs, circles, singles = circle_data


    print("--- Recommend circles (leaf recommended packages only) ---")
    print(f"Leaf set size: {len(leaf_pkgs)}")

    if not circles:
        print("No circles found.")
    else:
        print(f"Circles found: {len(circles)}")
        for idx, circle in enumerate(circles, start=1):
            print(f"\nCircle #{idx} ({len(circle)} leaf packages):")
            for pkg in sorted(circle.names()):
                pkg_id = _pkg_context.get_id(pkg)
                recommenders = sorted(recommends_deps.get(pkg_id, PkgSet()).names())
                preview = ', '.join(recommenders[:4])
                suffix = " ..." if len(recommenders) > 4 else ""
                print(f"  - {pkg}  <-  [{preview}{suffix}]")

    if singles:
        sorted_singles = sorted(singles.names())
        print(f"\nUnlinked leaf packages ({len(singles)}):")
        for pkg in sorted_singles[:50]:
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
        default=False,
        help="Show recommend circles in default listing mode.",
    )
    parser.add_argument(
        "--filter-recommend-circles",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Filter recommend circles (use --no-filter-recommend-circles to disable).",
    )
    parser.add_argument("name", nargs="?", help="Snapshot name for plain diff mode.")

    args = parser.parse_args()

    # 1. Collect data
    manual_packages = get_manual_packages()
    installed_packages = get_installed_packages()
    strict_deps, recommends_deps, recommender_packages = get_dependencies_data(installed_packages)

    # 2. Set operations
    current_peak_packages = manual_packages - strict_deps
    clean_recommenders = recommender_packages - strict_deps
    clean_recommends = {
        pkg_id: (recommenders - strict_deps)
        for pkg_id, recommenders in recommends_deps.items()
        if pkg_id not in strict_deps and (recommenders - strict_deps)
    }
    clean_recommended_targets = PkgSet(clean_recommends)
    circle_data = None
    if args.print_recommend_circles or args.filter_recommend_circles:
        circle_data = build_recommend_circle_data(clean_recommends, clean_recommenders)
    if args.filter_recommend_circles:
        non_peak_from_circles = collect_non_peak(circle_data) # type: ignore
        current_peak_packages -= non_peak_from_circles

    # --- LISTING (default mode) ---
    if not args.create and not args.diff and not args.base and not args.name:
        if args.print_recommend_circles:
            print_recommend_circles(
                clean_recommends,
                circle_data, # type: ignore
            )
            print()
        print(f"--- Installed Peak packages ({len(current_peak_packages)} total) ---")
        print(f"Legend: {COLOR_GREEN}green{COLOR_RESET} packages were likely installed as recommendations.\n")
        for pkg in sorted(current_peak_packages.names()):
            print(f"  * {format_pkg_output(pkg, clean_recommended_targets)}")
        return

    # --- SAVE SNAPSHOT ---
    if args.create:
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        file_path = SNAPSHOT_DIR / f"{args.create}.txt"
        with open(file_path, 'w') as f:
            for pkg in sorted(current_peak_packages.names()):
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

        new_set = combined_current - target_set
        rem_set = (target_set - combined_current) - installed_packages
        new_pkgs = sorted(new_set.names())
        rem_pkgs = sorted(rem_set.names())
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

        new_set = current_peak_packages - target_set
        rem_set = (target_set - current_peak_packages) - installed_packages
        new_pkgs = sorted(new_set.names())
        rem_pkgs = sorted(rem_set.names())
        print_changes_report(
            f"--- Changes since [{target_name}] ---",
            new_pkgs,
            rem_pkgs,
            clean_recommended_targets,
            "Removed peak packages",
        )

if __name__ == "__main__":
    main()
