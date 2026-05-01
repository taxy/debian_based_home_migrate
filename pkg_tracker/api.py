"""Public API layer for package data collection."""

from dataclasses import dataclass

from pathlib import Path

from .core import (
    collect_non_peak,
    get_dependencies_data,
    launch_manual_packages_query,
    launch_installed_packages_query,
    load_snapshot,
    parse_manual_packages_output,
    parse_installed_packages_output,
    PkgSet,
    SNAPSHOT_DIR,
)


@dataclass
class PackageData:
    """Batch of collected package data from queries and dependencies."""

    manual_packages: PkgSet
    installed_packages: PkgSet
    strict_deps: PkgSet
    recommends_deps: dict
    recommender_packages: PkgSet
    suggests_deps: dict
    suggester_packages: PkgSet
    system_packages: PkgSet
    alternative_deps: dict


def collect_package_data() -> PackageData:
    """Launch async queries, collect dependency data, and return batched results."""
    manual_packages_process = launch_manual_packages_query()
    installed_packages_process = launch_installed_packages_query()
    try:
        (
            strict_deps,
            recommends_deps,
            recommender_packages,
            suggests_deps,
            suggester_packages,
            system_packages,
            alternative_deps,
        ) = get_dependencies_data()
        installed_packages = parse_installed_packages_output(installed_packages_process)
        manual_packages = parse_manual_packages_output(manual_packages_process)
    finally:
        manual_packages_process.kill_if_running()
        manual_packages_process.close()

        installed_packages_process.kill_if_running()
        installed_packages_process.close()

    return PackageData(
        manual_packages=manual_packages,
        installed_packages=installed_packages,
        strict_deps=strict_deps,
        recommends_deps=recommends_deps,
        recommender_packages=recommender_packages,
        suggests_deps=suggests_deps,
        suggester_packages=suggester_packages,
        system_packages=system_packages,
        alternative_deps=alternative_deps,
    )


def calculate_peak_packages(
    pkg_data: PackageData,
    filter_non_peak_recommended: bool,
    filter_non_peak_suggested: bool,
) -> PkgSet:
    """Calculate current peak packages based on dependency filtering options.

    Args:
        pkg_data: Collected package data with dependencies
        filter_non_peak_recommended: Exclude recommended but non-peak packages
        filter_non_peak_suggested: Exclude suggested but non-peak packages

    Returns:
        PkgSet of calculated peak packages
    """
    current_peak_packages = pkg_data.manual_packages - pkg_data.strict_deps - pkg_data.system_packages
    if filter_non_peak_recommended:
        non_peak_recommends = collect_non_peak(pkg_data.recommends_deps, pkg_data.recommender_packages)
        current_peak_packages -= non_peak_recommends
    if filter_non_peak_suggested:
        non_peak_suggests = collect_non_peak(pkg_data.suggests_deps, pkg_data.suggester_packages)
        current_peak_packages -= non_peak_suggests
    return current_peak_packages


def compute_base_diff(
    base_name: str,
    target_name: str,
    current_peak_packages: PkgSet,
    installed_packages: PkgSet,
) -> tuple[PkgSet, PkgSet]:
    """Load base and target snapshots and compute new/removed package sets.

    Returns (new_set, rem_set).

    Raises:
        SnapshotNotFoundError: if either snapshot file does not exist.
    """
    base_set = load_snapshot(base_name)
    target_set = load_snapshot(target_name)
    new_set = current_peak_packages - target_set
    rem_set = (target_set - base_set) - installed_packages
    return new_set, rem_set


def compute_diff(
    target_name: str,
    current_peak_packages: PkgSet,
    installed_packages: PkgSet,
) -> tuple[PkgSet, PkgSet]:
    """Load a snapshot and compute new/removed package sets against it.

    Returns (new_set, rem_set).

    Raises:
        SnapshotNotFoundError: if the snapshot file does not exist.
    """
    target_set = load_snapshot(target_name)
    new_set = current_peak_packages - target_set
    rem_set = target_set - installed_packages
    return new_set, rem_set


def save_snapshot(snapshot_name: str, current_peak_packages: PkgSet) -> Path:
    """Persist the current peak package set under the provided snapshot name."""
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    file_path = SNAPSHOT_DIR / f"{snapshot_name}.txt"
    with open(file_path, "w", encoding="utf-8") as snapshot_file:
        for pkg in sorted(current_peak_packages.names()):
            snapshot_file.write(f"{pkg}\n")
    return file_path
