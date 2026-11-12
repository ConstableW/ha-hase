"""Canonical HASE spellbook discovery; user books override bundled IDs."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from .core.spellbook_engine import EngineSpellbook, load_spellbook_from_path

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
_LOGGER = logging.getLogger(__name__)

BUILTIN_BOOK_DIR = Path(__file__).parent / "spellbooks"
USER_BOOK_SUBDIR = "hase_spellbooks"


def _book_dirs(hass: HomeAssistant) -> list[Path]:
    return [BUILTIN_BOOK_DIR, Path(hass.config.path(USER_BOOK_SUBDIR))]


def _list_book_files(hass: HomeAssistant) -> dict[str, Path]:
    files: dict[str, Path] = {}
    for directory in _book_dirs(hass):
        try:
            if not directory.is_dir():
                continue
            paths = sorted(directory.glob("*.yaml")) + sorted(directory.glob("*.yml"))
        except OSError as err:
            _LOGGER.warning("spellbooker: could not scan %s: %s", directory, err)
            continue
        for path in paths:
            book = load_spellbook_from_path(path)
            if book is not None:
                files[book.id] = path
    return files


async def async_discover_spellbooks(hass: HomeAssistant) -> list[tuple[str, str]]:
    def _discover() -> list[tuple[str, str]]:
        results: list[tuple[str, str]] = []
        for book_id, path in _list_book_files(hass).items():
            book = load_spellbook_from_path(path)
            if book is not None and not book.disabled:
                results.append((book_id, book.name))
        results.sort(key=lambda item: item[1].lower())
        return results

    return await hass.async_add_executor_job(_discover)


async def async_get_spellbook(hass: HomeAssistant, book_id: str) -> EngineSpellbook | None:
    if not book_id:
        return None

    def _load() -> EngineSpellbook | None:
        path = _list_book_files(hass).get(book_id)
        book = load_spellbook_from_path(path) if path is not None else None
        return book if book is not None and not book.disabled else None

    return await hass.async_add_executor_job(_load)


