"""Core library for tracking and analyzing Debian/Ubuntu package dependencies."""

import enum
import pathlib
import re
import shlex
import subprocess
import sys
from collections import deque
from typing import Dict, Iterable, Iterator, Tuple, cast

SNAPSHOT_DIR = pathlib.Path.home() / "package_snapshots"


class _StatusField(enum.IntEnum):
    PACKAGE = 0
    DEPENDS = 1
    PRE_DEPENDS = 2
    RECOMMENDS = 3
    SUGGESTS = 4
    PROVIDES = 5
    ESSENTIAL = 6
    PRIORITY = 7


_STATUS_FIELD_MAP: Dict[str, _StatusField] = {
    "Package": _StatusField.PACKAGE,
    "Depends": _StatusField.DEPENDS,
    "Pre-Depends": _StatusField.PRE_DEPENDS,
    "Recommends": _StatusField.RECOMMENDS,
    "Suggests": _StatusField.SUGGESTS,
    "Provides": _StatusField.PROVIDES,
    "Essential": _StatusField.ESSENTIAL,
    "Priority": _StatusField.PRIORITY,
}


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

    def __sub__(self, other: "PkgSet") -> "PkgSet":
        """Set difference: self - other."""
        result = PkgSet()
        result._ids = self._ids - other._ids
        return result

    def __or__(self, other: "PkgSet") -> "PkgSet":
        """Set union: self | other."""
        result = PkgSet()
        result._ids = self._ids | other._ids
        return result

    def __and__(self, other: "PkgSet") -> "PkgSet":
        """Set intersection: self & other."""
        result = PkgSet()
        result._ids = self._ids & other._ids
        return result

    def __eq__(self, other: object) -> bool:
        """Check equality with another PkgSet."""
        if not isinstance(other, PkgSet):
            return False
        return self._ids == other._ids

    def __isub__(self, other: "PkgSet") -> "PkgSet":
        """In-place set difference: self -= other."""
        self._ids -= other._ids
        return self

    def __repr__(self) -> str:
        """String representation."""
        return f"PkgSet({sorted(self.names())})"


def get_manual_packages() -> PkgSet:
    """Get manually installed packages using apt-mark."""
    try:
        result = run_logged_subprocess(["apt-mark", "showmanual"])
        lines = result.stdout.splitlines()
        cleaned_packages = {line.partition(":")[0] for line in lines if line.strip()}
        return PkgSet(cleaned_packages)
    except subprocess.CalledProcessError as error:
        print(f"Error while querying apt: {error}", file=sys.stderr)
        sys.exit(1)


def get_installed_packages() -> PkgSet:
    """Get currently installed package names using dpkg-query."""
    try:
        result = run_logged_subprocess(["dpkg-query", "-W", "-f=${Package}\\n"])
        return PkgSet(filter(None, result.stdout.split("\n")))
    except subprocess.CalledProcessError as error:
        print(f"Error while querying installed packages: {error}", file=sys.stderr)
        sys.exit(1)


def get_dependencies_data() -> Tuple[PkgSet, Dict[int, PkgSet], PkgSet, Dict[int, PkgSet], PkgSet, PkgSet, Dict[int, int]]:
    """Parse the dpkg status file and collect dependency metadata."""
    strict_deps = PkgSet()
    recommends_deps: Dict[int, PkgSet] = {}
    recommender_packages = PkgSet()
    suggests_deps: Dict[int, PkgSet] = {}
    suggester_packages = PkgSet()
    system_packages = PkgSet()
    alternative_deps: Dict[int, int] = {}
    provides_map: Dict[int, PkgSet] = {}
    current_package: str | None = None

    try:
        with open("/var/lib/dpkg/status", "r", encoding="utf-8") as status_file:
            for line in status_file:
                key, sep, value = line.partition(":")
                if not sep:
                    continue
                field = _STATUS_FIELD_MAP.get(key)
                if field is None:
                    continue
                value = value.strip()

                if field is _StatusField.PACKAGE:
                    current_package = value
                    continue

                if current_package is None:
                    continue

                if field is _StatusField.DEPENDS or field is _StatusField.PRE_DEPENDS:
                    for dep_group in value.split(","):
                        dep_group = dep_group.strip()
                        if not dep_group:
                            continue

                        dep_alternatives = dep_group.split("|")
                        strict_deps.add(dep_alternatives[0].partition(" ")[0])
                        if len(dep_alternatives) > 1:
                            alternatives = tuple(
                                dep.strip().partition(" ")[0]
                                for dep in dep_alternatives[1:]
                            )
                            for alt in alternatives:
                                alt_id = _pkg_context.get_id(alt)
                                alternative_deps[alt_id] = _pkg_context.get_id(current_package)

                elif field is _StatusField.RECOMMENDS:
                    recommender_packages.add(current_package)
                    for dep in re.split(r"[,|]", value):
                        dep = dep.strip()
                        if not dep:
                            continue
                        name = dep.partition(" ")[0]
                        recommends_deps.setdefault(_pkg_context.get_id(name), PkgSet()).add(current_package)

                elif field is _StatusField.SUGGESTS:
                    suggester_packages.add(current_package)
                    for dep in re.split(r"[,|]", value):
                        dep = dep.strip()
                        if not dep:
                            continue
                        name = dep.partition(" ")[0]
                        suggests_deps.setdefault(_pkg_context.get_id(name), PkgSet()).add(current_package)

                elif field is _StatusField.PROVIDES:
                    real_pkg_id = _pkg_context.get_id(current_package)
                    for provided in value.split(","):
                        provided = provided.strip()
                        if not provided:
                            continue
                        virtual_name = provided.partition(" ")[0]
                        provides_map.setdefault(_pkg_context.get_id(virtual_name), PkgSet()).add(real_pkg_id)

                elif field is _StatusField.ESSENTIAL:
                    if value == "yes":
                        system_packages.add(current_package)

                elif field is _StatusField.PRIORITY:
                    if value in ("required", "important"):
                        system_packages.add(current_package)

    except FileNotFoundError:
        print("Error: Could not find /var/lib/dpkg/status.", file=sys.stderr)

    for dep_id in tuple(strict_deps):
        provided_by = provides_map.get(dep_id)
        if provided_by:
            strict_deps.update(provided_by)

    return (
        strict_deps,
        recommends_deps,
        recommender_packages,
        suggests_deps,
        suggester_packages,
        system_packages,
        alternative_deps,
    )


def load_snapshot(snapshot_name: str) -> PkgSet | None:
    """Load a snapshot file by name and return it as a PkgSet of package names."""
    file_path = SNAPSHOT_DIR / f"{snapshot_name}.txt"
    if not file_path.exists():
        print(
            f"Error: No snapshot named '{snapshot_name}'. Looked in: '{SNAPSHOT_DIR}'",
            file=sys.stderr,
        )
        return None
    with open(file_path, "r", encoding="utf-8") as snapshot_file:
        return PkgSet(snapshot_file.read().splitlines())


def leaf_packages(reverse_deps: Dict[int, PkgSet], who_has_deps: PkgSet) -> PkgSet:
    """Keep only leaf packages (have dependencies, but are not depended upon)."""
    return PkgSet(reverse_deps) - who_has_deps


def get_pulled_in_from_leaves(recommends_deps: Dict[int, PkgSet], leaf_pkgs: PkgSet) -> PkgSet:
    """Traverse the recommendation graph upward from the leaves."""
    pulled_in = PkgSet()
    visited = PkgSet(leaf_pkgs)
    queue: deque[int] = deque(leaf_pkgs)

    while queue:
        curr = queue.popleft()
        parents = recommends_deps.get(curr, PkgSet())

        if parents:
            pulled_in.add(curr)

        for parent in parents:
            if parent not in visited:
                visited.add(parent)
                queue.append(parent)

    return pulled_in


def collect_non_peak(reverse_deps: Dict[int, PkgSet], who_has_deps: PkgSet) -> PkgSet:
    """Collect non-peak packages but recommended."""
    leaf_pkgs = leaf_packages(reverse_deps, who_has_deps)
    return get_pulled_in_from_leaves(reverse_deps, leaf_pkgs)