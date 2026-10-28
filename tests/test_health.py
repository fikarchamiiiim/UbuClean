"""Tes skor kesehatan sistem dan rekomendasi."""

from __future__ import annotations

from ubuclean.core.health import compute_health
from ubuclean.core.models import Preview


def test_healthy_system_scores_high() -> None:
    report = compute_health(disk_percent=30, ram_percent=20, swap_percent=0)
    assert report.score >= 98
    assert report.verdict == "Excellent"
    assert report.severity == "success"
    assert report.recommendations == []


def test_full_disk_lowers_score() -> None:
    report = compute_health(disk_percent=95, ram_percent=20, swap_percent=0)
    assert report.score < 80
    disk = next(factor for factor in report.factors if factor.key == "disk")
    assert disk.score == 0
    assert any("Disk" in item.title for item in report.recommendations)


def test_score_bounded() -> None:
    report = compute_health(
        disk_percent=100,
        ram_percent=100,
        swap_percent=100,
        trash_bytes=10 * 1024**3,
        startup_count=50,
        cache_bytes=20 * 1024**3,
        old_kernels=10,
    )
    assert report.score == 0
    assert report.verdict == "Needs attention"
    assert report.severity == "error"
    assert len(report.recommendations) >= 3


def test_deductions_sorted() -> None:
    report = compute_health(
        disk_percent=95,
        ram_percent=90,
        swap_percent=0,
        trash_bytes=1024**3,
        startup_count=10,
        cache_bytes=2 * 1024**3,
        old_kernels=2,
    )
    lost = [factor.lost for factor in report.deductions()]
    assert lost == sorted(lost, reverse=True)
    assert all(factor.lost >= 1 for factor in report.deductions())


def test_old_kernels_recommendation() -> None:
    report = compute_health(disk_percent=40, ram_percent=30, swap_percent=0, old_kernels=3)
    assert any("Old kernels" in item.title for item in report.recommendations)
    assert report.as_dict()["score"] == report.score


def test_scan_health_aggregates_cache() -> None:
    from ubuclean.core.health import scan_health

    previews = {
        "user-cache": Preview(category_id="user-cache", estimated_bytes=1024**3),
        "trash": Preview(category_id="trash", estimated_bytes=1024**3),
    }
    report = scan_health(previews, disk_percent=50, ram_percent=50, swap_percent=0)
    cache = next(factor for factor in report.factors if factor.key == "cache")
    assert cache.detail.startswith("1.0 GB")
