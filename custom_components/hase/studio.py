"""Home Assistant adapter for the shared, book-scoped Spellbook Studio."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import tempfile
import uuid
from copy import deepcopy
from dataclasses import asdict, replace
from difflib import get_close_matches
from pathlib import Path

import yaml

from . import spellbook_loader
from .core.book_languages import (
    LIST_FIELDS,
    TEXT_FIELDS,
    default_language,
    languages,
    manage_language,
    translation_report,
)
from .core.book_languages import text as language_text
from .core.executor import OPERATION_CATALOG, ActionExecutor
from .core.models import DEFAULT_OUTPUT_MODE, OUTPUT_MODES, AreaPolicy, ScopedAreaPolicy
from .core.spellbook_engine import (
    CommandAmbiguity,
    find_command,
    load_spellbook_from_dict,
)
from .core.studio import (
    StudioError,
    book_capability,
    check_document,
    entity_capability_flags,
    export_book,
    merge_book,
    parse_document,
    preserve_user,
    spell_capability,
    spell_variables,
    static_report,
    user_actions,
)
from .spellbook_loader import USER_BOOK_SUBDIR, _list_book_files
from .spellbooker_ha import HaServiceCaller, HaTargetResolver


def _studio_book_sources(hass, book_id: str) -> tuple[Path | None, Path | None, Path | None]:
    """Resolve Studio book sources with an explicit user-override preference.

    Returns ``(active, user_override, builtin)``.  The Studio must never rely
    on directory iteration order to decide which copy wins: a user copy with
    the same logical book id always overrides the shipped built-in.
    """
    user_root = Path(hass.config.path(USER_BOOK_SUBDIR)).resolve()

    def matching(paths):
        matches = []
        for candidate in paths:
            try:
                if candidate.is_symlink() or not candidate.is_file():
                    continue
                book = spellbook_loader.load_spellbook_from_path(candidate)
            except Exception:
                continue
            if book is not None and book.id == book_id:
                matches.append(candidate.resolve())
        return matches

    user_matches = matching(user_root.glob("*.y*ml")) if user_root.exists() else []
    if len(user_matches) > 1:
        raise StudioError(f"Mehrere Benutzer-Spellbooks verwenden dieselbe ID: {book_id}")

    builtin_matches = matching(spellbook_loader.BUILTIN_BOOK_DIR.glob("*.y*ml"))
    if len(builtin_matches) > 1:
        raise StudioError(f"Mehrere ausgelieferte Spellbooks verwenden dieselbe ID: {book_id}")

    user = user_matches[0] if user_matches else None
    builtin = builtin_matches[0] if builtin_matches else None
    return user or builtin, user, builtin


def _preserve_existing_user_variant(old: dict | None, new: dict, fallback_mode: str | None) -> dict:
    """Apply the Studio's explicit User-fallback choice."""
    result = deepcopy(new)
    if fallback_mode != "keep" or old is None:
        return result

    # Triggers/name/tags remain editable/shared. Only the User execution layer
    # is preserved. This is deliberately explicit instead of capability-based:
    # an Expert operation such as light.toggle can itself be User-capable.
    for key in ("operation", "target", "arguments", "output", "catchphrase"):
        if key in old:
            result[key] = deepcopy(old[key])
        else:
            result.pop(key, None)
    return result


class SpellbookStudio:
    def __init__(self, hass):
        from homeassistant.helpers.storage import Store
        self.hass = hass
        self.lock = asyncio.Lock()
        self.permissions = Store(hass, 1, "hase.studio_permissions")
        self.ui_settings = Store(hass, 1, "hase.studio_ui")
        self.entries = set()
        self.last_reports = {}

    async def async_load(self):
        settings = await self.ui_settings.async_load() or {}
        self.hass.data["hase_studio_sidebar"] = settings.get("show_in_sidebar") is True
        saved = await self.permissions.async_load() or {}
        self.hass.data["hase_studio_permissions"] = {
            key: value is True for key, value in saved.items() if isinstance(key, str)
        }

    async def revalidate_entry(self, entry):
        """Refresh diagnostics after setup/reload without executing devices."""
        paths = await self.hass.async_add_executor_job(_list_book_files, self.hass)
        for book_id in paths:
            profiles = self.profiles(book_id)
            matching = [p for p in profiles if p[0] == entry.entry_id or p[0] in entry.subentries]
            if not matching:
                continue
            raw, revision = await self.read(book_id)
            self.last_reports[book_id] = {"revision": revision,
                                         "report": await self.report(raw, {"profile_id": matching[0][0]})}

    def expert_allowed(self, book_id):
        return self.hass.data.get("hase_studio_permissions", {}).get(book_id, False) is True

    async def file_info(self, book_id, *, cover=False):
        def inspect():
            path, user_override, builtin_path = _studio_book_sources(self.hass, book_id)
            if path is None:
                raise StudioError("Dieses Spellbook wurde nicht gefunden.")
            custom = user_override is not None and path == user_override
            builtin = builtin_path is not None
            png = path.with_suffix(".png")
            has_cover = png.is_file() and not png.is_symlink()
            info = {"origin": "override" if custom and builtin else "custom" if custom else "builtin",
                    "can_delete": custom, "can_reset": custom and builtin,
                    "filename": path.name, "has_cover": has_cover}
            if cover and has_cover:
                image = png.read_bytes()
                info["cover"] = "data:image/png;base64," + base64.b64encode(image).decode() if image.startswith(b"\x89PNG\r\n\x1a\n") else None
            return info
        return await self.hass.async_add_executor_job(inspect)

    async def remove_book(self, book_id, revision, *, reset=False):
        async with self.lock:
            def remove():
                _active, path, builtin_path = _studio_book_sources(self.hass, book_id)
                root = Path(self.hass.config.path(USER_BOOK_SUBDIR)).resolve()
                if path is None or path.is_symlink() or path.resolve().parent != root:
                    raise StudioError("Only custom books can be deleted or reset.")
                if hashlib.sha256(path.read_text(encoding="utf-8").encode()).hexdigest() != revision:
                    raise StudioError("Das Buch wurde inzwischen geändert. Bitte neu laden und Änderungen vergleichen.")
                builtin = builtin_path is not None
                if reset and not builtin:
                    raise StudioError("No built-in original exists.")
                moved = []
                try:
                    for original in (path, path.with_suffix(".png")):
                        if not original.exists():
                            continue
                        if original.is_symlink() or original.resolve().parent != root:
                            raise StudioError("Ungültiger Speicherort.")
                        temporary = root / f".hase-delete-{uuid.uuid4().hex}.tmp"
                        os.replace(original, temporary)
                        moved.append((original, temporary))
                except Exception:
                    for original, temporary in reversed(moved):
                        os.replace(temporary, original)
                    raise
                for _, temporary in moved:
                    temporary.unlink()
                return {"restored": builtin, "book_id": book_id}
            result = await self.hass.async_add_executor_job(remove)
            if not result["restored"]:
                permissions = dict(self.hass.data.get("hase_studio_permissions", {}))
                permissions.pop(book_id, None)
                await self.permissions.async_save(permissions)
                self.hass.data["hase_studio_permissions"] = permissions
            self.last_reports.pop(book_id, None)
            return result

    async def read(self, book_id):
        def read_file():
            path, _user_override, _builtin = _studio_book_sources(self.hass, book_id)
            if path is None:
                raise StudioError("Dieses Spellbook wurde nicht gefunden.")
            text = path.read_text(encoding="utf-8")
            raw = yaml.safe_load(text)
            # Older files may use the loader's generated command IDs.
            raw.setdefault("id", book_id)
            for i, spell in enumerate(raw["commands"]):
                spell.setdefault("id", f"{book_id}#{i}")
            return raw, hashlib.sha256(text.encode()).hexdigest()
        return await self.hass.async_add_executor_job(read_file)

    async def write(self, raw, revision):
        """Persist a Studio book, forking shipped books into the user directory.

        A built-in is immutable.  The first mutation writes a complete user
        override (and its adjacent cover, when present); later mutations target
        that override directly.  The built-in remains untouched and becomes
        active again when the override is reset/deleted.
        """
        check_document(raw)
        async with self.lock:
            def write_file():
                book_id = raw["id"]
                active_path, user_override, builtin_path = _studio_book_sources(self.hass, book_id)
                current = (
                    hashlib.sha256(active_path.read_text(encoding="utf-8").encode()).hexdigest()
                    if active_path else None
                )
                if current != revision:
                    raise StudioError("Das Buch wurde inzwischen geändert. Bitte neu laden und Änderungen vergleichen.")

                directory = Path(self.hass.config.path(USER_BOOK_SUBDIR)).resolve()
                directory.mkdir(parents=True, exist_ok=True)

                # User books are always edited in place.  Shipped books are
                # forked on the first mutation, preserving the shipped filename
                # so the override stays obvious on disk.
                if user_override is not None:
                    destination = user_override
                elif builtin_path is not None:
                    destination = directory / builtin_path.name
                else:
                    destination = directory / f"{book_id}.yaml"

                destination = destination.resolve()
                if destination.parent != directory:
                    raise StudioError("Ungültiger Speicherort.")
                if destination.is_symlink():
                    raise StudioError("Ungültiger Speicherort.")

                # A same-named user file that belongs to another logical book
                # must never be overwritten while creating an override.
                if destination.exists() and user_override is None:
                    existing = spellbook_loader.load_spellbook_from_path(destination)
                    if existing is None or existing.id != book_id:
                        raise StudioError("Eine andere Datei belegt diesen Speicherort.")

                text = yaml.safe_dump(raw, allow_unicode=True, sort_keys=False)
                descriptor, temporary = tempfile.mkstemp(dir=directory, suffix=".tmp")

                # Only the first fork inherits the shipped cover.  Existing user
                # covers are never replaced implicitly by a YAML save.
                cover_destination = None
                cover_bytes = None
                if user_override is None and builtin_path is not None:
                    cover_source = builtin_path.with_suffix(".png")
                    candidate = destination.with_suffix(".png")
                    if (
                        cover_source.is_file()
                        and not cover_source.is_symlink()
                        and not candidate.exists()
                        and not candidate.is_symlink()
                    ):
                        cover_destination = candidate
                        cover_bytes = cover_source.read_bytes()

                cover_created = False
                try:
                    with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
                        handle.write(text)
                        handle.flush()
                        os.fsync(handle.fileno())
                    if cover_destination is not None:
                        with cover_destination.open("xb") as handle:
                            cover_created = True
                            handle.write(cover_bytes)
                    os.replace(temporary, destination)
                except Exception:
                    if cover_created:
                        cover_destination.unlink(missing_ok=True)
                    raise
                finally:
                    if os.path.exists(temporary):
                        os.unlink(temporary)

                # Verify the fork is now the Studio's active source.  This catches
                # loader precedence regressions immediately instead of pretending
                # the save succeeded while continuing to show the built-in.
                resolved, resolved_user, _resolved_builtin = _studio_book_sources(self.hass, book_id)
                if resolved_user is None or resolved != destination:
                    raise StudioError("Benutzer-Override wurde gespeichert, konnte aber nicht als aktive Version aufgelöst werden.")

                return hashlib.sha256(text.encode()).hexdigest()

            return await self.hass.async_add_executor_job(write_file)

    def _configured_profiles(self):
        """Return every configured HASE client with its real area policy and assigned book."""
        from .const import (
            CONF_SPELLBOOKER_ALLOWED_AREAS,
            CONF_SPELLBOOKER_BOOK,
            CONF_SPELLBOOKER_QUERIES_ALLOWED_AREAS,
            CONF_SPELLBOOKER_QUERIES_UNRESTRICTED,
            CONF_SPELLBOOKER_UNRESTRICTED,
        )
        from .providers.code27.const import CONF_BRIDGE_SPELLBOOK

        results = []
        for entry in self.hass.config_entries.async_entries("hase"):
            candidates = [(entry.entry_id, entry.title, entry.options)]
            candidates.extend((s.subentry_id, s.title, s.data) for s in entry.subentries.values())

            for key, name, settings in candidates:
                spellbooker_book = settings.get(CONF_SPELLBOOKER_BOOK)
                bridge_book = settings.get(CONF_BRIDGE_SPELLBOOK)

                if spellbooker_book:
                    action = AreaPolicy(
                        frozenset(settings.get(CONF_SPELLBOOKER_ALLOWED_AREAS, [])),
                        settings.get(CONF_SPELLBOOKER_UNRESTRICTED, False) is True,
                    )
                    query = AreaPolicy(
                        frozenset(settings.get(CONF_SPELLBOOKER_QUERIES_ALLOWED_AREAS, [])),
                        settings.get(CONF_SPELLBOOKER_QUERIES_UNRESTRICTED, False) is True,
                    )
                    if (
                        CONF_SPELLBOOKER_QUERIES_ALLOWED_AREAS not in settings
                        and CONF_SPELLBOOKER_QUERIES_UNRESTRICTED not in settings
                    ):
                        query = action
                    results.append(
                        (key, name, ScopedAreaPolicy(action, query), spellbooker_book)
                    )
                elif bridge_book:
                    # Existing CODE27 owner policy, not an API-supplied permission.
                    area = AreaPolicy(None, unrestricted=True)
                    results.append((key, name, ScopedAreaPolicy(area, area), bridge_book))

        return results

    def profiles(self, book_id):
        """Runtime profiles assigned to one specific spellbook."""
        return [
            (key, name, policy)
            for key, name, policy, assigned_book in self._configured_profiles()
            if assigned_book == book_id
        ]

    def studio_profiles(self):
        """All configured clients available as an explicit Studio test context."""
        return [
            (key, name, policy)
            for key, name, policy, _assigned_book in self._configured_profiles()
        ]

    def policy(self, book_id, profile_id):
        """Runtime policy: client must actually be assigned to this spellbook."""
        for key, _, policy in self.profiles(book_id):
            if key == profile_id:
                return policy
        # No configured client means no authorisation to operate on any area.
        area = AreaPolicy(frozenset())
        return ScopedAreaPolicy(area, area)

    def studio_policy(self, profile_id):
        """Studio-only policy: use the selected client's real policy, independent of book assignment."""
        for key, _, policy in self.studio_profiles():
            if key == profile_id:
                return policy
        area = AreaPolicy(frozenset())
        return ScopedAreaPolicy(area, area)

    def operation_allowed(self, profile_id, operation):
        from .providers.code27.const import (
            CONF_BRIDGE_SPELLBOOK,
            CONF_BRIDGE_VACUUM_ENABLED,
        )
        entry = next((e for e in self.hass.config_entries.async_entries("hase") if e.entry_id == profile_id), None)
        if entry and entry.options.get(CONF_BRIDGE_SPELLBOOK) and operation == "vacuum.clean_area":
            return entry.options.get(CONF_BRIDGE_VACUUM_ENABLED, False) is True
        return True

    def areas(self):
        """Return the tiny area list used by Studio pickers."""
        from homeassistant.helpers import area_registry as ar

        areas = ar.async_get(self.hass)
        return sorted(
            ({"id": area.id, "name": area.name} for area in areas.areas.values()),
            key=lambda row: row["name"].casefold(),
        )

    def entities(self, domains=None, *, include_ids=None):
        """Return only entities needed by the currently opened picker.

        This intentionally does not build a complete HA registry snapshot.
        `domains=None` is reserved for generic operations such as entity.get_state.
        Existing selected IDs may be included even when temporarily unavailable so
        editing a spell never silently loses its configured target.
        """
        from homeassistant.helpers import area_registry as ar
        from homeassistant.helpers import device_registry as dr
        from homeassistant.helpers import entity_registry as er

        requested = None if domains is None else {d for d in domains if isinstance(d, str) and d}
        include_ids = {v for v in (include_ids or []) if isinstance(v, str) and "." in v}
        areas = ar.async_get(self.hass)
        devices = dr.async_get(self.hass)
        entities = er.async_get(self.hass)
        area_names = {area.id: area.name for area in areas.areas.values()}

        states = []
        if requested:
            for domain in requested:
                states.extend(self.hass.states.async_all(domain))
        else:
            states = self.hass.states.async_all()

        rows = []
        seen = set()
        for state in states:
            entity_id = state.entity_id
            if entity_id in seen:
                continue
            seen.add(entity_id)
            entry = entities.async_get(entity_id)
            selected = entity_id in include_ids
            if entry is not None and (entry.disabled_by is not None or entry.hidden_by is not None) and not selected:
                continue
            if state.state in {"unavailable", "unknown"} and not selected:
                continue

            area_id = entry.area_id if entry is not None else None
            if not area_id and entry is not None and entry.device_id:
                device = devices.async_get(entry.device_id)
                area_id = device.area_id if device is not None else None
            if area_id is None and not selected:
                continue

            domain = entity_id.split(".", 1)[0]
            rows.append({
                "id": entity_id,
                "name": state.attributes.get("friendly_name")
                        or (entry.name if entry is not None else None)
                        or (entry.original_name if entry is not None else None)
                        or entity_id,
                "domain": domain,
                "area_id": area_id,
                "area_name": area_names.get(area_id),
                "state": state.state,
                "selectable": state.state not in {"unavailable", "unknown"}
                              and not (entry is not None and (entry.disabled_by is not None or entry.hidden_by is not None)),
                # For the Wizard's capability-driven UI (item 13) - which
                # user_actions() rows to actually offer once this entity is
                # selected as the target. See entity_capability_flags()'s
                # own docstring for the tag vocabulary.
                "capabilities": entity_capability_flags(domain, dict(state.attributes)),
            })

        return sorted(rows, key=lambda row: (row["area_name"] or "", row["name"].casefold(), row["id"]))

    @staticmethod
    def operation_domains(operation):
        """Infer the HA entity domain used by a Studio operation."""
        if not isinstance(operation, str) or "." not in operation:
            return None
        domain = operation.split(".", 1)[0]
        return None if domain in {"entity", "homeassistant"} else [domain]

    async def test(self, raw, payload, *, execute=False):
        check_document(raw)
        book = load_spellbook_from_dict(raw["id"], raw)
        phrase = payload.get("phrase", "")
        if not isinstance(phrase, str) or len(phrase) > 2000:
            raise StudioError("Die Testphrase ist ungültig.")
        if not phrase.strip() or any(character in phrase for character in "{}[]"):
            raise StudioError("Bitte eine konkrete Testphrase ohne Platzhalter oder Klammern eingeben, z. B. „lumos Gondor“ statt „lumos {area}“.")
        found = find_command(book, phrase, payload.get("language", "de"))
        if found is None:
            return {"status": "NO_MATCH", "reason_code": "no_match", "data": {}}
        if isinstance(found, CommandAmbiguity):
            return {"status": "AMBIGUOUS", "reason_code": "ambiguous", "data": {}}
        if found.action_draft is None:
            return {"status": "UNSUPPORTED", "reason_code": "output_only", "data": {}, "spell_id": found.command.spell_id}
        preview_variant = payload.get("variant", "runtime") if not execute else "runtime"
        draft = found.action_draft
        # An enabled Expert editor test must test that draft, not silently
        # replace a failing Expert action with its stored User action.
        editor_expert = (payload.get("mode") == "expert"
                         and isinstance(payload.get("spell"), dict)
                         and isinstance(payload.get("spell", {}).get("expert"), dict)
                         and self.expert_allowed(raw["id"])
                         and preview_variant != "user")
        if editor_expert and not draft.expert:
            raise StudioError("Die Expert-Version ist ungültig. Der Test wurde ohne User-Fallback abgebrochen.")
        if preview_variant == "user" and draft.expert:
            if draft.user_fallback is None:
                return {"status": "DENIED", "reason_code": "no_user_variant", "data": {}}
            draft = draft.user_fallback
        if preview_variant == "expert" or editor_expert:
            draft = replace(draft, user_fallback=None)
        executor = ActionExecutor(HaTargetResolver(self.hass), HaServiceCaller(self.hass),
                                  expert_allowed=lambda: (not execute and preview_variant == "expert") or self.expert_allowed(raw["id"]),
                                  operation_allowed=lambda operation: self.operation_allowed(payload.get("profile_id"), operation))
        result = await executor.execute(draft, self.studio_policy(payload.get("profile_id")),
                                        source_area_id=payload.get("source_area_id"),
                                        context=payload.get("context"), dry_run=not execute)
        return {**asdict(result), "spell_id": found.command.spell_id,
                "captures": found.captures, "operation": result.data.get("effective_operation", draft.operation),
                "preview_only": not execute}

    async def report(self, raw, payload):
        check_document(raw)
        report = await self.hass.async_add_executor_job(static_report, raw)
        report["translations"] = translation_report(raw, payload.get("book_language", payload.get("language", "en")))
        areas = self.areas()
        sample_area = areas[0]["name"] if areas else "Bereich auswählen"
        for row, spell in zip(report["spells"], raw["commands"], strict=True):
            for translation in report["translations"]:
                if translation["id"] == spell["id"] and translation["used"] and translation["reason"] not in {"requested", "shared"}:
                    row["warnings"].append(f'{translation["field"]}: No {translation["requested"]} translation; {translation["used"]} fallback is used.')
            trigger = spell["trigger"]
            if isinstance(trigger, dict):
                trigger = language_text(trigger, payload.get("language", "en"), default_language(raw))
            if not trigger:
                row["warnings"].append("No trigger fallback is available; this spell will not execute.")
                continue
            if "{" in trigger:
                row["warnings"].append("Dynamischer Trigger: für vollständige Prüfung eigene Testphrase eingeben.")
                # Do not claim all captures validated against one arbitrary room.
                phrase = trigger.replace("{area}", sample_area).replace("{value}", "50")
                if "{" in phrase:
                    continue
            else:
                phrase = trigger
            single = {**raw, "commands": [{**spell, "enabled": True}]}
            for variant in ("user", "expert"):
                row[variant] = await self.test(single, {**payload, "phrase": phrase, "variant": variant})
            await asyncio.sleep(0)
        executable_statuses = {"SUCCESS", "QUERY_ANSWER"}
        report["user_executable"] = sum(r.get("user", {}).get("status") in executable_statuses and r["enabled"] for r in report["spells"])
        report["expert_executable"] = sum((r.get("expert", {}).get("status") in executable_statuses or r.get("user", {}).get("status") in executable_statuses) and r["enabled"] for r in report["spells"])
        report["warnings"] = sum(len(row["warnings"]) for row in report["spells"])
        return report

    def user_spell(self, data, old=None):
        old = deepcopy(old or {})
        draft = data.get("translations", {})
        if not isinstance(draft, dict) or set(draft) - set(TEXT_FIELDS + LIST_FIELDS):
            raise StudioError("Invalid translated content")
        old.update(deepcopy(draft))
        preserve_technical = data.get("action") == "__preserve__"
        if preserve_technical and not old.get("id"):
            raise StudioError("Ungültiger Spell.")
        choices = {a["id"]: a for a in user_actions()}
        action = {"operation":old.get("operation"), "arguments":old.get("arguments", {}),
                  "targets":[old.get("target", {}).get("kind")], "label":"Existing action"} if preserve_technical else choices.get(data.get("action"))
        if action is None:
            raise StudioError("Bitte eine unterstützte Aktion auswählen.")
        kind = old.get("target", {}).get("kind") if preserve_technical else data.get("target_kind")
        if kind not in action["targets"]:
            raise StudioError("Dieses Ziel wird von der Aktion nicht unterstützt.")
        target = {"kind": kind}
        value = data.get("target_value")
        if preserve_technical:
            target = deepcopy(old.get("target"))
        elif kind == "area_name":
            capture = action.get("target_capture", "area")
            if not (isinstance(capture, str) and capture.isidentifier()):
                raise StudioError("Ungültiger Capture-Name für diese Aktion.")
            target["capture"] = capture
        elif kind == "area_id":
            if value not in {a["id"] for a in self.areas()}:
                raise StudioError("Bitte einen vorhandenen Bereich auswählen.")
            target["value"] = value
        elif kind in ("entity_id", "entity_ids"):
            values = value if kind == "entity_ids" else [value]
            if not isinstance(values, list) or not values or any(
                not isinstance(v, str) or self.hass.states.get(v) is None for v in values
            ):
                raise StudioError("Bitte vorhandene Geräte auswählen.")
            target["value"] = values if kind == "entity_ids" else value
        elif kind == "entity_name":
            raise StudioError("Bitte die Geräteauswahl verwenden.")
        triggers = data.get("triggers")
        if not isinstance(triggers, list) or not triggers or not all(isinstance(t, str) and t.strip() for t in triggers):
            raise StudioError("Mindestens ein gesprochener Trigger ist erforderlich.")
        if kind == "area_name":
            capture = target["capture"]
            placeholder = "{" + capture + "}"
            triggers = [t if placeholder in t else t + " " + placeholder for t in triggers]

        required_argument_captures = {
            spec["capture"]
            for spec in action["arguments"].values()
            if isinstance(spec, dict) and isinstance(spec.get("capture"), str)
        }
        for capture in sorted(required_argument_captures):
            if any("{" + capture + "}" not in trigger for trigger in triggers):
                raise StudioError(
                    f'Die Aktion „{action["label"]}“ benötigt {{{capture}}} in jeder Trigger-Formulierung.'
                )

        edit_language = data.get("trigger_language")
        first_trigger = triggers[0]
        if edit_language:
            previous = (old or {}).get("trigger")
            first_trigger = dict(previous) if isinstance(previous, dict) else ({data.get("default_language", edit_language): previous} if previous else {})
            first_trigger[edit_language] = triggers[0]
        elif old and isinstance(old.get("trigger"), dict):
            language = edit_language or next(iter(old["trigger"]))
            first_trigger = {**old["trigger"], language: first_trigger}
        output_mode = data.get("output_mode") or old.get("output_mode") or DEFAULT_OUTPUT_MODE
        if output_mode not in OUTPUT_MODES:
            raise StudioError("Ungültiger Output-Modus.")
        result = deepcopy(old or {})
        result.update(id=data.get("id") or uuid.uuid4().hex, name=data.get("name") or triggers[0],
                      trigger=first_trigger, triggers=triggers[1:], operation=action["operation"],
                      arguments=action["arguments"], target=target,
                      tags=data.get("tags", old.get("tags", [])), enabled=data.get("enabled", old.get("enabled", True)),
                      output_mode=output_mode)
        if edit_language:
            for field, value in (("name", data.get("name") or triggers[0]), ("description", data.get("description", "")),
                                 ("examples", data.get("examples", [])), ("triggers", triggers[1:]),
                                 ("output", data.get("output")), ("catchphrase", data.get("catchphrase"))):
                if value is None:
                    continue
                previous = (old or {}).get(field)
                mapping = dict(previous) if isinstance(previous, dict) else ({data.get("default_language", edit_language): previous} if previous else {})
                if value:
                    mapping[edit_language] = value
                else:
                    mapping.pop(edit_language, None)
                if mapping:
                    result[field] = mapping
                else:
                    result.pop(field, None)
        if preserve_technical:
            for field in ("operation", "arguments", "target"):
                if field in old:
                    result[field] = deepcopy(old[field])
                else:
                    result.pop(field, None)
        return result

    async def dispatch(self, operation, data, context):
        if operation in {"import_book_preview", "import_book_apply"}:
            incoming = parse_document(data.get("text"))
            paths = await self.hass.async_add_executor_job(_list_book_files, self.hass)
            if incoming["id"] in paths:
                raise StudioError("Diese Buch-ID existiert bereits. Bitte das vorhandene Buch öffnen und dort importieren.")
            report = await self.report(incoming, data)
            if operation == "import_book_preview":
                return {"incoming": incoming, "report": report,
                        "expert_allowed": self.expert_allowed(incoming["id"])}
            revision = await self.write(incoming, None)
            return {"book_id": incoming["id"], "revision": revision, "report": report}
        if operation == "list":
            paths = await self.hass.async_add_executor_job(_list_book_files, self.hass)
            books = []
            for book_id in paths:
                raw, revision = await self.read(book_id)
                books.append({"id": book_id, "name": raw["name"], "count": len(raw["commands"]),
                              "capability": book_capability(raw), "expert_allowed": self.expert_allowed(book_id),
                              "active_clients": len(self.profiles(book_id)),
                              "description": raw.get("description", ""), "metadata": raw.get("metadata", {}),
                              "revision": revision, "default_language": default_language(raw), "languages": languages(raw),
                              **await self.file_info(book_id, cover=False)})
            return {"books": books, "actions": user_actions(), "operations": list(OPERATION_CATALOG),
                    "output_modes": sorted(OUTPUT_MODES), "default_output_mode": DEFAULT_OUTPUT_MODE,
                    "show_in_sidebar": self.hass.data.get("hase_studio_sidebar", False)}
        if operation == "ui_settings":
            allowed = data.get("show_in_sidebar")
            if not isinstance(allowed, bool):
                raise StudioError("Die Seitenleistenoption muss an oder aus sein.")
            await self.ui_settings.async_save({"show_in_sidebar": allowed})
            self.hass.data["hase_studio_sidebar"] = allowed
            from .studio_api import async_update_panel
            async_update_panel(self.hass)
            return {"show_in_sidebar": allowed}
        if operation == "validate_all":
            paths = await self.hass.async_add_executor_job(_list_book_files, self.hass)
            results = []
            for book_id in paths:
                raw, _ = await self.read(book_id)
                profiles = self.profiles(book_id)
                results.append({"id": book_id, "name": raw["name"],
                                "profile": profiles[0][1] if profiles else None,
                                "report": await self.report(raw, {"profile_id": profiles[0][0] if profiles else None})})
            return {"books": results}
        if operation == "areas":
            return {"areas": self.areas()}
        if operation == "entities":
            domains = data.get("domains")
            if domains is not None and (
                not isinstance(domains, list)
                or any(not isinstance(domain, str) or not domain for domain in domains)
            ):
                raise StudioError("Ungültiger Entity-Domain-Filter.")
            include_ids = data.get("include_ids", [])
            if not isinstance(include_ids, list):
                raise StudioError("Ungültige Entity-Auswahl.")
            return {"entities": self.entities(domains or None, include_ids=include_ids)}
        if operation == "cover":
            book_id = data.get("book_id")
            if not isinstance(book_id, str):
                raise StudioError("Bitte ein Spellbook auswählen.")
            info = await self.file_info(book_id, cover=True)
            return {"cover": info.get("cover")}
        if operation == "create_book":
            language = data.get("language") or "en"
            raw = {"id": data.get("book_id"), "name": data.get("name"), "commands": [],
                   "default_language": language, "languages": [language]}
            return {"revision": await self.write(raw, None)}
        book_id = data.get("book_id")
        if not isinstance(book_id, str):
            raise StudioError("Bitte ein Spellbook auswählen.")
        raw, revision = await self.read(book_id)
        if operation == "read":
            return {"book": raw, "revision": revision, "expert_allowed": self.expert_allowed(book_id),
                    "spell_capabilities": {c["id"]: spell_capability(c) for c in raw["commands"]},
                    "spell_variables": {c["id"]: spell_variables(c) for c in raw["commands"]},
                    "capability": book_capability(raw),
                    "profiles": [{"id": k, "name": n} for k, n, _ in self.profiles(book_id)],
                    "studio_profiles": [{"id": k, "name": n} for k, n, _ in self.studio_profiles()],
                    "languages": languages(raw), "default_language": default_language(raw),
                    "translations": translation_report(raw, data.get("book_language", data.get("language", "en"))),
                    **await self.file_info(book_id, cover=True)}
        if operation in {"delete_book", "reset_book"}:
            if data.get("confirmed") is not True:
                raise StudioError("Please confirm deleting or resetting this book.")
            return await self.remove_book(book_id, data.get("revision"), reset=operation == "reset_book")
        if operation == "manage_language":
            try:
                updated = manage_language(raw, data.get("action"), data.get("code"), data.get("source"))
            except ValueError as err:
                raise StudioError(str(err)) from err
            if data.get("action") == "remove" and data.get("confirmed") is not True:
                raise StudioError("Please confirm removing this language.")
            return {"revision": await self.write(updated, data.get("revision"))}
        if operation == "save_metadata":
            metadata = data.get("metadata")
            allowed = {"name", "description", "author", "version", "language", "languages", "default_language", "examples", "hase_min_version", "tags", "metadata"}
            if not isinstance(metadata, dict) or set(metadata) - allowed:
                raise StudioError("Ungültige Buch-Metadaten.")
            raw.update(metadata)
            return {"revision": await self.write(raw, data.get("revision"))}
        if operation == "expert_permission":
            if type(data.get("allowed")) is not bool:
                raise StudioError("Ungültige Freigabe.")
            async with self.lock:
                permissions = {**self.hass.data["hase_studio_permissions"], book_id: data["allowed"]}
                await self.permissions.async_save(permissions)
                self.hass.data["hase_studio_permissions"] = permissions
            return {"expert_allowed": self.expert_allowed(book_id)}
        if operation in {"validate", "dry_run", "execute"}:
            candidate = deepcopy(raw)
            # Studio checks and confirmed live tests use the current editor
            # candidate in memory. This does not persist anything.
            if data.get("spell") is not None:
                old = next((s for s in raw["commands"] if s["id"] == data["spell"].get("id")), None)
                if data.get("mode") == "user":
                    spell = self.user_spell(data["spell"], old)
                else:
                    spell = _preserve_existing_user_variant(
                        old, data["spell"], data.get("fallback_mode")
                    )
                candidate["commands"] = [spell]
            payload = {**data, "context": context}
            if operation == "validate":
                return await self.report(candidate, payload)
            if operation == "execute" and data.get("confirmed") is not True:
                raise StudioError("Echte Ausführung muss bewusst bestätigt werden.")
            if operation == "execute" and data.get("revision") != revision:
                raise StudioError("Das Buch wurde geändert. Bitte erneut testen.")
            return await self.test(candidate, payload, execute=operation == "execute")
        if operation == "export":
            exported = export_book(raw, data.get("ids"), data.get("collection"))
            warnings = []
            for spell in exported["commands"]:
                for definition in (spell, spell.get("expert", {})):
                    target = definition.get("target", {})
                    if target.get("kind") in {"entity_id", "entity_ids", "area_id", "area_name", "template"} and "value" in target:
                        warnings.append({"id": spell["id"], "kind": target["kind"], "message": "Instanzabhängiges Ziel; vor Import prüfen."})
            return {"yaml": yaml.safe_dump(exported, allow_unicode=True, sort_keys=False), "portability": warnings}
        if operation in {"import_preview", "import_apply"}:
            incoming = parse_document(data.get("text"))
            decisions = data.get("decisions", {})
            if not isinstance(decisions, dict):
                raise StudioError("Ungültige Konfliktentscheidungen.")
            merged = merge_book(raw, incoming, decisions)
            if operation == "import_preview":
                suggestions = []
                areas = self.areas()
                for spell in incoming["commands"]:
                    for variant, definition in (("user", spell), ("expert", spell.get("expert", {}))):
                        target = definition.get("target", {})
                        kind = target.get("kind")
                        value = target.get("value")
                        if not isinstance(value, str) or kind == "template":
                            continue
                        if kind in {"area_id", "area_name"}:
                            candidates = areas
                            key = "name" if kind == "area_name" else "id"
                            category = "areas"
                        elif kind in {"entity_id", "entity_name"}:
                            candidates = self.entities(self.operation_domains(definition.get("operation")))
                            key = "name" if kind == "entity_name" else "id"
                            category = "entities"
                        else:
                            continue
                        available = [row[key] for row in candidates]
                        if value not in available:
                            choices = get_close_matches(value, available, n=3, cutoff=.35)
                            suggestions.append({
                                "id": spell["id"], "variant": variant, "value": value,
                                "category": category, "key": key, "suggestions": choices,
                                "options": [{"id": row[key], "name": row["name"]} for row in candidates],
                            })
                return {"incoming": incoming, "conflicts": [s["id"] for s in incoming["commands"] if any(o["id"] == s["id"] for o in raw["commands"])],
                        "report": await self.report({**incoming, "id": book_id}, data), "suggestions": suggestions,
                        "expert_allowed": self.expert_allowed(book_id)}
            report = await self.report(merged, data)
            revision = await self.write(merged, data.get("revision"))
            return {"revision": revision, "report": report}
        if operation in {"save_spell", "delete_spell", "duplicate_spell", "toggle_spell"}:
            sid = data.get("spell_id")
            index = next((i for i, s in enumerate(raw["commands"]) if s["id"] == sid), None)
            old = raw["commands"][index] if index is not None else None
            if operation == "save_spell":
                candidate = data.get("spell")
                if not isinstance(candidate, dict):
                    raise StudioError("Ungültiger Spell.")
                if data.get("mode") == "user":
                    spell = self.user_spell(candidate, old)
                else:
                    spell = _preserve_existing_user_variant(
                        old, candidate, data.get("fallback_mode")
                    )

                # The editor always sends its current Expert draft so it can be
                # tested independently of persistence. "Expert-Variante speichern"
                # controls persistence only:
                # - checked: store the current expert draft
                # - unchecked + existing expert: preserve the stored expert
                # - unchecked + no existing expert: do not create one
                if data.get("mode") == "expert" and data.get("persist_expert") is not True:
                    if old is not None and isinstance(old.get("expert"), dict):
                        spell["expert"] = deepcopy(old["expert"])
                    else:
                        spell.pop("expert", None)

                spell = preserve_user(old, spell, remove_user=data.get("remove_user") is True)
                if old is not None:
                    spell["id"] = old["id"]
                    raw["commands"][index] = spell
                else:
                    raw["commands"].append(spell)
            elif old is None:
                raise StudioError("Spell nicht gefunden.")
            elif operation == "delete_spell":
                raw["commands"].pop(index)
            elif operation == "duplicate_spell":
                name = old.get("name", old["id"])
                name = {lang: f"{value} (copy)" for lang, value in name.items()} if isinstance(name, dict) else f"{name} (copy)"
                raw["commands"].append({**deepcopy(old), "id": uuid.uuid4().hex, "name": name})
            else:
                old["enabled"] = not old.get("enabled", True)
            report = await self.report(raw, data)
            revision = await self.write(raw, data.get("revision"))
            return {"revision": revision, "report": report}
        raise StudioError("Unbekannte Studio-Funktion.")
