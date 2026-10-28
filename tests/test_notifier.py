"""Tes notifikasi pintar (ambang, cooldown, sustained, mute)."""

from __future__ import annotations

import json
from pathlib import Path

from ubuclean.core.context import Context
from ubuclean.core.notifier import Notifier
from ubuclean.core.runner import FakeRunner


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_notifier(context: Context, clock: Clock) -> Notifier:
    context.config.update(
        {
            "notifications": {
                "enabled": True,
                "muted": False,
                "disk_threshold": 90,
                "ram_threshold": 90,
                "ram_sustained_minutes": 5,
                "cooldown_hours": 6,
            }
        }
    )
    return Notifier(context, clock=clock, state_file=context.paths.state_dir / "notifier.json")


def test_disk_threshold_and_cooldown(context: Context) -> None:
    clock = Clock()
    notifier = make_notifier(context, clock)
    assert notifier.evaluate(89.9, 10) is None
    decision = notifier.evaluate(95, 10)
    assert decision is not None
    assert decision.kind == "disk"
    assert notifier.evaluate(95, 10) is None
    clock.advance(6 * 3600 + 1)
    assert notifier.evaluate(95, 10) is not None


def test_ram_must_be_sustained(context: Context) -> None:
    clock = Clock()
    notifier = make_notifier(context, clock)
    assert notifier.evaluate(50, 95) is None
    clock.advance(2 * 60)
    assert notifier.evaluate(50, 95) is None
    clock.advance(4 * 60)
    decision = notifier.evaluate(50, 95)
    assert decision is not None
    assert decision.kind == "ram"


def test_ram_resets_when_below_threshold(context: Context) -> None:
    clock = Clock()
    notifier = make_notifier(context, clock)
    assert notifier.evaluate(50, 95) is None
    clock.advance(240)
    assert notifier.evaluate(50, 80) is None
    clock.advance(10)
    assert notifier.evaluate(50, 95) is None
    clock.advance(299)
    assert notifier.evaluate(50, 95) is None
    clock.advance(2)
    assert notifier.evaluate(50, 95) is not None


def test_muted_and_disabled(context: Context) -> None:
    clock = Clock()
    notifier = make_notifier(context, clock)
    context.config.update({"notifications": {"muted": True}})
    assert notifier.evaluate(99, 99) is None
    context.config.update({"notifications": {"muted": False, "enabled": False}})
    assert notifier.evaluate(99, 99) is None


def test_state_persists_cooldown(context: Context) -> None:
    clock = Clock()
    notifier = make_notifier(context, clock)
    assert notifier.evaluate(95, 10) is not None
    state = json.loads((context.paths.state_dir / "notifier.json").read_text(encoding="utf-8"))
    assert state["last_notify"]["disk"] == clock.now
    fresh = Notifier(context, clock=clock, state_file=context.paths.state_dir / "notifier.json")
    assert fresh.evaluate(95, 10) is None


def test_notify_uses_actions(context: Context, fake_runner: FakeRunner) -> None:
    clock = Clock()
    notifier = make_notifier(context, clock)
    fake_runner.paths["notify-send"] = "/usr/bin/notify-send"
    decision = notifier.evaluate(96, 10)
    assert decision is not None
    assert notifier.notify(decision) is True
    call = fake_runner.calls[-1]
    assert call[0] == "notify-send"
    assert any(part.startswith("--action=clean=") for part in call)
    assert "Disk" in " ".join(call)


def test_notify_without_notify_send(context: Context) -> None:
    clock = Clock()
    notifier = make_notifier(context, clock)
    decision = notifier.evaluate(96, 10)
    assert decision is not None
    assert notifier.notify(decision) is False
