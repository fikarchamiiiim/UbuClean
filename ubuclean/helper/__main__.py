"""UbuClean root helper — only accepts whitelisted actions."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Callable, Sequence

from ubuclean.constants import HELPER_NAME
from ubuclean.helper import actions, validate
from ubuclean.helper.actions import ActionResult, HelperContext


def _typed(converter: Callable[[str], object]) -> Callable[[str], object]:
    """Wrap a validator so the argparse message stays friendly."""

    def wrapper(value: str) -> object:
        try:
            return converter(value)
        except validate.ValidationError as exc:
            raise argparse.ArgumentTypeError(str(exc)) from exc

    return wrapper


def _emit(result: ActionResult) -> None:
    print(json.dumps(result.to_dict(), ensure_ascii=False))
    for error in result.errors:
        print(error, file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    """Build the parser with whitelisted subcommands and type validation."""
    parser = argparse.ArgumentParser(
        prog=HELPER_NAME,
        description="UbuClean root helper. Only whitelisted actions are accepted.",
    )
    subparsers = parser.add_subparsers(dest="action", required=True)

    clean_apt_parser = subparsers.add_parser("clean-apt", help="apt-get clean")
    clean_apt_parser.set_defaults(handler=lambda ctx, args: actions.clean_apt(ctx))

    autoremove_parser = subparsers.add_parser("autoremove", help="apt-get autoremove")
    autoremove_parser.set_defaults(handler=lambda ctx, args: actions.autoremove(ctx))

    kernels = subparsers.add_parser("purge-kernels", help="Delete old kernel packages")
    kernels.add_argument("packages", nargs="+", type=_typed(validate.kernel_package))
    kernels.set_defaults(handler=lambda ctx, args: actions.purge_kernels(ctx, args.packages))

    snap_revisions = subparsers.add_parser("snap-old-revisions", help="Delete disabled snap revisions")
    snap_revisions.add_argument("specs", nargs="+", type=_typed(validate.snap_spec))
    snap_revisions.set_defaults(
        handler=lambda ctx, args: actions.snap_old_revisions(ctx, [f"{name}:{rev}" for name, rev in args.specs])
    )

    flatpak_parser = subparsers.add_parser("flatpak-unused", help="Delete unused Flatpak runtimes")
    flatpak_parser.set_defaults(handler=lambda ctx, args: actions.flatpak_unused(ctx))

    journal_parser = subparsers.add_parser("journal-vacuum", help="Vacuum journald")
    journal_parser.add_argument("span", type=_typed(validate.journal_span))
    journal_parser.set_defaults(handler=lambda ctx, args: actions.journal_vacuum(ctx, args.span))

    crash_parser = subparsers.add_parser("clean-crash", help="Delete crash reports")
    crash_parser.add_argument("names", nargs="+", type=_typed(validate.crash_name))
    crash_parser.set_defaults(handler=lambda ctx, args: actions.clean_crash(ctx, args.names))

    tmp_parser = subparsers.add_parser("clean-tmp", help="Delete old /tmp files")
    tmp_parser.add_argument("days", type=_typed(validate.day_count))
    tmp_parser.set_defaults(handler=lambda ctx, args: actions.clean_tmp(ctx, args.days))

    drop_parser = subparsers.add_parser("drop-caches", help="Drop kernel caches")
    drop_parser.add_argument("level", type=_typed(validate.cache_level))
    drop_parser.set_defaults(handler=lambda ctx, args: actions.drop_caches(ctx, args.level))

    swap_parser = subparsers.add_parser("swap-refresh", help="swapoff -a && swapon -a")
    swap_parser.set_defaults(handler=lambda ctx, args: actions.swap_refresh(ctx))

    apt_remove_parser = subparsers.add_parser("apt-remove", help="Remove a single apt package")
    apt_remove_parser.add_argument("package", type=_typed(validate.removable_package))
    apt_remove_parser.set_defaults(handler=lambda ctx, args: actions.apt_remove(ctx, args.package))

    snap_remove_parser = subparsers.add_parser("snap-remove", help="Remove a single snap")
    snap_remove_parser.add_argument("name", type=_typed(validate.snap_name))
    snap_remove_parser.set_defaults(handler=lambda ctx, args: actions.snap_remove(ctx, args.name))

    flatpak_remove_parser = subparsers.add_parser("flatpak-uninstall", help="Uninstall a single Flatpak")
    flatpak_remove_parser.add_argument("ref", type=_typed(validate.flatpak_ref))
    flatpak_remove_parser.set_defaults(
        handler=lambda ctx, args: actions.flatpak_uninstall(ctx, args.ref)
    )

    docker_parser = subparsers.add_parser("docker-prune", help="Prune one Docker object type")
    docker_parser.add_argument("target", choices=validate.DOCKER_TARGETS)
    docker_parser.set_defaults(handler=lambda ctx, args: actions.docker_prune(ctx, args.target))

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the helper; refuses direct execution outside pkexec."""
    if os.geteuid() != 0:
        _emit(ActionResult(ok=False, errors=["The helper must be run as root."]))
        return 77
    if not os.environ.get("PKEXEC_UID") and os.environ.get("UBUCLEAN_HELPER_ALLOW_ROOT") != "1":
        _emit(
            ActionResult(
                ok=False,
                errors=["The helper may only be run through pkexec with the UbuClean policy."],
            )
        )
        return 78
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 2
    try:
        result: ActionResult = handler(HelperContext(), args)
    except validate.ValidationError as exc:
        result = ActionResult(ok=False, errors=[str(exc)])
    except Exception as exc:  # the helper must not crash without a message
        result = ActionResult(ok=False, errors=[f"Helper internal error: {exc}"])
    _emit(result)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
