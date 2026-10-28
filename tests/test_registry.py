"""Tes registry kategori."""

from __future__ import annotations

from ubuclean.core import registry
from ubuclean.core.models import Group


def test_category_ids_unique() -> None:
    ids = [category.id for category in registry.CATEGORIES]
    assert len(ids) == len(set(ids))


def test_risky_categories_not_scheduled() -> None:
    for category in registry.CATEGORIES:
        if category.risky:
            assert category.schedule_allowed is False
            assert registry.is_schedule_safe(category.id) is False


def test_schedule_defaults_are_safe() -> None:
    for category_id in registry.scheduled_ids():
        assert registry.is_schedule_safe(category_id)


def test_default_selected_excludes_risky() -> None:
    selected = set(registry.default_selected_ids())
    assert "kernels" not in selected
    assert "browser-cache" not in selected
    assert "user-cache" in selected


def test_groups_present() -> None:
    for group in Group:
        assert registry.categories_for_group(group)
