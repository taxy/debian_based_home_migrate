"""CLI interface for package tracking and analysis."""

import argparse
import sys
from importlib import metadata

from .api import (
    PackageData,
    calculate_peak_packages,
    collect_package_data,
    compute_base_diff,
    compute_diff,
    save_snapshot
)

from .core import (
    _pkg_context,
    SnapshotNotFoundError,
)


COLOR_GREEN = "\033[92m"
COLOR_RED = "\033[91m"
COLOR_YELLOW = "\033[93m"
COLOR_RESET = "\033[0m"


def _get_cli_version() -> str:
    """Return installed package version when available."""
    try:
        return metadata.version("pkg-tracker")
    except metadata.PackageNotFoundError:
        return "dev"


def format_pkg_output(pkg_name: str, pkg_data: PackageData) -> str:
    """Color packages based on why they appear in the report."""
    pkg_id = _pkg_context.name_to_id.get(pkg_name)
    if pkg_id is None:
        return pkg_name

    depender_id = pkg_data.alternative_deps.get(pkg_id)
    if depender_id is not None:
        depender_name = _pkg_context.get_name(depender_id)
        return f"{COLOR_RED}{pkg_name}{COLOR_RESET}  <|  [{depender_name}]"

    recommenders = pkg_data.recommends_deps.get(pkg_id)
    if recommenders:
        recommender_list = ", ".join(sorted(recommenders.names()))
        return f"{COLOR_GREEN}{pkg_name}{COLOR_RESET}  <-  [{recommender_list}]"

    suggesters = pkg_data.suggests_deps.get(pkg_id)
    if suggesters:
        suggester_list = ", ".join(sorted(suggesters.names()))
        return f"{COLOR_YELLOW}{pkg_name}{COLOR_RESET}  <~  [{suggester_list}]"

    return pkg_name


def print_legend() -> None:
    """Print the color legend for package output."""
    print(
        f"Legend: {COLOR_GREEN}green{COLOR_RESET} = recommended, "
        f"{COLOR_YELLOW}yellow{COLOR_RESET} = suggested, "
        f"{COLOR_RED}red{COLOR_RESET} = alternative dependency (required by shown package)\n"
    )


def print_changes_report(
    header: str,
    new_pkgs: list[str],
    rem_pkgs: list[str],
    pkg_data: PackageData,
    removed_label: str,
) -> None:
    """Print a formatted package diff report used by both diff modes."""
    print(header)
    print_legend()

    if new_pkgs:
        print(f"New peak packages ({len(new_pkgs)}):")
        for pkg in new_pkgs:
            print(f"  + {format_pkg_output(pkg, pkg_data)}")
    if rem_pkgs:
        print(f"\n{removed_label} ({len(rem_pkgs)}):")
        for pkg in rem_pkgs:
            print(f"  - {pkg}")

    if not new_pkgs and not rem_pkgs:
        print("No changes.")


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
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
        "-r",
        "--filter-non-peak-recommended",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Exclude packages that are recommended but not peak (disable with --no-filter-non-peak-recommended).",
    )
    parser.add_argument(
        "-s",
        "--filter-non-peak-suggested",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Exclude packages that are suggested but not peak (enable with -s or --filter-non-peak-suggested).",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {_get_cli_version()}",
        help="Show program version and exit.",
    )
    parser.add_argument(
        "name",
        nargs="?",
        help="Optional snapshot name for plain diff mode (same as --diff NAME).",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    pkg_data = collect_package_data()

    current_peak_packages = calculate_peak_packages(
        pkg_data,
        args.filter_non_peak_recommended,
        args.filter_non_peak_suggested,
    )

    if not args.create and not args.diff and not args.base and not args.name:
        print(f"--- Installed Peak packages ({len(current_peak_packages)} total) ---")
        print_legend()
        for pkg in sorted(current_peak_packages.names()):
            print(f"  * {format_pkg_output(pkg, pkg_data)}")
        return

    if args.create:
        file_path = save_snapshot(args.create, current_peak_packages)
        print(f"Saved successfully: {len(current_peak_packages)} packages recorded ({file_path}).")
        return

    try:
        if args.base:
            base_name, target_name = args.base[0], args.base[1]
            new_set, rem_set = compute_base_diff(base_name, target_name,
                                                 current_peak_packages, pkg_data.installed_packages)
            print_changes_report(
                f"--- Changes since [{target_name}] (base system filter: [{base_name}]) ---",
                sorted(new_set.names()),
                sorted(rem_set.names()),
                pkg_data,
                "Missing (removed) peak packages",
            )
            return

        target_name = args.diff if args.diff else args.name
        new_set, rem_set = compute_diff(target_name, current_peak_packages, pkg_data.installed_packages)
        print_changes_report(
            f"--- Changes since [{target_name}] ---",
            sorted(new_set.names()),
            sorted(rem_set.names()),
            pkg_data,
            "Removed peak packages",
        )
    except SnapshotNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
