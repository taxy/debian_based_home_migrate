#!/usr/bin/env python3
import argparse
import enum
import pathlib
import shlex
import subprocess
import sys
import re
from collections import deque
from typing import Dict, Tuple, Iterable, Iterator, cast

SNAPSHOT_DIR = pathlib.Path.home() / "package_snapshots"

class _StatusField(enum.IntEnum):
    PACKAGE     = 0
    DEPENDS     = 1
    PRE_DEPENDS = 2
    RECOMMENDS  = 3
    SUGGESTS    = 4
    PROVIDES    = 5
    ESSENTIAL   = 6
    PRIORITY    = 7

_STATUS_FIELD_MAP: Dict[str, _StatusField] = {
    'Package':     _StatusField.PACKAGE,
    'Depends':     _StatusField.DEPENDS,
    'Pre-Depends': _StatusField.PRE_DEPENDS,
    'Recommends':  _StatusField.RECOMMENDS,
    'Suggests':    _StatusField.SUGGESTS,
    'Provides':    _StatusField.PROVIDES,
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

def run_logged_subprocess(command: list[str]) -> subprocess.CompletedProcess[str]:
    """Run subprocess command and print the command line for visibility."""
    print(f"Running command: {shlex.join(command)}", file=sys.stderr)
    return subprocess.run(command, capture_output=True, text=True, check=True)

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
        result = run_logged_subprocess(['apt-mark', 'showmanual'])
        lines = result.stdout.splitlines()
        cleaned_packages = {line.partition(':')[0] for line in lines if line.strip()}
        return PkgSet(cleaned_packages)
    except subprocess.CalledProcessError as e:
        print(f"Error while querying apt: {e}", file=sys.stderr)
        sys.exit(1)

def get_installed_packages() -> PkgSet:
    """Get currently installed package names using dpkg-query."""
    try:
        result = run_logged_subprocess(['dpkg-query', '-W', '-f=${Package}\n'])
        return PkgSet(filter(None, result.stdout.split('\n')))
    except subprocess.CalledProcessError as e:
        print(f"Error while querying installed packages: {e}", file=sys.stderr)
        sys.exit(1)

def get_dependencies_data() -> Tuple[PkgSet, Dict[int, PkgSet], PkgSet, Dict[int, PkgSet], PkgSet, Dict[int, int]]:
    """
    Parse the dpkg status file and collect:
    1. Strict dependencies (Depends, Pre-Depends)
    2. Recommended packages map: recommended package -> package that recommends it
    3. Recommender packages: package names that have a Recommends field
    4. Suggested packages map: suggested package -> package that suggests it
    5. System packages: Essential: yes or Priority: required/important
    6. Alternative dependencies: alternative package -> package that depends on it

    Provides aliases are collected internally and used to expand strict dependencies
    to the real packages that satisfy virtual package names.
    """
    strict_deps: PkgSet = PkgSet()
    recommends_deps: Dict[int, PkgSet] = {}
    recommender_packages: PkgSet = PkgSet()
    suggests_deps: Dict[int, PkgSet] = {}
    system_packages: PkgSet = PkgSet()
    alternative_deps: Dict[int, int] = {}
    provides_map: Dict[int, PkgSet] = {}
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
                        strict_deps.add(dep_alternatives[0].partition(' ')[0])
                        if len(dep_alternatives) > 1:
                            alternatives = tuple(
                                dep.strip().partition(' ')[0]
                                for dep in dep_alternatives[1:]
                            )
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

                elif field is _StatusField.PROVIDES:
                    real_pkg_id = _pkg_context.get_id(current_package)
                    for provided in value.split(','):
                        provided = provided.strip()
                        if not provided:
                            continue
                        virtual_name = provided.partition(' ')[0]
                        provides_map.setdefault(_pkg_context.get_id(virtual_name), PkgSet()).add(real_pkg_id)

                elif field is _StatusField.ESSENTIAL:
                    if value == 'yes':
                        system_packages.add(current_package)

                elif field is _StatusField.PRIORITY:
                    if value in ('required', 'important'):
                        system_packages.add(current_package)

    except FileNotFoundError:
        print("Error: Could not find /var/lib/dpkg/status.", file=sys.stderr)

    for dep_id in tuple(strict_deps):
        provided_by = provides_map.get(dep_id)
        if provided_by:
            strict_deps.update(provided_by)

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
) -> PkgSet:
    """Collect non-peak packages but recommended."""
    leaf_pkgs = leaf_recommended_packages(recommends_deps, clean_recommenders)
    
    return  get_pulled_in_from_leaves(
        recommends_deps,
        leaf_pkgs,
    )

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
    strict_deps, recommends_deps, recommender_packages, suggests_deps, system_packages, alternative_deps = get_dependencies_data()

    # 2. Set operations
    current_peak_packages = manual_packages - strict_deps - system_packages
    if args.filter_non_peak_recommended:
        non_peak_recommends = collect_non_peak_recommended(recommends_deps, recommender_packages)
        current_peak_packages -= non_peak_recommends

    # --- LISTING (default mode) ---
    if not args.create and not args.diff and not args.base and not args.name:
        print(f"--- Installed Peak packages ({len(current_peak_packages)} total) ---")
        print_legend()
        for pkg in sorted(current_peak_packages.names()):
            print(f"  * {format_pkg_output(pkg, recommends_deps, suggests_deps, alternative_deps)}")
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
            recommends_deps,
            suggests_deps,
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
            recommends_deps,
            suggests_deps,
            alternative_deps,
            "Removed peak packages",
        )

if __name__ == "__main__":
    main()
