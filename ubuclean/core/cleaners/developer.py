"""Docker object cleaner (dangling images, stopped containers, volumes, build cache)."""

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
    "image": "Dangling image",
    "container": "Stopped containers",
    "volume": "Unused volumes",
    "builder": "Build cache",
}


class DockerCleaner(BaseCleaner):
    """Prune per object type, without ``docker system prune -a``."""

    category = get_category("dev-docker")

    def scan(self, ctx: Context) -> Preview:
        """Read the docker system df summary and offer a prune per object type."""
        if ctx.runner.which("docker") is None:
            return self.new_preview(
                supported=False,
                notes=["Docker is not installed on this system."],
            )
        info = ctx.runner.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"], timeout=15
        )
        if info.returncode != 0:
            return self.new_preview(
                supported=False,
                notes=[
                    "Docker cannot be accessed without extra privileges.",
                    "Add the user to the docker group or run the cleanup through admin authentication.",
                ],
            )
        result = ctx.runner.run(
            ["docker", "system", "df", "--format", "{{json .}}"], timeout=60
        )
        if result.returncode != 0:
            return self.new_preview(
                supported=False,
                errors=[result.stderr.strip() or "docker system df failed."],
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
                    detail=f"{total} total · {active} active",
                )
            )
        items.sort(key=lambda item: item.size, reverse=True)
        notes = [
            "Each type is pruned separately; docker system prune -a is not used.",
            "Images still used by active containers are not deleted.",
            "Deleted directly by Docker, not quarantined.",
        ]
        if not items:
            notes.append("No Docker objects can be freed.")
        commands = [
            ctx.privileged.describe(["docker-prune", item.id]) for item in items
        ]
        return self.new_preview(items=items, notes=notes, commands=commands)

    def clean(self, ctx: Context, preview: Preview) -> CleanOutcome:
        """Prune each object type present in the preview."""
        targets = [item.id for item in preview.items]
        if not targets:
            return CleanOutcome(category_id=self.category.id, detail="No Docker objects.")
        if ctx.dry_run:
            return CleanOutcome(
                category_id=self.category.id,
                freed_bytes=preview.total_bytes,
                removed=len(targets),
                detail="Preview: docker prune was not run.",
            )
        outcome = CleanOutcome(category_id=self.category.id)
        for target in targets:
            outcome.merge(self.helper_clean(ctx, ["docker-prune", target], timeout=900))
        return outcome
