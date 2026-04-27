#!/usr/bin/env python3
import argparse
import enum
import pathlib
import subprocess
import sys
import re
from collections import deque
from typing import Dict, Tuple, List, Iterable, Iterator, cast

SNAPSHOT_DIR = pathlib.Path.home() / "package_snapshots"

class _StatusField(enum.IntEnum):
    PACKAGE     = 0
    DEPENDS     = 1
    PRE_DEPENDS = 2
    RECOMMENDS  = 3
    SUGGESTS    = 4
    ESSENTIAL   = 5
    PRIORITY    = 6

_STATUS_FIELD_MAP: Dict[str, _StatusField] = {
    'Package':     _StatusField.PACKAGE,
    'Depends':     _StatusField.DEPENDS,
    'Pre-Depends': _StatusField.PRE_DEPENDS,
    'Recommends':  _StatusField.RECOMMENDS,
    'Suggests':    _StatusField.SUGGESTS,
    'Essential':   _StatusField.ESSENTIAL,
    'Priority':    _StatusField.PRIORITY,
}

# ANSI color codes for terminal formatting
COLOR_GREEN = '\033[92m'
COLOR_RED = '\033[91m'
COLOR_YELLOW = '\033[93m'
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
        lines = result.stdout.splitlines()
        cleaned_packages = {line.partition(':')[0] for line in lines if line.strip()}
        return PkgSet(cleaned_packages)
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

def get_dependencies_data(installed_packages: PkgSet) -> Tuple[PkgSet, Dict[int, PkgSet], PkgSet, Dict[int, PkgSet], PkgSet, Dict[int, int]]:
    """
    Parse the dpkg status file and collect:
    1. Strict dependencies (Depends, Pre-Depends)
    2. Recommended packages map: recommended package -> package that recommends it
    3. Recommender packages: package names that have a Recommends field
    4. Suggested packages map: suggested package -> package that suggests it
    5. System packages: Essential: yes or Priority: required/important
    6. Alternative dependencies: alternative package -> package that depends on it
    """
    strict_deps: PkgSet = PkgSet()
    recommends_deps: Dict[int, PkgSet] = {}
    recommender_packages: PkgSet = PkgSet()
    suggests_deps: Dict[int, PkgSet] = {}
    system_packages: PkgSet = PkgSet()
    alternative_deps: Dict[int, int] = {}
    current_package: str | None = None

    try:
        with open('/var/lib/dpkg/status', 'r', encoding='utf-8') as f:
            for line in f:
                key, sep, value = line.partition(':')
                if not sep:
                    continue
                field = _STATUS_FIELD_MAP.get(key)
                if field is None:
                    continue
                value = value.strip()

                if field is _StatusField.PACKAGE:
                    current_package = value

                elif field is _StatusField.DEPENDS or field is _StatusField.PRE_DEPENDS:
                    for dep_group in value.split(','):
                        dep_group = dep_group.strip()
                        if not dep_group:
                            continue

                        dep_alternatives = dep_group.split('|')
                        if len(dep_alternatives) == 1:
                            strict_deps.add(dep_group.partition(' ')[0])
                        else:
                            alternatives = tuple(
                                dep.strip().partition(' ')[0]
                                for dep in dep_alternatives
                            )
                            if all(pkg in installed_packages for pkg in alternatives):
                                strict_deps.update(alternatives)
                            else:
                                for alt in alternatives:
                                    alt_id = _pkg_context.get_id(alt)
                                    alternative_deps[alt_id] = _pkg_context.get_id(current_package)


                elif field is _StatusField.RECOMMENDS:
                    recommender_packages.add(current_package)
                    for dep in re.split(r'[,|]', value):
                        dep = dep.strip()
                        if not dep:
                            continue
                        name = dep.partition(' ')[0]
                        recommends_deps.setdefault(_pkg_context.get_id(name), PkgSet()).add(current_package)

                elif field is _StatusField.SUGGESTS:
                    for dep in re.split(r'[,|]', value):
                        dep = dep.strip()
                        if not dep:
                            continue
                        name = dep.partition(' ')[0]
                        suggests_deps.setdefault(_pkg_context.get_id(name), PkgSet()).add(current_package)

                elif field is _StatusField.ESSENTIAL:
                    if value == 'yes':
                        system_packages.add(current_package)

                elif field is _StatusField.PRIORITY:
                    if value in ('required', 'important'):
                        system_packages.add(current_package)

    except FileNotFoundError:
        print("Error: Could not find /var/lib/dpkg/status.", file=sys.stderr)

    return strict_deps, recommends_deps, recommender_packages, suggests_deps, system_packages, alternative_deps

def format_pkg_output(pkg_name: str, clean_recommends: Dict[int, PkgSet], clean_suggests: Dict[int, PkgSet], alternative_deps: Dict[int, int]) -> str:
    """Color packages: red if alternative dependency, green if recommended, yellow if suggested.
    Priority: red > green > yellow
    """
    pkg_id = _pkg_context.name_to_id.get(pkg_name)
    if pkg_id is None:
        return pkg_name

    # Alternative dependencies take precedence (highest priority)
    depender_id = alternative_deps.get(pkg_id)
    if depender_id is not None:
        depender_name = _pkg_context.get_name(depender_id)
        return f"{COLOR_RED}{pkg_name}{COLOR_RESET}  <|  [{depender_name}]"
    
    # Check recommended (green) - higher priority than suggested
    recommenders = clean_recommends.get(pkg_id)
    if recommenders:
        recommender_list = ', '.join(sorted(recommenders.names()))
        return f"{COLOR_GREEN}{pkg_name}{COLOR_RESET}  <-  [{recommender_list}]"
    
    # Check suggested (yellow) - lower priority
    suggesters = clean_suggests.get(pkg_id)
    if suggesters:
        suggester_list = ', '.join(sorted(suggesters.names()))
        return f"{COLOR_YELLOW}{pkg_name}{COLOR_RESET}  <~  [{suggester_list}]"
    
    return pkg_name

def load_snapshot(snapshot_name):
    """Load a snapshot file by name and return it as a PkgSet of package names."""
    file_path = SNAPSHOT_DIR / f"{snapshot_name}.txt"
    if not file_path.exists():
        print(f"Error: No snapshot named '{snapshot_name}'. Looked in: '{SNAPSHOT_DIR}'", file=sys.stderr)
        return None
    with open(file_path, 'r') as f:
        return PkgSet(f.read().splitlines())

def print_legend() -> None:
    """Print the color legend for package output."""
    print(f"Legend: {COLOR_GREEN}green{COLOR_RESET} = recommended, {COLOR_YELLOW}yellow{COLOR_RESET} = suggested, {COLOR_RED}red{COLOR_RESET} = alternative dependency (required by shown package)\n")

def print_changes_report(header, new_pkgs, rem_pkgs, clean_recommends: Dict[int, PkgSet], clean_suggests: Dict[int, PkgSet], alternative_deps: Dict[int, int], removed_label):
    """Print a formatted package diff report used by both diff modes."""
    print(header)
    print_legend()

    if new_pkgs:
        print(f"New peak packages ({len(new_pkgs)}):")
        for p in new_pkgs:
            print(f"  + {format_pkg_output(p, clean_recommends, clean_suggests, alternative_deps)}")
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

def get_pulled_in_from_leaves(
    recommends_deps: Dict[int, PkgSet], 
    leaf_pkgs: PkgSet
) -> PkgSet:
    """
    Traverse the recommendation graph upward from the leaves.
    Add every touched package to the pulled_in set,
    EXCEPT the top-level packages that are not recommended by anything.
    """
    pulled_in = PkgSet()
    visited = PkgSet(leaf_pkgs)
    queue: deque[int] = deque(leaf_pkgs)

    while queue:
        curr = queue.popleft()
        
        # Which packages RECOMMEND curr? (Upward edges / parents)
        parents = recommends_deps.get(curr, PkgSet())

        # If it HAS parents (so something pulls it in), it is NOT a top package.
        # Add it to the removable set, including leaves that also have parents.
        if parents:
            pulled_in.add(curr)
        # If it has NO parents (parents is empty), then it is a TOP package.
        # Do not add it to pulled_in, so it remains among the peak packages.

        # Continue the traversal upward through the parents
        for parent in parents:
            if parent not in visited:
                visited.add(parent)
                queue.append(parent)

    return pulled_in

def collect_non_peak_recommended(
    recommends_deps: Dict[int, PkgSet], clean_recommenders: PkgSet
) -> Tuple[PkgSet, List[PkgSet], PkgSet]:
    """Collect non-peak packages but recommended."""
    leaf_pkgs = leaf_recommended_packages(recommends_deps, clean_recommenders)
    
    return  get_pulled_in_from_leaves(
        recommends_deps,
        leaf_pkgs,
    )

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
    parser = argparse.ArgumentParser(
        description=(
            "Track manually installed (peak) packages, create snapshots, and compare package state over time."
        )
    )

    parser.add_argument(
        "--create",
        metavar="NAME",
        help="Save the current peak package set as snapshot NAME.",
    )
    parser.add_argument(
        "--diff",
        metavar="NAME",
        help="Compare the current system against snapshot NAME.",
    )
    parser.add_argument(
        "--base",
        nargs=2,
        metavar=("BASE_NAME", "TARGET_NAME"),
        help="Compare current system against TARGET_NAME while ignoring packages present in BASE_NAME.",
    )
    parser.add_argument(
        "--print-recommend-circles",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="In default listing mode, also print connected recommendation groups.",
    )
    parser.add_argument(
        "--filter-non-peak-recommended",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Exclude packages that are recommended but not peak (disable with --no-filter-non-peak-recommended).",
    )
    parser.add_argument(
        "name",
        nargs="?",
        help="Optional snapshot name for plain diff mode (same as --diff NAME).",
    )

    args = parser.parse_args()

    # 1. Collect data
    manual_packages = get_manual_packages()
    installed_packages = get_installed_packages()
    strict_deps, recommends_deps, recommender_packages, suggests_deps, system_packages, alternative_deps = get_dependencies_data(installed_packages)

    # 2. Set operations
    current_peak_packages = manual_packages - strict_deps - system_packages
    clean_recommenders = recommender_packages - strict_deps - system_packages
    clean_recommends = recommends_deps
    clean_suggests = suggests_deps
    if args.filter_non_peak_recommended:
        non_peak_recommends = collect_non_peak_recommended(clean_recommends, clean_recommenders)
        current_peak_packages -= non_peak_recommends

    # --- LISTING (default mode) ---
    if not args.create and not args.diff and not args.base and not args.name:
        if args.print_recommend_circles:
            circle_data = build_recommend_circle_data(clean_recommends, clean_recommenders)
            print_recommend_circles(
                clean_recommends,
                circle_data,
            )
            print()
        print(f"--- Installed Peak packages ({len(current_peak_packages)} total) ---")
        print_legend()
        for pkg in sorted(current_peak_packages.names()):
            print(f"  * {format_pkg_output(pkg, clean_recommends, clean_suggests, alternative_deps)}")
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

  
        new_set = current_peak_packages - target_set
        rem_set = (target_set - base_set) - installed_packages
        new_pkgs = sorted(new_set.names())
        rem_pkgs = sorted(rem_set.names())
        print_changes_report(
            f"--- Changes since [{target_name}] (base system filter: [{base_name}]) ---",
            new_pkgs,
            rem_pkgs,
            clean_recommends,
            clean_suggests,
            alternative_deps,
            "Missing (removed) peak packages",
        )

    # --- PLAIN COMPARISON (diff mode) ---
    elif args.diff or args.name:
        target_name = args.diff if args.diff else args.name
        target_set = load_snapshot(target_name)
        if target_set is None:
            return

        new_set = current_peak_packages - target_set
        rem_set = target_set - installed_packages
        new_pkgs = sorted(new_set.names())
        rem_pkgs = sorted(rem_set.names())
        print_changes_report(
            f"--- Changes since [{target_name}] ---",
            new_pkgs,
            rem_pkgs,
            clean_recommends,
            clean_suggests,
            alternative_deps,
            "Removed peak packages",
        )

if __name__ == "__main__":
    main()
