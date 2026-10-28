"""Perhitungan Skor Kesehatan Sistem dan rekomendasi."""

from __future__ import annotations

from dataclasses import dataclass, field

from ubuclean.core.models import Preview
from ubuclean.core.util import clamp, format_bytes


@dataclass(frozen=True, slots=True)
class HealthFactor:
    """Kontribusi satu aspek terhadap skor kesehatan."""

    key: str
    label: str
    score: float
    maximum: float
    detail: str

    @property
    def lost(self) -> float:
        """Poin yang hilang dari aspek ini."""
        return max(0.0, self.maximum - self.score)


@dataclass(frozen=True, slots=True)
class Recommendation:
    """Saran tindakan yang relevan dengan kondisi saat ini."""

    title: str
    detail: str
    severity: str = "info"
    category_ids: tuple[str, ...] = ()


@dataclass(slots=True)
class HealthReport:
    """Hasil penilaian kesehatan sistem."""

    score: int
    factors: list[HealthFactor] = field(default_factory=list)
    recommendations: list[Recommendation] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        """Label singkat untuk skor."""
        if self.score >= 85:
            return "Sangat baik"
        if self.score >= 70:
            return "Baik"
        if self.score >= 50:
            return "Waspada"
        return "Perlu perhatian"

    @property
    def severity(self) -> str:
        """Kategori warna: success/warning/error."""
        if self.score >= 70:
            return "success"
        if self.score >= 45:
            return "warning"
        return "error"

    def deductions(self) -> list[HealthFactor]:
        """Aspek yang menurunkan skor, terbesar lebih dulu."""
        return sorted(
            [factor for factor in self.factors if factor.lost >= 1],
            key=lambda factor: factor.lost,
            reverse=True,
        )

    def as_dict(self) -> dict[str, object]:
        """Representasi JSON-able."""
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
    """Hitung skor 0–100 dari beberapa metrik yang sudah dikumpulkan."""
    factors: list[HealthFactor] = []

    disk_score = 25.0 * clamp((95.0 - float(disk_percent)) / 25.0, 0.0, 1.0)
    factors.append(
        HealthFactor(
            "disk",
            "Ruang disk",
            disk_score,
            25.0,
            f"Disk terpakai {disk_percent:.0f}%.",
        )
    )

    ram_score = 20.0 * clamp((95.0 - float(ram_percent)) / 20.0, 0.0, 1.0)
    factors.append(
        HealthFactor("ram", "Penggunaan RAM", ram_score, 20.0, f"RAM terpakai {ram_percent:.0f}%.")
    )

    swap_score = 10.0 * (1.0 - clamp(float(swap_percent) / 40.0, 0.0, 1.0))
    factors.append(
        HealthFactor("swap", "Swap", swap_score, 10.0, f"Swap terpakai {swap_percent:.0f}%.")
    )

    trash_score = 10.0 * (1.0 - clamp(trash_bytes / (2 * 1024**3), 0.0, 1.0))
    factors.append(
        HealthFactor(
            "trash",
            "Tong sampah",
            trash_score,
            10.0,
            "Tong sampah kosong." if trash_bytes <= 0 else f"Tong sampah berisi {format_bytes(trash_bytes)}.",
        )
    )

    startup_score = 10.0 * (1.0 - clamp((startup_count - 5) / 15.0, 0.0, 1.0))
    factors.append(
        HealthFactor(
            "startup",
            "Beban startup",
            startup_score,
            10.0,
            f"{startup_count} aplikasi/layanan autostart.",
        )
    )

    cache_score = 15.0 * (1.0 - clamp(cache_bytes / (5 * 1024**3), 0.0, 1.0))
    factors.append(
        HealthFactor(
            "cache",
            "Sampah terdeteksi",
            cache_score,
            15.0,
            "Tidak ada sampah besar terdeteksi."
            if cache_bytes <= 0
            else f"{format_bytes(cache_bytes)} sampah bisa dibebaskan.",
        )
    )

    kernel_score = 10.0 if old_kernels <= 0 else max(0.0, 10.0 - min(10.0, old_kernels * 3.0))
    factors.append(
        HealthFactor(
            "kernels",
            "Kernel lama",
            kernel_score,
            10.0,
            "Tidak ada kernel lama." if old_kernels <= 0 else f"{old_kernels} paket kernel lama.",
        )
    )

    total = int(round(sum(factor.score for factor in factors)))
    report = HealthReport(score=max(0, min(100, total)), factors=factors)

    if disk_percent >= 85:
        detail = (
            f"{format_bytes(cache_bytes)} cache dan sampah bisa dibebaskan."
            if cache_bytes > 0
            else "Buka pembersih untuk memindai sampah dan cache."
        )
        report.recommendations.append(
            Recommendation(
                title=f"Disk {disk_percent:.0f}% penuh",
                detail=detail,
                severity="critical" if disk_percent >= 92 else "warning",
                category_ids=("user-cache", "apt-cache", "journal", "trash"),
            )
        )
    if trash_bytes >= 512 * 1024**2:
        report.recommendations.append(
            Recommendation(
                title="Tong sampah besar",
                detail=f"{format_bytes(trash_bytes)} item menunggu dikosongkan.",
                severity="warning",
                category_ids=("trash",),
            )
        )
    if old_kernels > 0:
        report.recommendations.append(
            Recommendation(
                title="Kernel lama terpasang",
                detail=f"{old_kernels} paket kernel lama bisa dihapus dengan aman.",
                severity="info",
                category_ids=("kernels",),
            )
        )
    if ram_percent >= 90:
        report.recommendations.append(
            Recommendation(
                title="RAM hampir penuh",
                detail="Tutup aplikasi yang tidak dipakai atau buang cache yang terbentuk lagi.",
                severity="warning",
                category_ids=(),
            )
        )
    if startup_count >= 15:
        report.recommendations.append(
            Recommendation(
                title="Startup terlalu ramai",
                detail=f"{startup_count} aplikasi berjalan saat login.",
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
    """Bangun laporan kesehatan dari hasil pindai dan metrik live."""
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
