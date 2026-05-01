"""CLI interface for package tracking and analysis."""

import argparse
import subprocess
import sys
from collections.abc import Iterable, Iterator
from importlib import metadata

from .api import (
    PackageData,
    calculate_peak_packages,
    collect_package_data,
    compute_base_diff,
    compute_diff,
    save_snapshot,
    SnapshotNotFoundError,
    get_pkg_name
)


COLOR_GREEN = "\033[92m"
COLOR_RED = "\033[91m"
COLOR_YELLOW = "\033[93m"
COLOR_RESET = "\033[0m"


class _DefaultsFormatter(argparse.RawDescriptionHelpFormatter):
    """Show defaults only for BooleanOptionalAction flags, not for simple store_true/store_false or optional arguments."""
    def _get_help_string(self, action):
        help_text = action.help or ""
        # Only add default for BooleanOptionalAction with non-None defaults
        if (action.default not in (None, argparse.SUPPRESS)): 
            if help_text:
                help_text += f" (default: {action.default})"
            else:
                help_text = f"(default: {action.default})"
        return help_text


def _get_cli_version() -> str:
    """Return installed package version when available."""
    try:
        return metadata.version("pkg-tracker")
    except metadata.PackageNotFoundError:
        return "dev"


def format_pkg_output(pkg_id: int, pkg_data: PackageData) -> str:
    """Color packages based on why they appear in the report."""
    pkg_name = get_pkg_name(pkg_id)
    if pkg_name is None:
        return str(pkg_id)

    depender_id = pkg_data.alternative_deps.get(pkg_id)
    if depender_id is not None:
        depender_name = get_pkg_name(depender_id)
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


def fetch_descriptions(package_names: Iterable[str]) -> Iterator[str]:
    """Yield package lines enriched with apt descriptions when available."""
    names = list(package_names)
    descriptions: dict[str, str] = {}

    if not names:
        return

    try:
        result = subprocess.run(
            ["apt-cache", "show", *names],
            capture_output=True,
            text=True,
            timeout=30,
        )
        current_pkg = None
        for line in result.stdout.splitlines():
            if line.startswith("Package:"):
                current_pkg = line.split(":", 1)[1].strip()
            elif line.startswith("Description") and current_pkg and current_pkg not in descriptions:
                # Handle Description-en, Description-fr, etc. - grab the first one
                if ":" in line:
                    desc = line.split(":", 1)[1].strip()
                    if desc:  # Only store if there's actual content
                        descriptions[current_pkg] = desc
    except (subprocess.SubprocessError, FileNotFoundError):
        descriptions = {}

    for name in names:
        desc = descriptions.get(name)
        if desc:
            yield f"{name}: {desc}"
        else:
            yield name


def print_legend() -> None:
    """Print the color legend for package output."""
    print(
        f"Legend: {COLOR_GREEN}green{COLOR_RESET} = recommended, "
        f"{COLOR_YELLOW}yellow{COLOR_RESET} = suggested, "
        f"{COLOR_RED}red{COLOR_RESET} = alternative dependency (required by shown package)\n"
    )


def print_changes_report(
    header: str,
    new_pkgs: list[int],
    rem_pkgs: list[int],
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
            print(f"  - {get_pkg_name(pkg)}")

    if not new_pkgs and not rem_pkgs:
        print("No changes.")

def print_pipe_output(package_names: Iterable[str], include_descriptions: bool) -> None:
    """Print package names (optionally enriched with descriptions) for piped output."""
    pipe: Iterator[str] = iter(package_names)
    if include_descriptions:
        pipe = fetch_descriptions(pipe)
    for name in pipe:
        print(name)


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description=(
            "Track manually installed (peak) packages, create snapshots, and compare package state over time."
        ),
        formatter_class=_DefaultsFormatter,
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
        help="Exclude packages that are recommended but not peak (disable with -n or --no-filter-non-peak-recommended).",
    )
    parser.add_argument(
        "-n",
        dest="filter_non_peak_recommended",
        action="store_false",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "-s",
        "--filter-non-peak-suggested",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Exclude packages that are suggested but not peak (enable with -s or --filter-non-peak-suggested).",
    )
    parser.add_argument(
        "-d",
        "--descriptions",
        action="store_true",
        help="Show package descriptions",
    )
    parser.add_argument(
        "-v",
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

    is_tty = sys.stdout.isatty() and not args.descriptions

    pkg_data = collect_package_data()

    current_peak_packages = calculate_peak_packages(
        pkg_data,
        args.filter_non_peak_recommended,
        args.filter_non_peak_suggested,
    )

    if not args.create and not args.diff and not args.base and not args.name:
        if is_tty:
            print(f"--- Installed Peak packages ({len(current_peak_packages)} total) ---")
            print_legend()
            for pkg in current_peak_packages.sorted_by_name():
                print(f"  * {format_pkg_output(pkg, pkg_data)}")
        else:
            print_pipe_output(sorted(current_peak_packages.names()), args.descriptions)
        return

    if args.create:
        file_path = save_snapshot(args.create, current_peak_packages)
        if is_tty:
            print(f"Saved successfully: {len(current_peak_packages)} packages recorded ({file_path}).")
        return

    try:
        if args.base:
            base_name, target_name = args.base[0], args.base[1]
            new_set, rem_set = compute_base_diff(base_name, target_name,
                                                 current_peak_packages, pkg_data.installed_packages)
            if is_tty:
                print_changes_report(
                    f"--- Changes since [{target_name}] (base system filter: [{base_name}]) ---",
                    new_set.sorted_by_name(),
                    rem_set.sorted_by_name(),
                    pkg_data,
                    "Missing (removed) peak packages"
                )
            else:
                print_pipe_output(sorted(new_set.names()), args.descriptions)
            return

        target_name = args.diff if args.diff else args.name
        new_set, rem_set = compute_diff(target_name, current_peak_packages, pkg_data.installed_packages)
        if is_tty:
            print_changes_report(
                f"--- Changes since [{target_name}] ---",
                new_set.sorted_by_name(),
                rem_set.sorted_by_name(),
                pkg_data,
                "Removed peak packages"
            )
        else:
            print_pipe_output(sorted(new_set.names()), args.descriptions)
    except SnapshotNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
