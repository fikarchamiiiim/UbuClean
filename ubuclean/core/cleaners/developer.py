"""Cleaner objek Docker (dangling image, container berhenti, volume, build cache)."""

from __future__ import annotations

import json

from ubuclean.core.cleaners.base import BaseCleaner
from ubuclean.core.context import Context
from ubuclean.core.models import CleanOutcome, Preview, PreviewItem
from ubuclean.core.registry import get_category
from ubuclean.core.util import parse_human_size

TARGET_BY_TYPE = {
    "Images": "image",
    "Containers": "container",
    "Local Volumes": "volume",
    "Build Cache": "builder",
}
LABELS = {
    "image": "Image dangling",
    "container": "Container berhenti",
    "volume": "Volume tak terpakai",
    "builder": "Build cache",
}


class DockerCleaner(BaseCleaner):
    """Prune per jenis objek, tanpa ``docker system prune -a``."""

    category = get_category("dev-docker")

    def scan(self, ctx: Context) -> Preview:
        """Baca ringkasan docker system df dan tawarkan prune per jenis."""
        if ctx.runner.which("docker") is None:
            return self.new_preview(
                supported=False,
                notes=["Docker tidak terpasang di sistem ini."],
            )
        info = ctx.runner.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"], timeout=15
        )
        if info.returncode != 0:
            return self.new_preview(
                supported=False,
                notes=[
                    "Docker tidak bisa diakses tanpa hak tambahan.",
                    "Tambahkan pengguna ke grup docker atau jalankan pembersihan lewat autentikasi admin.",
                ],
            )
        result = ctx.runner.run(
            ["docker", "system", "df", "--format", "{{json .}}"], timeout=60
        )
        if result.returncode != 0:
            return self.new_preview(
                supported=False,
                errors=[result.stderr.strip() or "docker system df gagal."],
            )
        items: list[PreviewItem] = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except ValueError:
                continue
            if not isinstance(data, dict):
                continue
            target = TARGET_BY_TYPE.get(str(data.get("Type", "")))
            if target is None:
                continue
            reclaimable = parse_human_size(str(data.get("Reclaimable", "")))
            if reclaimable <= 0:
                continue
            total = data.get("TotalCount", "0")
            active = data.get("Active", "0")
            items.append(
                PreviewItem(
                    id=target,
                    label=LABELS[target],
                    size=reclaimable,
                    detail=f"{total} total · {active} aktif",
                )
            )
        items.sort(key=lambda item: item.size, reverse=True)
        notes = [
            "Setiap jenis dipangkas terpisah; tidak menggunakan docker system prune -a.",
            "Image yang masih dipakai container aktif tidak dihapus.",
            "Dihapus langsung oleh Docker, bukan karantina.",
        ]
        if not items:
            notes.append("Tidak ada objek Docker yang bisa dibebaskan.")
        commands = [
            ctx.privileged.describe(["docker-prune", item.id]) for item in items
        ]
        return self.new_preview(items=items, notes=notes, commands=commands)

    def clean(self, ctx: Context, preview: Preview) -> CleanOutcome:
        """Prune setiap jenis objek yang ada di preview."""
        targets = [item.id for item in preview.items]
        if not targets:
            return CleanOutcome(category_id=self.category.id, detail="Tidak ada objek Docker.")
        if ctx.dry_run:
            return CleanOutcome(
                category_id=self.category.id,
                freed_bytes=preview.total_bytes,
                removed=len(targets),
                detail="Pratinjau: docker prune tidak dijalankan.",
            )
        outcome = CleanOutcome(category_id=self.category.id)
        for target in targets:
            outcome.merge(self.helper_clean(ctx, ["docker-prune", target], timeout=900))
        return outcome
