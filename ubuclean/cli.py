"""UbuClean CLI mode (without opening the GUI)."""

from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
from typing import Any, Sequence

from ubuclean import constants
from ubuclean.core.context import Context
from ubuclean.core.paths import AppPaths
from ubuclean.core.registry import CATEGORY_MAP, default_selected_ids
from ubuclean.core.scan import CleanAssistant, PreviewRequired
from ubuclean.core.util import format_bytes, format_percent


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Must be a number.") from exc
    if number <= 0:
        raise argparse.ArgumentTypeError("Must be greater than 0.")
    return number


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="ubuclean",
        description="System cleaner, monitor, and maintenance for Ubuntu Desktop.",
    )
    parser.add_argument("--version", action="version", version=f"{constants.APP_NAME} {constants.VERSION}")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the plan without changing anything.",
    )
    parser.add_argument(
        "--run-scheduled",
        action="store_true",
        help="Run the scheduled cleanup without opening the GUI.",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Run the notification watcher in the background.",
    )
    parser.add_argument(
        "--page",
        choices=("home", "clean", "monitor"),
        default=None,
        help="Initial page when the GUI opens.",
    )
    subparsers = parser.add_subparsers(dest="command")

    scan = subparsers.add_parser("scan", help="Scan categories and show a preview.")
    scan.add_argument("--category", action="append", choices=sorted(CATEGORY_MAP), help="Categories to scan (may be repeated).")
    scan.add_argument("--all", action="store_true", help="Scan all categories.")
    scan.add_argument("--json", action="store_true", help="Output JSON.")
    scan.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        default=argparse.SUPPRESS,
        help="Change nothing (default for scan).",
    )

    clean = subparsers.add_parser("clean", help="Scan, then clean after confirmation.")
    clean.add_argument("--category", action="append", choices=sorted(CATEGORY_MAP), help="Categories to clean.")
    clean.add_argument("--all", action="store_true", help="Clean all categories.")
    clean.add_argument("--yes", "-y", action="store_true", help="Skip the confirmation prompt.")
    clean.add_argument("--json", action="store_true", help="Output JSON.")
    clean.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        default=argparse.SUPPRESS,
        help="Show the plan without changing anything.",
    )

    status = subparsers.add_parser("status", help="Show system status.")
    status.add_argument("--json", action="store_true", help="Output JSON.")

    history = subparsers.add_parser("history", help="Show action history.")
    history.add_argument("--limit", type=_positive_int, default=20, help="Number of entries (default 20).")
    history.add_argument("--json", action="store_true", help="Output JSON.")

    return parser


def _select_categories(args: argparse.Namespace) -> list[str]:
    if args.all:
        return list(CATEGORY_MAP)
    return list(args.category or default_selected_ids())


def _print_previews(previews: dict[str, Any]) -> None:
    total = sum(preview.total_bytes for preview in previews.values())
    print(f"Total reclaimable: {format_bytes(total)}")
    for category_id, preview in previews.items():
        category = CATEGORY_MAP[category_id]
        if not preview.supported:
            print(f"  ! {category.label}: unavailable")
            for error in preview.errors:
                print(f"      {error}")
            continue
        print(f"  - {category.label}: {format_bytes(preview.total_bytes)} ({preview.item_count} item(s))")
        for note in preview.notes:
            print(f"      {note}")


def _confirm() -> bool:
    if not sys.stdin.isatty():
        print("Confirmation required. Re-run with --yes for non-interactive mode.", file=sys.stderr)
        return False
    try:
        answer = input("Continue with the cleanup? [y/N] ").strip().lower()
    except EOFError:
        return False
    return answer in {"y", "ya", "yes"}


def _command_scan(args: argparse.Namespace, dry_run: bool) -> int:
    ctx = Context.build(dry_run=dry_run)
    assistant = CleanAssistant(ctx)
    previews = assistant.scan(_select_categories(args))
    if args.json:
        payload = {
            "dry_run": True,
            "total_bytes": sum(preview.total_bytes for preview in previews.values()),
            "previews": [preview.to_dict() for preview in previews.values()],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    _print_previews(previews)
    return 0


def _command_clean(args: argparse.Namespace, dry_run: bool) -> int:
    ctx = Context.build(dry_run=dry_run)
    assistant = CleanAssistant(ctx)
    previews = assistant.scan(_select_categories(args))
    if not args.json:
        _print_previews(previews)
    if not dry_run and not args.yes and not _confirm():
        print("Cancelled.", file=sys.stderr)
        return 2
    try:
        report = assistant.clean(previews, dry_run=dry_run)
    except PreviewRequired as exc:
        print(f"Rejected: {exc}", file=sys.stderr)
        return 3
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        return 0
    action = "Preview" if dry_run else "Cleaned"
    print(f"{action}: {format_bytes(report.freed_bytes)}")
    for outcome in report.outcomes:
        category = CATEGORY_MAP.get(outcome.category_id)
        label = category.label if category else outcome.category_id
        print(f"  - {label}: {format_bytes(outcome.freed_bytes)} ({outcome.removed} item(s))")
        if outcome.quarantined:
            print(f"      {format_bytes(outcome.quarantined_bytes)} moved to quarantine")
        for error in outcome.errors:
            print(f"      ! {error}")
    if report.failures:
        print(f"Finished with {report.failures} failure(s).", file=sys.stderr)
        return 1
    return 0


def _command_status(args: argparse.Namespace) -> int:
    try:
        import psutil
    except ImportError:
        print("psutil is not installed. Run: sudo apt install python3-psutil", file=sys.stderr)
        return 1
    paths = AppPaths.default()
    memory = psutil.virtual_memory()
    swap = psutil.swap_memory()
    disk = psutil.disk_usage(str(paths.home))
    payload = {
        "ram_percent": round(memory.percent, 1),
        "ram_used": memory.used,
        "ram_total": memory.total,
        "swap_percent": round(swap.percent, 1),
        "swap_used": swap.used,
        "swap_total": swap.total,
        "disk_percent": round(disk.percent, 1),
        "disk_used": disk.used,
        "disk_total": disk.total,
        "cpu_percent": psutil.cpu_percent(interval=0.2),
        "load_1m": psutil.getloadavg()[0],
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    print(f"RAM    : {format_percent(payload['ram_percent'], precision=1)} "
          f"({format_bytes(payload['ram_used'])} / {format_bytes(payload['ram_total'])})")
    print(f"Swap   : {format_percent(payload['swap_percent'], precision=1)} "
          f"({format_bytes(payload['swap_used'])} / {format_bytes(payload['swap_total'])})")
    print(f"Disk   : {format_percent(payload['disk_percent'], precision=1)} "
          f"({format_bytes(payload['disk_used'])} / {format_bytes(payload['disk_total'])})")
    print(f"CPU    : {format_percent(payload['cpu_percent'], precision=1)} · load {payload['load_1m']:.2f}")
    return 0


def _command_history(args: argparse.Namespace) -> int:
    from ubuclean.core.journal import HistoryLog

    paths = AppPaths.default()
    entries = HistoryLog(paths.log_file).entries(limit=args.limit)
    if args.json:
        print(json.dumps([entry.to_dict() for entry in entries], ensure_ascii=False, indent=2))
        return 0
    if not entries:
        print("No history yet.")
        return 0
    for entry in entries:
        status = "OK" if entry.ok else "FAILED"
        marker = " (preview)" if entry.dry_run else ""
        print(
            f"{entry.to_dict()['time']}  {status}{marker}  {entry.label}: "
            f"{format_bytes(entry.bytes)} · {entry.items} item(s)"
        )
        for error in entry.errors:
            print(f"    ! {error}")
    return 0


def _command_scheduled() -> int:
    from ubuclean.core.scheduled import run_scheduled

    outcome = run_scheduled(notify=True)
    print(f"Scheduled cleanup: {format_bytes(outcome.freed_bytes)}")
    if outcome.cleaned:
        print("  Cleaned: " + ", ".join(outcome.cleaned))
    if outcome.skipped:
        print("  Skipped: " + ", ".join(outcome.skipped))
    if outcome.notified:
        print("  Desktop notification sent.")
    for error in outcome.errors:
        print(f"  ! {error}", file=sys.stderr)
    return 1 if outcome.errors else 0


def _command_watch() -> int:
    from ubuclean.core.context import Context
    from ubuclean.core.notifier import Notifier

    stop = threading.Event()

    def handle_signal(_signum: int, _frame: Any) -> None:
        stop.set()

    for name in ("SIGINT", "SIGTERM"):
        if hasattr(signal, name):
            try:
                signal.signal(getattr(signal, name), handle_signal)
            except (OSError, ValueError):
                pass
    notifier = Notifier(Context.build())
    notifier.watch(stop)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point; with no subcommand it opens the GUI."""
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(raw_argv)
    dry_run = bool(args.dry_run)
    if args.run_scheduled:
        return _command_scheduled()
    if args.watch:
        return _command_watch()
    if args.command == "scan":
        return _command_scan(args, dry_run)
    if args.command == "clean":
        return _command_clean(args, dry_run)
    if args.command == "status":
        return _command_status(args)
    if args.command == "history":
        return _command_history(args)
    if args.command is None:
        from ubuclean.app import run_gui

        return run_gui(raw_argv, page=args.page or "home")
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
