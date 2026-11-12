"""Offline conversion utility for historical action shortcuts.

Never a production loader. Unknown actions and output-only entries remain
reported by this typed-subset audit utility. Converted typed entries preserve
optional output; it never becomes an executable action.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .spellbook_engine import EngineSpellbook, load_spellbook_from_dict

# Historical conversion vocabulary, never runtime dispatch.
_LIGHT_ALL_ON = "light_all_on"
_LIGHT_ALL_OFF = "light_all_off"
_VACUUM_CLEAN = "vacuum_clean"


@dataclass(frozen=True)
class UnsupportedEntry:
    spell_id: str
    trigger: Any
    reason: str  # "free_text_output" | "unknown_action" | "vacuum_clean_missing_area_placeholder"


@dataclass(frozen=True)
class MigrationResult:
    book: EngineSpellbook | None
    unsupported: tuple[UnsupportedEntry, ...]


def _trigger_variants(trigger: Any) -> list[str]:
    if isinstance(trigger, dict):
        return [v for v in trigger.values() if isinstance(v, str)]
    if isinstance(trigger, str):
        return [trigger]
    return []


def _trigger_has_area_placeholder(trigger: Any) -> bool:
    variants = _trigger_variants(trigger)
    return bool(variants) and all("{area}" in v for v in variants)


def migrate_legacy_spellbook(book_id: str, raw: dict[str, Any]) -> MigrationResult:
    if not isinstance(raw, dict):
        return MigrationResult(book=None, unsupported=())
    name = raw.get("name")
    raw_commands = raw.get("commands")
    if not isinstance(name, str) or not name.strip() or not isinstance(raw_commands, list):
        return MigrationResult(book=None, unsupported=())

    converted: list[dict[str, Any]] = []
    unsupported: list[UnsupportedEntry] = []

    for i, raw_command in enumerate(raw_commands):
        if not isinstance(raw_command, dict):
            continue
        spell_id = raw_command.get("id") or f"{book_id}#{i}"
        trigger = raw_command.get("trigger")
        catchphrase = raw_command.get("catchphrase")
        action = raw_command.get("action")

        if action in (_LIGHT_ALL_ON, _LIGHT_ALL_OFF):
            converted.append({
                "id": spell_id,
                "trigger": trigger,
                "catchphrase": catchphrase,
                "output": raw_command.get("output"),
                "operation": "light.set_power",
                "target": {"kind": "all_allowed"},
                "arguments": {"on": action == _LIGHT_ALL_ON},
            })
        elif action == _VACUUM_CLEAN:
            if not _trigger_has_area_placeholder(trigger):
                unsupported.append(
                    UnsupportedEntry(spell_id, trigger, "vacuum_clean_missing_area_placeholder")
                )
                continue
            converted.append({
                "id": spell_id,
                "trigger": trigger,
                "catchphrase": catchphrase,
                "output": raw_command.get("output"),
                "operation": "vacuum.clean_area",
                "target": {"kind": "area_name", "capture": "area"},
                "arguments": {},
            })
        elif action is None:
            unsupported.append(UnsupportedEntry(spell_id, trigger, "free_text_output"))
        else:
            unsupported.append(UnsupportedEntry(spell_id, trigger, "unknown_action"))

    book = load_spellbook_from_dict(book_id, {"name": name, "commands": converted})
    return MigrationResult(book=book, unsupported=tuple(unsupported))
