"""System Health Score computation and recommendations."""

from __future__ import annotations

from dataclasses import dataclass, field

from ubuclean.core.models import Preview
from ubuclean.core.util import clamp, format_bytes


@dataclass(frozen=True, slots=True)
class HealthFactor:
    """Contribution of one aspect to the health score."""

    key: str
    label: str
    score: float
    maximum: float
    detail: str

    @property
    def lost(self) -> float:
        """Points lost on this aspect."""
        return max(0.0, self.maximum - self.score)


@dataclass(frozen=True, slots=True)
class Recommendation:
    """Suggested action relevant to the current conditions."""

    title: str
    detail: str
    severity: str = "info"
    category_ids: tuple[str, ...] = ()


@dataclass(slots=True)
class HealthReport:
    """Result of the system health assessment."""

    score: int
    factors: list[HealthFactor] = field(default_factory=list)
    recommendations: list[Recommendation] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        """Short label for the score."""
        if self.score >= 85:
            return "Excellent"
        if self.score >= 70:
            return "Good"
        if self.score >= 50:
            return "Caution"
        return "Needs attention"

    @property
    def severity(self) -> str:
        """Colour category: success/warning/error."""
        if self.score >= 70:
            return "success"
        if self.score >= 45:
            return "warning"
        return "error"

    def deductions(self) -> list[HealthFactor]:
        """Aspects that lower the score, largest first."""
        return sorted(
            [factor for factor in self.factors if factor.lost >= 1],
            key=lambda factor: factor.lost,
            reverse=True,
        )

    def as_dict(self) -> dict[str, object]:
        """JSON-able representation."""
        return {
            "score": self.score,
            "verdict": self.verdict,
            "factors": [
                {
                    "key": factor.key,
                    "label": factor.label,
                    "score": round(factor.score, 1),
                    "maximum": factor.maximum,
                    "detail": factor.detail,
                }
                for factor in self.factors
            ],
            "recommendations": [
                {
                    "title": item.title,
                    "detail": item.detail,
                    "severity": item.severity,
                    "category_ids": list(item.category_ids),
                }
                for item in self.recommendations
            ],
        }


def compute_health(
    *,
    disk_percent: float,
    ram_percent: float,
    swap_percent: float,
    trash_bytes: int = 0,
    startup_count: int = 0,
    cache_bytes: int = 0,
    old_kernels: int = 0,
) -> HealthReport:
    """Compute a 0–100 score from the metrics that have already been collected."""
    factors: list[HealthFactor] = []

    disk_score = 25.0 * clamp((95.0 - float(disk_percent)) / 25.0, 0.0, 1.0)
    factors.append(
        HealthFactor(
            "disk",
            "Disk space",
            disk_score,
            25.0,
            f"Disk {disk_percent:.0f}% used.",
        )
    )

    ram_score = 20.0 * clamp((95.0 - float(ram_percent)) / 20.0, 0.0, 1.0)
    factors.append(
        HealthFactor("ram", "RAM usage", ram_score, 20.0, f"RAM {ram_percent:.0f}% used.")
    )

    swap_score = 10.0 * (1.0 - clamp(float(swap_percent) / 40.0, 0.0, 1.0))
    factors.append(
        HealthFactor("swap", "Swap", swap_score, 10.0, f"Swap {swap_percent:.0f}% used.")
    )

    trash_score = 10.0 * (1.0 - clamp(trash_bytes / (2 * 1024**3), 0.0, 1.0))
    factors.append(
        HealthFactor(
            "trash",
            "Trash",
            trash_score,
            10.0,
            "Trash is empty." if trash_bytes <= 0 else f"Trash holds {format_bytes(trash_bytes)}.",
        )
    )

    startup_score = 10.0 * (1.0 - clamp((startup_count - 5) / 15.0, 0.0, 1.0))
    factors.append(
        HealthFactor(
            "startup",
            "Startup load",
            startup_score,
            10.0,
            f"{startup_count} autostart applications/services.",
        )
    )

    cache_score = 15.0 * (1.0 - clamp(cache_bytes / (5 * 1024**3), 0.0, 1.0))
    factors.append(
        HealthFactor(
            "cache",
            "Detected junk",
            cache_score,
            15.0,
            "No large junk detected."
            if cache_bytes <= 0
            else f"{format_bytes(cache_bytes)} of junk can be freed.",
        )
    )

    kernel_score = 10.0 if old_kernels <= 0 else max(0.0, 10.0 - min(10.0, old_kernels * 3.0))
    factors.append(
        HealthFactor(
            "kernels",
            "Old kernels",
            kernel_score,
            10.0,
            "No old kernels." if old_kernels <= 0 else f"{old_kernels} old kernel packages.",
        )
    )

    total = int(round(sum(factor.score for factor in factors)))
    report = HealthReport(score=max(0, min(100, total)), factors=factors)

    if disk_percent >= 85:
        detail = (
            f"{format_bytes(cache_bytes)} of cache and junk can be freed."
            if cache_bytes > 0
            else "Open the cleaner to scan for junk and cache."
        )
        report.recommendations.append(
            Recommendation(
                title=f"Disk {disk_percent:.0f}% full",
                detail=detail,
                severity="critical" if disk_percent >= 92 else "warning",
                category_ids=("user-cache", "apt-cache", "journal", "trash"),
            )
        )
    if trash_bytes >= 512 * 1024**2:
        report.recommendations.append(
            Recommendation(
                title="Trash is large",
                detail=f"{format_bytes(trash_bytes)} of items are waiting to be emptied.",
                severity="warning",
                category_ids=("trash",),
            )
        )
    if old_kernels > 0:
        report.recommendations.append(
            Recommendation(
                title="Old kernels installed",
                detail=f"{old_kernels} old kernel packages can be removed safely.",
                severity="info",
                category_ids=("kernels",),
            )
        )
    if ram_percent >= 90:
        report.recommendations.append(
            Recommendation(
                title="RAM is almost full",
                detail="Close applications you are not using or drop the cache that has built up again.",
                severity="warning",
                category_ids=(),
            )
        )
    if startup_count >= 15:
        report.recommendations.append(
            Recommendation(
                title="Startup is too crowded",
                detail=f"{startup_count} applications run at login.",
                severity="info",
                category_ids=(),
            )
        )
    return report


def scan_health(
    previews: dict[str, Preview],
    *,
    disk_percent: float,
    ram_percent: float,
    swap_percent: float,
    trash_bytes: int = 0,
    startup_count: int = 0,
    old_kernels: int = 0,
) -> HealthReport:
    """Build the health report from scan results and live metrics."""
    cache_bytes = sum(
        preview.total_bytes
        for category_id, preview in previews.items()
        if category_id not in {"trash", "kernels"}
    )
    return compute_health(
        disk_percent=disk_percent,
        ram_percent=ram_percent,
        swap_percent=swap_percent,
        trash_bytes=trash_bytes,
        startup_count=startup_count,
        cache_bytes=cache_bytes,
        old_kernels=old_kernels,
    )
