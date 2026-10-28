"""Helper root UbuClean — hanya menerima aksi dari whitelist."""

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
    """Bungkus validator agar pesan argparse ramah."""

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
    """Bangun parser dengan subcommand whitelist dan validasi tipe."""
    parser = argparse.ArgumentParser(
        prog=HELPER_NAME,
        description="Helper root UbuClean. Hanya aksi whitelist yang diterima.",
    )
    subparsers = parser.add_subparsers(dest="action", required=True)

    clean_apt_parser = subparsers.add_parser("clean-apt", help="apt-get clean")
    clean_apt_parser.set_defaults(handler=lambda ctx, args: actions.clean_apt(ctx))

    autoremove_parser = subparsers.add_parser("autoremove", help="apt-get autoremove")
    autoremove_parser.set_defaults(handler=lambda ctx, args: actions.autoremove(ctx))

    kernels = subparsers.add_parser("purge-kernels", help="Hapus paket kernel lama")
    kernels.add_argument("packages", nargs="+", type=_typed(validate.kernel_package))
    kernels.set_defaults(handler=lambda ctx, args: actions.purge_kernels(ctx, args.packages))

    snap_revisions = subparsers.add_parser("snap-old-revisions", help="Hapus revisi snap disabled")
    snap_revisions.add_argument("specs", nargs="+", type=_typed(validate.snap_spec))
    snap_revisions.set_defaults(
        handler=lambda ctx, args: actions.snap_old_revisions(ctx, [f"{name}:{rev}" for name, rev in args.specs])
    )

    flatpak_parser = subparsers.add_parser("flatpak-unused", help="Hapus runtime Flatpak tak terpakai")
    flatpak_parser.set_defaults(handler=lambda ctx, args: actions.flatpak_unused(ctx))

    journal_parser = subparsers.add_parser("journal-vacuum", help="Vakum journald")
    journal_parser.add_argument("span", type=_typed(validate.journal_span))
    journal_parser.set_defaults(handler=lambda ctx, args: actions.journal_vacuum(ctx, args.span))

    crash_parser = subparsers.add_parser("clean-crash", help="Hapus laporan crash")
    crash_parser.add_argument("names", nargs="+", type=_typed(validate.crash_name))
    crash_parser.set_defaults(handler=lambda ctx, args: actions.clean_crash(ctx, args.names))

    tmp_parser = subparsers.add_parser("clean-tmp", help="Hapus file /tmp lama")
    tmp_parser.add_argument("days", type=_typed(validate.day_count))
    tmp_parser.set_defaults(handler=lambda ctx, args: actions.clean_tmp(ctx, args.days))

    drop_parser = subparsers.add_parser("drop-caches", help="Buang cache kernel")
    drop_parser.add_argument("level", type=_typed(validate.cache_level))
    drop_parser.set_defaults(handler=lambda ctx, args: actions.drop_caches(ctx, args.level))

    swap_parser = subparsers.add_parser("swap-refresh", help="swapoff -a && swapon -a")
    swap_parser.set_defaults(handler=lambda ctx, args: actions.swap_refresh(ctx))

    apt_remove_parser = subparsers.add_parser("apt-remove", help="Hapus satu paket apt")
    apt_remove_parser.add_argument("package", type=_typed(validate.removable_package))
    apt_remove_parser.set_defaults(handler=lambda ctx, args: actions.apt_remove(ctx, args.package))

    snap_remove_parser = subparsers.add_parser("snap-remove", help="Hapus satu snap")
    snap_remove_parser.add_argument("name", type=_typed(validate.snap_name))
    snap_remove_parser.set_defaults(handler=lambda ctx, args: actions.snap_remove(ctx, args.name))

    flatpak_remove_parser = subparsers.add_parser("flatpak-uninstall", help="Hapus satu Flatpak")
    flatpak_remove_parser.add_argument("ref", type=_typed(validate.flatpak_ref))
    flatpak_remove_parser.set_defaults(
        handler=lambda ctx, args: actions.flatpak_uninstall(ctx, args.ref)
    )

    docker_parser = subparsers.add_parser("docker-prune", help="Prune satu jenis objek Docker")
    docker_parser.add_argument("target", choices=validate.DOCKER_TARGETS)
    docker_parser.set_defaults(handler=lambda ctx, args: actions.docker_prune(ctx, args.target))

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Jalankan helper; menolak eksekusi langsung di luar pkexec."""
    if os.geteuid() != 0:
        _emit(ActionResult(ok=False, errors=["Helper harus dijalankan sebagai root."]))
        return 77
    if not os.environ.get("PKEXEC_UID") and os.environ.get("UBUCLEAN_HELPER_ALLOW_ROOT") != "1":
        _emit(
            ActionResult(
                ok=False,
                errors=["Helper hanya boleh dijalankan melalui pkexec dengan policy UbuClean."],
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
    except Exception as exc:  # helper tidak boleh crash tanpa pesan
        result = ActionResult(ok=False, errors=[f"Kesalahan internal helper: {exc}"])
    _emit(result)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
