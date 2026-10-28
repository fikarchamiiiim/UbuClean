"""Pembungkus subprocess dengan argv list dan timeout.

Tidak ada pemakaian ``shell=True`` di seluruh proyek. Semua perintah
dijalankan sebagai list argumen melalui :class:`SubprocessRunner`, dan dapat
diganti dengan :class:`FakeRunner` saat pengujian.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, Sequence


@dataclass(frozen=True)
class CommandResult:
    """Hasil eksekusi satu perintah."""

    argv: tuple[str, ...]
    returncode: int
    stdout: str = ""
    stderr: str = ""
    duration: float = 0.0
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        """True bila perintah selesai dengan kode 0."""
        return self.returncode == 0 and not self.timed_out


class Runner(Protocol):
    """Antarmuka runner yang bisa diganti pada tes."""

    def run(
        self,
        argv: Sequence[str],
        *,
        timeout: float = 60.0,
        env: dict[str, str] | None = None,
    ) -> CommandResult:
        """Jalankan argv dan kembalikan hasilnya."""

    def which(self, name: str) -> str | None:
        """Cari executable pada PATH."""


class SubprocessRunner:
    """Runner nyata berbasis :func:`subprocess.run`."""

    def run(
        self,
        argv: Sequence[str],
        *,
        timeout: float = 60.0,
        env: dict[str, str] | None = None,
    ) -> CommandResult:
        """Jalankan ``argv`` tanpa shell dan tangkap stdout/stderr."""
        args = [str(part) for part in argv]
        if not args:
            raise ValueError("argv tidak boleh kosong")
        started = time.monotonic()
        run_env = dict(os.environ)
        if env:
            run_env.update(env)
        try:
            completed = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                shell=False,
                env=run_env,
            )
            return CommandResult(
                argv=tuple(args),
                returncode=completed.returncode,
                stdout=completed.stdout or "",
                stderr=completed.stderr or "",
                duration=time.monotonic() - started,
            )
        except FileNotFoundError:
            return CommandResult(
                argv=tuple(args),
                returncode=127,
                stderr=f"Perintah tidak ditemukan: {args[0]}",
                duration=time.monotonic() - started,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            return CommandResult(
                argv=tuple(args),
                returncode=-1,
                stdout=stdout,
                stderr=stderr or f"Timeout setelah {timeout:.0f} detik",
                duration=time.monotonic() - started,
                timed_out=True,
            )
        except OSError as exc:
            return CommandResult(
                argv=tuple(args),
                returncode=126,
                stderr=str(exc),
                duration=time.monotonic() - started,
            )

    def which(self, name: str) -> str | None:
        """Cari executable pada PATH sistem."""
        return shutil.which(name)


@dataclass
class FakeRunner:
    """Runner palsu untuk tes: merekam argv dan mengembalikan respons tetap."""

    responses: dict[str, CommandResult] = field(default_factory=dict)
    argv_responses: dict[tuple[str, ...], CommandResult] = field(default_factory=dict)
    paths: dict[str, str] = field(default_factory=dict)
    default_returncode: int = 0
    default_stdout: str = ""
    calls: list[tuple[str, ...]] = field(default_factory=list)
    timeouts: list[float] = field(default_factory=list)

    def run(
        self,
        argv: Sequence[str],
        *,
        timeout: float = 60.0,
        env: dict[str, str] | None = None,
    ) -> CommandResult:
        """Catat panggilan dan kembalikan respons yang sudah disiapkan."""
        args = tuple(str(part) for part in argv)
        self.calls.append(args)
        self.timeouts.append(timeout)
        if args in self.argv_responses:
            return self.argv_responses[args]
        if args and args[0] in self.responses:
            return self.responses[args[0]]
        return CommandResult(
            argv=args,
            returncode=self.default_returncode,
            stdout=self.default_stdout,
        )

    def which(self, name: str) -> str | None:
        """Kembalikan path palsu bila sudah didaftarkan."""
        return self.paths.get(name)

    @property
    def last_argv(self) -> tuple[str, ...]:
        """Argumen terakhir yang dijalankan."""
        return self.calls[-1] if self.calls else ()


class PrivilegedError(RuntimeError):
    """Kegagalan umum pada pemanggilan helper lewat pkexec."""

    def __init__(self, message: str, result: CommandResult | None = None) -> None:
        super().__init__(message)
        self.result = result


class AuthCancelled(PrivilegedError):
    """Pengguna menutup dialog autentikasi polkit."""


class AuthDenied(PrivilegedError):
    """Polkit menolak permintaan autentikasi."""


class PrivilegedFailure(PrivilegedError):
    """Helper berjalan tetapi mengembalikan kode gagal."""


def default_helper_path() -> str:
    """Lokasi helper: env override, sibling dev, lalu lokasi instalasi."""
    override = os.environ.get("UBUCLEAN_HELPER")
    if override:
        return override
    sibling = Path(__file__).resolve().parents[2] / "bin" / "ubuclean-helper"
    if sibling.is_file():
        return str(sibling)
    return "/usr/lib/ubuclean/bin/ubuclean-helper"


class PrivilegedRunner:
    """Pemanggil helper root melalui ``pkexec`` dengan whitelist argumen."""

    def __init__(
        self,
        runner: Runner,
        helper_path: str | None = None,
        *,
        dry_run: bool = False,
    ) -> None:
        self.runner = runner
        self.helper_path = helper_path or default_helper_path()
        self.dry_run = dry_run

    def describe(self, args: Sequence[str]) -> tuple[str, ...]:
        """Argumen lengkap yang akan dijalankan (untuk preview/dry-run)."""
        return ("pkexec", self.helper_path, *(str(arg) for arg in args))

    def run(self, args: Sequence[str], *, timeout: float = 300.0) -> CommandResult:
        """Jalankan satu aksi helper, memetakan kode keluar polkit."""
        argv = self.describe(args)
        if self.dry_run:
            return CommandResult(argv=argv, returncode=0, stdout="dry-run")
        result = self.runner.run(argv, timeout=timeout)
        if result.returncode == 126:
            raise AuthCancelled("Autentikasi dibatalkan.", result)
        if result.returncode == 127:
            raise AuthDenied("Otorisasi ditolak oleh polkit.", result)
        if result.timed_out:
            raise PrivilegedFailure("Perintah sistem melewati batas waktu.", result)
        if result.returncode != 0:
            message = result.stderr.strip() or f"Helper gagal (kode {result.returncode})."
            raise PrivilegedFailure(message, result)
        return result


def prune_environment() -> dict[str, str]:
    """Environment minimal untuk helper (menghindari PYTHONPATH asing)."""
    return {
        "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
        "LANG": os.environ.get("LANG", "C.UTF-8"),
    }
