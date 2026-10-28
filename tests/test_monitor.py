"""Tes sampler monitor, pengurutan proses, dan perlindungan proses kritis."""

from __future__ import annotations

import os

from ubuclean.core.monitor import (
    CRITICAL_NAMES,
    MonitorSampler,
    ProcessInfo,
    Snapshot,
    critical_reason,
    sort_processes,
)


def snapshot(ts: float, *, sent: int = 0, recv: int = 0, cpu: float = 10.0) -> Snapshot:
    return Snapshot(
        ts=ts,
        cpu_percent=cpu,
        ram_percent=40.0,
        disk_percent=50.0,
        net_sent_total=sent,
        net_recv_total=recv,
    )


def test_sampler_computes_network_rates() -> None:
    samples = iter([snapshot(100.0, sent=1000, recv=2000), snapshot(102.0, sent=5000, recv=6000)])
    sampler = MonitorSampler(interval=1.0, history_seconds=5, provider=lambda: next(samples))
    first = sampler.sample()
    second = sampler.sample()
    assert first.net_sent_rate == 0.0
    assert second.net_sent_rate == 2000.0
    assert second.net_recv_rate == 2000.0
    assert len(sampler.history) == 2
    assert sampler.latest == second


def test_sampler_history_limit() -> None:
    counter = {"value": 0}

    def provider() -> Snapshot:
        counter["value"] += 1
        return snapshot(float(counter["value"]))

    sampler = MonitorSampler(interval=1.0, history_seconds=3, provider=provider)
    for _ in range(6):
        sampler.sample()
    assert len(sampler.history) == 3
    sampler.reset()
    assert sampler.history == ()
    assert sampler.latest is None


def process(pid: int, *, name: str = "app", cpu: float = 1.0, ram: float = 1.0) -> ProcessInfo:
    return ProcessInfo(
        pid=pid,
        name=name,
        username="engineer",
        cpu_percent=cpu,
        memory_percent=ram,
        rss=1000,
        command=name,
        status="running",
    )


def test_sort_processes_by_cpu_and_memory() -> None:
    processes = [process(1, cpu=5, ram=20), process(2, cpu=50, ram=2), process(3, cpu=10, ram=30)]
    assert [item.pid for item in sort_processes(processes, sort_by="cpu")] == [2, 3, 1]
    assert [item.pid for item in sort_processes(processes, sort_by="memory")] == [3, 1, 2]
    assert len(sort_processes(processes, limit=1)) == 1


def test_critical_reason_blocks_init_and_self() -> None:
    assert critical_reason(1, "systemd", "root", 0, current_uid=1000, current_pid=100) == "Proses init sistem"
    assert critical_reason(100, "ubuclean", "engineer", 1000, current_uid=1000, current_pid=100)


def test_critical_reason_blocks_desktop_names() -> None:
    for name in CRITICAL_NAMES:
        reason = critical_reason(500, name, "engineer", 1000, current_uid=1000, current_pid=1)
        assert reason is not None


def test_critical_reason_blocks_other_users() -> None:
    assert critical_reason(500, "app", "root", 0, current_uid=1000, current_pid=1)
    assert critical_reason(500, "app", "budi", 1001, current_uid=1000, current_pid=1)


def test_critical_reason_allows_own_process() -> None:
    assert critical_reason(500, "firefox", "engineer", os.getuid(), current_uid=os.getuid(), current_pid=1) is None


def test_snapshot_as_dict() -> None:
    payload = snapshot(1.0, cpu=12.34).as_dict()
    assert payload["cpu_percent"] == 12.3
    assert payload["ram_percent"] == 40.0
