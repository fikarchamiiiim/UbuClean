"""Real-time system metric sampler and process management."""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Iterable

from ubuclean import constants

CRITICAL_NAMES = {
    "systemd",
    "init",
    "kthreadd",
    "gnome-shell",
    "mutter",
    "kwin_wayland",
    "Xorg",
    "Xwayland",
    "gdm",
    "gdm3",
    "dbus-daemon",
    "dbus-broker",
    "systemd-journald",
    "systemd-logind",
    "systemd-udevd",
    "pipewire",
    "wireplumber",
    "polkitd",
    "ubuclean",
}

_cpu_lock = threading.Lock()
_cpu_samples: dict[int, tuple[float, float]] = {}


@dataclass(frozen=True, slots=True)
class Snapshot:
    """One system metrics sample."""

    ts: float
    cpu_percent: float = 0.0
    cpu_per_core: tuple[float, ...] = ()
    ram_percent: float = 0.0
    ram_used: int = 0
    ram_total: int = 0
    swap_percent: float = 0.0
    swap_used: int = 0
    swap_total: int = 0
    disk_percent: float = 0.0
    disk_used: int = 0
    disk_total: int = 0
    net_sent_total: int = 0
    net_recv_total: int = 0
    net_sent_rate: float = 0.0
    net_recv_rate: float = 0.0
    temperature: float | None = None
    load_1: float = 0.0
    load_5: float = 0.0
    load_15: float = 0.0
    process_count: int = 0

    def as_dict(self) -> dict[str, object]:
        """JSON-able representation."""
        return {
            "ts": self.ts,
            "cpu_percent": round(self.cpu_percent, 1),
            "cpu_per_core": [round(value, 1) for value in self.cpu_per_core],
            "ram_percent": round(self.ram_percent, 1),
            "ram_used": self.ram_used,
            "ram_total": self.ram_total,
            "swap_percent": round(self.swap_percent, 1),
            "swap_used": self.swap_used,
            "swap_total": self.swap_total,
            "disk_percent": round(self.disk_percent, 1),
            "disk_used": self.disk_used,
            "disk_total": self.disk_total,
            "net_sent_rate": round(self.net_sent_rate, 1),
            "net_recv_rate": round(self.net_recv_rate, 1),
            "temperature": self.temperature,
            "load_1": round(self.load_1, 2),
            "process_count": self.process_count,
        }


def _temperature() -> float | None:
    try:
        import psutil

        sensors = psutil.sensors_temperatures()
    except (ImportError, OSError, AttributeError):
        return None
    if not sensors:
        return None
    preferred = ("coretemp", "k10temp", "zenpower", "acpitz", "cpu_thermal")
    for key in preferred:
        for entry in sensors.get(key, []):
            if entry.current and entry.current > 0:
                return float(entry.current)
    for entries in sensors.values():
        for entry in entries:
            if entry.current and entry.current > 0:
                return float(entry.current)
    return None


def collect_snapshot(disk_path: str | Path = "/") -> Snapshot:
    """Read system metrics using psutil (called from a worker thread)."""
    import psutil

    memory = psutil.virtual_memory()
    swap = psutil.swap_memory()
    disk = psutil.disk_usage(str(disk_path))
    net = psutil.net_io_counters()
    try:
        load_1, load_5, load_15 = psutil.getloadavg()
    except (AttributeError, OSError):
        load_1 = load_5 = load_15 = 0.0
    try:
        process_count = len(psutil.pids())
    except (psutil.Error, OSError):
        process_count = 0
    return Snapshot(
        ts=time.time(),
        cpu_percent=float(psutil.cpu_percent(interval=None)),
        cpu_per_core=tuple(float(value) for value in psutil.cpu_percent(interval=None, percpu=True)),
        ram_percent=float(memory.percent),
        ram_used=int(memory.used),
        ram_total=int(memory.total),
        swap_percent=float(swap.percent),
        swap_used=int(swap.used),
        swap_total=int(swap.total),
        disk_percent=float(disk.percent),
        disk_used=int(disk.used),
        disk_total=int(disk.total),
        net_sent_total=int(net.bytes_sent),
        net_recv_total=int(net.bytes_recv),
        temperature=_temperature(),
        load_1=float(load_1),
        load_5=float(load_5),
        load_15=float(load_15),
        process_count=process_count,
    )


class MonitorSampler:
    """Keeps a short snapshot history and computes network rates."""

    def __init__(
        self,
        *,
        interval: float = constants.MONITOR_INTERVAL_SECONDS,
        history_seconds: int = constants.HISTORY_SECONDS,
        disk_path: str | Path = "/",
        provider: Callable[[], Snapshot] | None = None,
    ) -> None:
        self.interval = max(0.2, float(interval))
        self.maxlen = max(2, int(history_seconds / self.interval))
        self.disk_path = disk_path
        self._provider = provider or (lambda: collect_snapshot(self.disk_path))
        self._history: list[Snapshot] = []
        self._lock = threading.Lock()

    def sample(self) -> Snapshot:
        """Take a new snapshot, fill in the network rates, and add it to the history."""
        snapshot = self._provider()
        with self._lock:
            last = self._history[-1] if self._history else None
            if last is not None:
                elapsed = max(0.001, snapshot.ts - last.ts)
                snapshot = replace(
                    snapshot,
                    net_sent_rate=max(0.0, (snapshot.net_sent_total - last.net_sent_total) / elapsed),
                    net_recv_rate=max(0.0, (snapshot.net_recv_total - last.net_recv_total) / elapsed),
                )
            self._history.append(snapshot)
            if len(self._history) > self.maxlen:
                del self._history[: len(self._history) - self.maxlen]
        return snapshot

    @property
    def history(self) -> tuple[Snapshot, ...]:
        """Copy of the snapshot history, oldest → newest."""
        with self._lock:
            return tuple(self._history)

    @property
    def latest(self) -> Snapshot | None:
        """The most recent snapshot, if any."""
        with self._lock:
            return self._history[-1] if self._history else None

    def reset(self) -> None:
        """Clear the history."""
        with self._lock:
            self._history.clear()


@dataclass(frozen=True, slots=True)
class ProcessInfo:
    """Summary of one process for the monitor list."""

    pid: int
    name: str
    username: str
    cpu_percent: float
    memory_percent: float
    rss: int
    command: str
    status: str
    protected_reason: str | None = None

    def as_dict(self) -> dict[str, object]:
        """JSON-able representation."""
        return {
            "pid": self.pid,
            "name": self.name,
            "username": self.username,
            "cpu_percent": round(self.cpu_percent, 1),
            "memory_percent": round(self.memory_percent, 1),
            "rss": self.rss,
            "command": self.command,
            "status": self.status,
            "protected_reason": self.protected_reason,
        }


def critical_reason(
    pid: int,
    name: str,
    username: str,
    uid: int,
    *,
    current_uid: int,
    current_pid: int | None = None,
) -> str | None:
    """Reason the process must not be terminated, or ``None`` when it is safe."""
    if pid <= 1:
        return "System init process"
    if current_pid is not None and pid == current_pid:
        return "UbuClean's own process"
    if name in CRITICAL_NAMES:
        return "Core desktop session process"
    if uid == 0 and current_uid != 0:
        return "Root-owned system process"
    if uid != current_uid:
        return "Owned by another user"
    return None


def _cpu_percent_for(process, now: float) -> float:
    """Compute CPU% from the cpu_times difference so it stays accurate between refreshes."""
    try:
        times = process.cpu_times()
        total = float(times.user + times.system)
    except Exception:
        return 0.0
    pid = process.pid
    with _cpu_lock:
        previous = _cpu_samples.get(pid)
        _cpu_samples[pid] = (now, total)
    if previous is None:
        return 0.0
    previous_time, previous_total = previous
    elapsed = max(0.001, now - previous_time)
    cores = os.cpu_count() or 1
    percent = (total - previous_total) / (elapsed * cores) * 100.0
    return max(0.0, min(100.0, percent))


def list_processes(
    *,
    limit: int = constants.TOP_PROCESS_LIMIT,
    sort_by: str = "cpu",
    current_uid: int | None = None,
    current_pid: int | None = None,
) -> list[ProcessInfo]:
    """Fetch the top processes by CPU or RAM."""
    import psutil

    uid = os.getuid() if current_uid is None else current_uid
    own_pid = os.getpid() if current_pid is None else current_pid
    now = time.monotonic()
    alive: set[int] = set()
    result: list[ProcessInfo] = []
    for process in psutil.process_iter(
        ["pid", "name", "username", "memory_info", "memory_percent", "status", "cmdline"]
    ):
        try:
            info = process.info
            pid = int(info["pid"])
            alive.add(pid)
            name = info.get("name") or f"pid {pid}"
            username = info.get("username") or "?"
            try:
                process_uid = process.uids().real
            except (psutil.Error, AttributeError):
                process_uid = -1
            memory_info = info.get("memory_info")
            rss = int(memory_info.rss) if memory_info is not None else 0
            command = " ".join(info.get("cmdline") or [])[:300]
            result.append(
                ProcessInfo(
                    pid=pid,
                    name=name,
                    username=username,
                    cpu_percent=_cpu_percent_for(process, now),
                    memory_percent=float(info.get("memory_percent") or 0.0),
                    rss=rss,
                    command=command,
                    status=str(info.get("status") or ""),
                    protected_reason=critical_reason(
                        pid,
                        name,
                        username,
                        process_uid,
                        current_uid=uid,
                        current_pid=own_pid,
                    ),
                )
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
            continue
    with _cpu_lock:
        for pid in list(_cpu_samples):
            if pid not in alive:
                _cpu_samples.pop(pid, None)
    return sort_processes(result, sort_by=sort_by, limit=limit)


def sort_processes(
    processes: Iterable[ProcessInfo],
    *,
    sort_by: str = "cpu",
    limit: int = constants.TOP_PROCESS_LIMIT,
) -> list[ProcessInfo]:
    """Sort processes and limit their number (a pure function used by tests)."""
    key = (lambda item: item.memory_percent) if sort_by == "memory" else (lambda item: item.cpu_percent)
    ordered = sorted(processes, key=key, reverse=True)
    return ordered[: max(1, limit)]


def terminate_process(pid: int, *, current_uid: int | None = None) -> tuple[bool, str]:
    """Terminate a single process after re-validating it; never for core processes."""
    import psutil

    uid = os.getuid() if current_uid is None else current_uid
    try:
        process = psutil.Process(pid)
        name = process.name()
        username = process.username()
        process_uid = process.uids().real
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError) as exc:
        return False, f"Process cannot be accessed: {exc}"
    reason = critical_reason(
        pid,
        name,
        username,
        process_uid,
        current_uid=uid,
        current_pid=os.getpid(),
    )
    if reason:
        return False, reason
    try:
        process.terminate()
        try:
            process.wait(timeout=3)
        except psutil.TimeoutExpired:
            process.kill()
        return True, f"Process {name} ({pid}) terminated."
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError) as exc:
        return False, f"Failed to terminate the process: {exc}"
