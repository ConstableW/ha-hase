"""Converts a legacy custom_components.hase.spellbook-format YAML
dict into the new engine's typed command shape - for the small
fraction of entries that actually declare a structured `action`
(light_all_on/off, vacuum_clean). Everything else (plain free-text
`output`, forwarded to Assist by bridge.py today) is reported as
unsupported rather than silently wrapped into an ActionRequest - that
would be exactly the free-text-in-a-typed-wrapper backdoor Etappe 1
ruled out.

Deliberately reads the raw YAML dict directly instead of importing
custom_components.hase.spellbook - core/ stays independent of that
module's internals (same reasoning as core/matching.py). The legacy
file itself is untouched; this only ever reads it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .spellbook_engine import EngineSpellbook, load_spellbook_from_dict

# The only three action values custom_components.hase.providers.code27.spellbook's
# own _VALID_ACTIONS ever allowed - kept as a local, independent
# constant rather than imported, for the same reason as everything
# else in this module.
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
