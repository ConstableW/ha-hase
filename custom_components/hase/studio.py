"""Home Assistant adapter for the shared, book-scoped Spellbook Studio."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import re
import tempfile
import time
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
from .core.collision_analyzer import analyze_collisions
from .core.executor import OPERATION_CATALOG, ActionExecutor
from .core.match_explain import build_match_explain
from .core.models import DEFAULT_OUTPUT_MODE, OUTPUT_MODES, AreaPolicy, ScopedAreaPolicy
from .core.spellbook_engine import (
    _MAX_STEPS_PER_COMMAND,
    _QUERY_SOURCE_KINDS,
    _VALID_TARGET_KINDS,
    CommandAmbiguity,
    _parse_sources,
    explain,
    load_spellbook_from_dict,
)
from .core.studio import (
    StudioError,
    _is_freeform_light_color,
    as_logical_steps,
    attach_mcp_capability_cache,
    book_capability,
    check_document,
    classify_spell,
    _describe_action_arguments_shape,
    entity_capability_flags,
    export_book,
    mcp_capability_report,
    merge_book,
    parse_document,
    preserve_user,
    query_source_capabilities,
    spell_capability,
    spell_variables,
    static_report,
    user_actions,
    wizard_blocker_details,
    wizard_incompatibilities,
    wizard_step_flags,
)
from .core.studio_validation import validate_wizard_step
from .execution_trace import record_execution
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


def _collision_report(raw: dict, language: str = "en") -> dict:
    """Advisory exact-structure collision diagnostics for Studio.

    Matching Semantics v1 remains authoritative. The static analyzer is
    deliberately conservative and currently reports only collisions it can
    prove from identical normalized trigger shapes.
    """
    book = load_spellbook_from_dict(raw.get("id", ""), raw)
    if book is None:
        return {"semantics": 1, "scope": "exact_trigger_shape", "findings": [], "count": 0, "spells": {},
                "status": _status(0, 0)}
    findings = [asdict(finding) for finding in analyze_collisions(book, language)]
    return {
        "semantics": 1,
        "scope": "exact_trigger_shape",
        "findings": findings,
        "count": len(findings),
        "spells": _collision_status_by_spell(book, findings),
        "status": _collision_summary(findings),
    }


def _status(errors: int, warnings: int, infos: int = 0) -> dict:
    """Uniform summary for Studio tab badges: the UI renders it, it never
    derives it."""
    state = "error" if errors else "warning" if warnings else "ok"
    return {"state": state, "errors": errors, "warnings": warnings, "infos": infos}


def _collision_summary(findings: list[dict]) -> dict:
    severities = [finding.get("severity", "info") for finding in findings]
    return _status(severities.count("error"), severities.count("warning"), severities.count("info"))


def _collision_status_by_spell(book, findings: list[dict]) -> dict[str, dict]:
    """Per-spell badge data derived only from analyzer findings and their
    analyzer-assigned severity - the UI renders it, it never classifies."""
    status: dict[str, dict] = {}
    for command in book.commands:
        status.setdefault(command.spell_id, {
            "status": "ok" if command.enabled else "not_analyzed",
            "errors": 0, "warnings": 0, "infos": 0, "findings": [],
        })
    rank = {"ok": 0, "not_analyzed": 0, "info": 0, "warning": 1, "error": 2}
    for index, finding in enumerate(findings):
        severity = finding.get("severity", "info")
        for spell_id in set(finding.get("spell_ids") or ()):
            entry = status.setdefault(spell_id, {
                "status": "ok", "errors": 0, "warnings": 0, "infos": 0, "findings": [],
            })
            entry[{"error": "errors", "warning": "warnings"}.get(severity, "infos")] += 1
            entry["findings"].append(index)
            if severity in {"error", "warning"} and rank[severity] > rank[entry["status"]]:
                entry["status"] = severity
    return status


def _safety_findings(raw: dict, language: str = "en") -> list[dict]:
    """Return only statically proven safety ambiguities for a complete book."""
    return [
        finding
        for finding in _collision_report(raw, language)["findings"]
        if finding.get("kind") == "safety_ambiguity"
    ]


def _spell_targets(spell: dict):
    """Yield every target position accepted by the current spell schema.

    Keep raw paths alongside definitions so import suggestions can use the
    action operation or query source filter without a second target model.
    """
    def one(definition, variant, path):
        if isinstance(definition, dict):
            target = definition.get("target")
            if isinstance(target, dict):
                yield variant, path + ".target", definition, target
            for index, source in enumerate(definition.get("sources") or []):
                if isinstance(source, dict) and isinstance(source.get("target"), dict):
                    yield variant, f"{path}.sources[{index}].target", source, source["target"]

    yield from one(spell, "user", "spell")
    yield from one(spell.get("expert"), "expert", "spell.expert")
    for index, step in enumerate(spell.get("steps") or []):
        yield from one(step, "step", f"spell.steps[{index}]")


def _target_values(target: dict):
    value = target.get("value")
    return value if target.get("kind") == "entity_ids" and isinstance(value, list) else [value]


def _entity_id_targets(spell: dict) -> list[str]:
    return list(dict.fromkeys(
        value for _variant, _path, _definition, target in _spell_targets(spell)
        if target.get("kind") in {"entity_id", "entity_ids"}
        for value in _target_values(target)
        if isinstance(value, str) and "." in value
    ))


def _validation_issues(
    raw: dict,
    language: str = "en",
    *,
    owner_spell_id: str | None = None,
) -> list[dict]:
    """Build explicit validation issues without mutating authoring data.

    Interactive create/edit has one owner: only that spell receives the ERROR.
    Full-book/import validation has no owner: both sides receive an ERROR with
    a cross-reference.  order_dependent stays advisory in collision_report and
    is deliberately not an error here.
    """
    issues: list[dict] = []
    seen: set[tuple[str, str, str]] = set()

    for finding in _safety_findings(raw, language):
        spell_ids = tuple(finding.get("spell_ids") or ())
        if len(spell_ids) != 2:
            continue
        left, right = spell_ids

        if owner_spell_id is not None:
            if owner_spell_id not in spell_ids:
                continue
            owners = (owner_spell_id,)
        else:
            owners = spell_ids

        for owner in owners:
            related = right if owner == left else left
            key = ("safety_ambiguity", owner, related)
            if key in seen:
                continue
            seen.add(key)
            issues.append({
                "code": "safety_ambiguity",
                "severity": "error",
                "spell_id": owner,
                "related_spell_ids": [related],
                "message": (
                    "Dieser Spell ist statisch sicherheitsmehrdeutig mit "
                    f"„{related}“: gleiches Ziel, aber gegensätzliche Aktion."
                ),
                "finding": finding,
            })
    return issues


def _checked_phrase(phrase) -> str:
    if not isinstance(phrase, str) or len(phrase) > 2000:
        raise StudioError("Die Testphrase ist ungültig.")
    if not phrase.strip() or any(character in phrase for character in "{}[]"):
        raise StudioError("Bitte eine konkrete Testphrase ohne Platzhalter oder Klammern eingeben, z. B. „lumos Gondor“ statt „lumos {area}“.")
    return phrase


def _book_with_spell(raw: dict, spell: dict) -> dict:
    """Complete book copy with one (edited/unsaved) spell substituted or
    appended - the context in which matching and safety must be judged."""
    candidate = deepcopy(raw)
    index = next(
        (i for i, existing in enumerate(candidate["commands"]) if existing["id"] == spell["id"]),
        None,
    )
    if index is None:
        candidate["commands"].append(deepcopy(spell))
    else:
        candidate["commands"][index] = deepcopy(spell)
    return candidate


def _describe_action_draft(payload) -> list[dict]:
    """The "Apply" button's single-draft structural check (Step 2's
    "an invalid draft must never be accepted into the canonical step
    state" requirement) - reuses the exact same primitives Step 2's own
    Next-gate uses (_describe_action_arguments_shape, _parse_sources),
    never a second copy of these rules. _describe_action_arguments_shape
    (not ActionExecutor.describe_shape/_describe_argument_errors) is used
    deliberately - an Expert draft's arguments may themselves be capture
    specs (free-form JSON, same as YAML), which those two expect already
    resolved to literal values. Target VALUE shape is deliberately not
    checked here either - a capture-based target (spoken area/entity) has
    no resolved value yet at draft time, only a `capture` reference,
    which is a Step 3 (Trigger) concern."""
    def issue(code: str, path: str, message: str) -> dict:
        return {"code": code, "severity": "error", "path": path, "message": message,
                "step_index": None, "classification": None}

    if not isinstance(payload, dict):
        return [issue("invalid_draft", "", "Ungültiger Entwurf.")]
    if isinstance(payload.get("sources"), list):
        diagnostics: list[dict] = []
        placeholder_names = {n for n in (payload.get("placeholder_names") or []) if isinstance(n, str)}
        if _parse_sources("draft", payload["sources"], placeholder_names, diagnostics=diagnostics) is None:
            return [issue(d["code"], d["path"], d["message"]) for d in diagnostics]
        return []
    operation = payload.get("operation")
    target = payload.get("target") if isinstance(payload.get("target"), dict) else {}
    arguments = payload.get("arguments") if isinstance(payload.get("arguments"), dict) else {}
    kind = target.get("kind")
    if not isinstance(operation, str) or not operation.strip():
        return [issue("invalid_operation", "operation", "Operation fehlt oder ist ungültig.")]
    spec = OPERATION_CATALOG.get(operation)
    if spec is None:
        return [issue("unknown_operation", "operation", f"Unbekannte Operation „{operation}“.")]
    if kind is not None and kind not in spec.allowed_target_kinds:
        return [issue("target_kind_not_allowed", "target.kind", f"Zieltyp „{kind}“ ist für „{operation}“ nicht zulässig.")]
    return [issue(err["code"], err["path"], err["message"]) for err in _describe_action_arguments_shape(spec, arguments)]


def _explain_phrase(raw: dict, phrase: str, language: str) -> dict:
    """Studio Explain: run the production matcher once with trace collection
    on the complete book and return the stable MatchExplain contract.  No
    Home Assistant calls, no execution, nothing persisted."""
    book = load_spellbook_from_dict(raw.get("id", ""), raw)
    if book is None:
        raise StudioError("Das Spellbook ist in diesem Zustand nicht ladbar.")
    return build_match_explain(explain(book, phrase, language)).as_dict()


def _spell_display_name(raw: dict, spell_id: str, language: str = "en") -> str:
    """Resolve an author-facing name while ids remain the stable reference."""
    spell = next(
        (s for s in raw.get("commands", []) if s.get("id") == spell_id),
        None,
    )
    if not spell:
        return spell_id

    for field in ("name", "trigger"):
        value = spell.get(field)

        if isinstance(value, str) and value.strip():
            return value.strip()

        if isinstance(value, dict):
            localized = language_text(
                value,
                language,
                default_language(raw),
            )
            if localized and localized.strip():
                return localized.strip()

    return spell_id

def _owned_safety_confirmation(
    raw: dict,
    owner_spell_id: str,
    language: str = "en",
) -> dict | None:
    """Return structured confirmation data for an interactive safety ambiguity."""
    issues = _validation_issues(raw, language, owner_spell_id=owner_spell_id)
    if not issues:
        return None
    related_ids = sorted({
        related
        for issue in issues
        for related in issue.get("related_spell_ids", [])
    })
    return {
        "requires_confirmation": True,
        "confirmation_code": "safety_ambiguity",
        "validation_issues": issues,
        "related_spell_ids": related_ids,
        "related_spell_names": [
            _spell_display_name(raw, spell_id, language)
            for spell_id in related_ids
        ],
    }


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

        Also refreshes every spell's own persisted MCP-capability status
        (attach_mcp_capability_cache) before writing - the single hook
        point for the "automatically saved status" the docs promise, so
        every save (a spell edit, an import, a language/metadata change,
        an explicit "MCP prüfen") keeps it current without each call site
        having to remember to do so separately. Runs AFTER the first
        check_document so a malformed document is still rejected with a
        clean StudioError first, never reaching the capability
        computation at all; check_document runs again afterward too,
        since attaching a cache entry to every spell can itself push a
        near-the-limit book over MAX_BYTES - the same size/shape
        invariant every other save must satisfy, not a cache-only
        exemption.
        """
        check_document(raw)
        raw = attach_mcp_capability_cache(raw)
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

        from homeassistant.components.homeassistant.exposed_entities import (
            async_should_expose,
        )
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
            if area_id is None and not selected and requested is None:
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
                "exposed": async_should_expose(self.hass, "conversation", entity_id),
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
        started_at = time.monotonic()
        check_document(raw)
        book = load_spellbook_from_dict(raw["id"], raw)
        phrase = _checked_phrase(payload.get("phrase", ""))
        explanation = explain(book, phrase, payload.get("language", "de"))
        found = explanation.result
        matching_trace = asdict(explanation.trace)
        match_explain = build_match_explain(explanation).as_dict()

        def with_trace(result: dict) -> dict:
            return {**result, "matching_trace": matching_trace, "match_explain": match_explain}

        def record(result):
            if isinstance(getattr(self.hass, "data", None), dict):
                record_execution(self.hass, channel="studio_execute" if execute else "studio_dry_run",
                                 book_id=raw["id"], spell_id=found.command.spell_id,
                                 result=result, started_at=started_at)

        if found is None:
            return with_trace({"status": "NO_MATCH", "reason_code": "no_match", "data": {}})
        if isinstance(found, CommandAmbiguity):
            return with_trace({"status": "AMBIGUOUS", "reason_code": "ambiguous", "data": {}})
        if found.step_plan is not None:
            # Combined Spells are USER-tier only (see core/models.py's
            # ActionStep docstring) - no expert/preview_variant concept,
            # just the same dry_run-vs-execute distinction execute()
            # already gives every single-action test.
            executor = ActionExecutor(HaTargetResolver(self.hass), HaServiceCaller(self.hass),
                                      expert_allowed=lambda: self.expert_allowed(raw["id"]),
                                      operation_allowed=lambda operation: self.operation_allowed(payload.get("profile_id"), operation))
            result = await executor.execute_steps(found.step_plan, self.studio_policy(payload.get("profile_id")),
                                                  source_area_id=payload.get("source_area_id"),
                                                  context=payload.get("context"), dry_run=not execute)
            record(result)
            return with_trace({**asdict(result), "spell_id": found.command.spell_id,
                    "captures": found.captures, "operation": None, "preview_only": not execute})
        if found.query_plan is not None:
            # Query sources read HA state only. Reuse the production resolver
            # and response renderer for both preview and live execution.
            executor = ActionExecutor(HaTargetResolver(self.hass), HaServiceCaller(self.hass))
            result = await executor.execute_query_plan(
                found.query_plan, self.studio_policy(payload.get("profile_id")),
                source_area_id=payload.get("source_area_id"), context=payload.get("context"),
            )
            record(result)
            return with_trace({**asdict(result), "spell_id": found.command.spell_id,
                    "captures": found.captures, "operation": None,
                    "query_plan": asdict(found.query_plan), "preview_only": not execute})
        if found.action_draft is None:
            return with_trace({"status": "UNSUPPORTED", "reason_code": "output_only", "data": {}, "spell_id": found.command.spell_id})
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
                return with_trace({"status": "DENIED", "reason_code": "no_user_variant", "data": {}})
            draft = draft.user_fallback
        if preview_variant == "expert" or editor_expert:
            draft = replace(draft, user_fallback=None)
        executor = ActionExecutor(HaTargetResolver(self.hass), HaServiceCaller(self.hass),
                                  expert_allowed=lambda: (not execute and preview_variant == "expert") or self.expert_allowed(raw["id"]),
                                  operation_allowed=lambda operation: self.operation_allowed(payload.get("profile_id"), operation))
        result = await executor.execute(draft, self.studio_policy(payload.get("profile_id")),
                                        source_area_id=payload.get("source_area_id"),
                                        context=payload.get("context"), dry_run=not execute)
        record(result)
        return with_trace({**asdict(result), "spell_id": found.command.spell_id,
                "captures": found.captures, "operation": result.data.get("effective_operation", draft.operation),
                "preview_only": not execute})

    async def report(self, raw, payload):
        check_document(raw)
        report = await self.hass.async_add_executor_job(static_report, raw)
        report["translations"] = translation_report(raw, payload.get("book_language", payload.get("language", "en")))
        areas = self.areas()
        sample_area = areas[0]["name"] if areas else "Bereich auswählen"
        from homeassistant.components.homeassistant.exposed_entities import (
            async_should_expose,
        )
        for row, spell in zip(report["spells"], raw["commands"], strict=True):
            for entity_id in _entity_id_targets(spell):
                if not async_should_expose(self.hass, "conversation", entity_id):
                    row["warnings"].append(
                        f'Das Ziel "{entity_id}" ist nicht für Assist freigegeben '
                        "und kann nicht aufgelöst werden."
                    )
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
                row.setdefault("infos", []).append("Dynamischer Trigger: für vollständige Prüfung eigene Testphrase eingeben.")
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
        user_ids = [r["id"] for r in report["spells"]
                    if r.get("user", {}).get("status") in executable_statuses and r["enabled"]]
        expert_ids = [r["id"] for r in report["spells"]
                      if (r.get("expert", {}).get("status") in executable_statuses
                          or r.get("user", {}).get("status") in executable_statuses) and r["enabled"]]
        report["user_executable"] = len(user_ids)
        report["expert_executable"] = len(expert_ids)
        # Which spells each summary tile counts - Studio lists them on click
        # instead of re-deriving the classification.
        report["executable_ids"] = {"user": user_ids, "expert": expert_ids}
        report["warnings"] = sum(len(row["warnings"]) for row in report["spells"])
        report_language = payload.get("book_language", payload.get("language", "en"))
        report["collisions"] = _collision_report(raw, report_language)
        report["validation_issues"] = _validation_issues(raw, report_language)
        # static_report's "errors" are schema errors; P5 safety issues are a
        # separate authoring-error source.  Keep both visible and let
        # "errors" be their total (it used to be overwritten by the safety
        # count alone, hiding schema errors from the summary).
        report["schema_errors"] = report.get("errors", 0)
        report["safety_errors"] = sum(
            issue.get("severity") == "error"
            for issue in report["validation_issues"]
        )
        report["errors"] = report["schema_errors"] + report["safety_errors"]
        report["status"] = _status(report["errors"], report["warnings"])
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
                                 ("output", data.get("output")), ("catchphrase", data.get("catchphrase")),
                                 ("failure_text", data.get("failure_text"))):
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

    _STEP_TARGET_KINDS = frozenset({"area_id", "entity_id", "entity_ids", "all_allowed", "source_area"})

    def _resolve_step_target(self, kind, value, *, label):
        """One step's target - the same area_id/entity_id/entity_ids
        validation user_spell() already does for a single action,
        minus area_name/entity_name: a spoken-area capture would need
        its own reserved placeholder name per step (and a design
        decision for what it means when two steps share one), which
        this first guided step-list builder pass deliberately doesn't
        offer yet - still reachable by hand-editing a step's YAML
        through the Manual/Expert editor, since the backend schema and
        execution already support it fully."""
        if kind not in self._STEP_TARGET_KINDS:
            raise StudioError(f"{label}: dieses Ziel wird für Schritte noch nicht unterstützt.")
        target = {"kind": kind}
        if kind == "area_id":
            if value not in {a["id"] for a in self.areas()}:
                raise StudioError(f"{label}: bitte einen vorhandenen Bereich auswählen.")
            target["value"] = value
        elif kind in ("entity_id", "entity_ids"):
            values = value if kind == "entity_ids" else [value]
            if not isinstance(values, list) or not values or any(
                not isinstance(v, str) or self.hass.states.get(v) is None for v in values
            ):
                raise StudioError(f"{label}: bitte vorhandene Geräte auswählen.")
            target["value"] = values if kind == "entity_ids" else value
        return target

    def steps_spell(self, data, old=None):
        """Combined Spells' sibling of user_spell() - turns the Wizard's
        step-list builder payload into a spell dict's `steps` field.
        USER-tier only this pass (see core/models.py's ActionStep
        docstring) - no Expert variant for a steps spell."""
        old = deepcopy(old or {})
        draft = data.get("translations", {})
        if not isinstance(draft, dict) or set(draft) - set(TEXT_FIELDS + LIST_FIELDS):
            raise StudioError("Invalid translated content")
        old.update(deepcopy(draft))

        triggers = data.get("triggers")
        if not isinstance(triggers, list) or not triggers or not all(isinstance(t, str) and t.strip() for t in triggers):
            raise StudioError("Mindestens ein gesprochener Trigger ist erforderlich.")

        raw_steps = data.get("steps")
        if not isinstance(raw_steps, list) or not raw_steps:
            raise StudioError("Mindestens ein Schritt ist erforderlich.")

        action_choices = {a["id"]: a for a in user_actions()}
        # Same rule as the engine's _parse_steps: an alias must not shadow a
        # trigger capture - reported here with a clear message instead of
        # the generic load failure.
        trigger_placeholders = {
            name for trigger in triggers for name in re.findall(r"\{(\w+)\}", trigger)
        }
        old_steps = old.get("steps") if isinstance(old.get("steps"), list) else []
        seen_aliases = set()
        steps = []
        for i, raw_step in enumerate(raw_steps):
            label = f"Schritt {i + 1}"
            if not isinstance(raw_step, dict):
                raise StudioError(f"{label}: ungültig.")
            if raw_step.get("kind") == "preserve":
                # A hand-written step the guided builder cannot represent
                # (capture targets, template/area_name/entity_name targets,
                # non-catalogue arguments, ...) is carried over verbatim
                # from the saved spell - never rebuilt or silently dropped.
                index = raw_step.get("index")
                if type(index) is not int or not 0 <= index < len(old_steps) or not isinstance(old_steps[index], dict):
                    raise StudioError(f"{label}: die übernommene technische Definition existiert nicht mehr.")
                preserved = deepcopy(old_steps[index])
                alias = preserved.get("alias")
                if alias is not None:
                    if alias in seen_aliases:
                        raise StudioError(f"{label}: Name „{alias}“ ist bereits vergeben.")
                    seen_aliases.add(alias)
                steps.append(preserved)
                continue
            alias = raw_step.get("alias")
            if alias == "":
                alias = None
            if alias is not None:
                if not (isinstance(alias, str) and alias.isidentifier()):
                    raise StudioError(f"{label}: ungültiger Name.")
                if alias in seen_aliases:
                    raise StudioError(f"{label}: Name „{alias}“ ist bereits vergeben.")
                if alias in trigger_placeholders:
                    raise StudioError(f"{label}: Name „{alias}“ ist bereits ein Platzhalter im Trigger.")
                seen_aliases.add(alias)

            if raw_step.get("kind") == "action":
                action = action_choices.get(raw_step.get("action"))
                if action is None:
                    raise StudioError(f"{label}: bitte eine unterstützte Aktion auswählen.")
                target = self._resolve_step_target(raw_step.get("target_kind"), raw_step.get("target_value"), label=label)
                steps.append({"alias": alias, "operation": action["operation"],
                             "target": target, "arguments": deepcopy(action["arguments"])})
            elif raw_step.get("kind") == "query":
                raw_sources = raw_step.get("sources")
                if not isinstance(raw_sources, list) or not raw_sources:
                    raise StudioError(f"{label}: mindestens eine Datenquelle ist erforderlich.")
                sources = []
                for j, raw_source in enumerate(raw_sources):
                    source_label = f"{label}, Quelle {j + 1}"
                    if not isinstance(raw_source, dict):
                        raise StudioError(f"{source_label}: ungültig.")
                    source_kind = raw_source.get("kind")
                    if source_kind not in {"state", "attribute", "count", "min", "max", "avg", "sum", "names", "states"}:
                        raise StudioError(f"{source_label}: ungültige Art.")
                    is_aggregate = source_kind != "state" and source_kind != "attribute"
                    target_kind = raw_source.get("target_kind")
                    if target_kind == "entity_id" and is_aggregate:
                        raise StudioError(f"{source_label}: diese Auswertung benötigt ein Bereichs- oder Sammelziel.")
                    target = self._resolve_step_target(target_kind, raw_source.get("target_value"), label=source_label)
                    variable = raw_source.get("variable")
                    if not (isinstance(variable, str) and variable.isidentifier()):
                        raise StudioError(f"{source_label}: ungültiger Variablenname.")
                    source = {"kind": source_kind, "target": target, "variable": variable}
                    if raw_source.get("domain_filter"):
                        source["domain_filter"] = raw_source["domain_filter"]
                    if raw_source.get("device_class"):
                        source["device_class"] = raw_source["device_class"]
                    if source_kind == "attribute":
                        attribute = raw_source.get("attribute")
                        if not (isinstance(attribute, str) and attribute.strip()):
                            raise StudioError(f"{source_label}: Attributname erforderlich.")
                        source["attribute"] = attribute
                    if is_aggregate and raw_source.get("state_filter"):
                        source["state_filter"] = raw_source["state_filter"]
                    if source_kind == "state" and raw_source.get("unit_variable"):
                        unit_variable = raw_source["unit_variable"]
                        if not (isinstance(unit_variable, str) and unit_variable.isidentifier()):
                            raise StudioError(f"{source_label}: ungültiger Einheit-Variablenname.")
                        source["unit_variable"] = unit_variable
                    sources.append(source)
                steps.append({"alias": alias, "sources": sources})
            else:
                raise StudioError(f"{label}: unbekannte Schrittart.")

        output_mode = data.get("output_mode") or old.get("output_mode") or DEFAULT_OUTPUT_MODE
        if output_mode not in OUTPUT_MODES:
            raise StudioError("Ungültiger Output-Modus.")

        edit_language = data.get("trigger_language")
        first_trigger = triggers[0]
        if edit_language:
            previous = (old or {}).get("trigger")
            first_trigger = dict(previous) if isinstance(previous, dict) else ({data.get("default_language", edit_language): previous} if previous else {})
            first_trigger[edit_language] = triggers[0]
        elif old and isinstance(old.get("trigger"), dict):
            language = edit_language or next(iter(old["trigger"]))
            first_trigger = {**old["trigger"], language: first_trigger}

        result = deepcopy(old or {})
        for legacy_field in ("operation", "target", "arguments", "sources"):
            result.pop(legacy_field, None)
        result.update(id=data.get("id") or uuid.uuid4().hex, name=data.get("name") or triggers[0],
                      trigger=first_trigger, triggers=triggers[1:], steps=steps,
                      tags=data.get("tags", old.get("tags", [])), enabled=data.get("enabled", old.get("enabled", True)),
                      output_mode=output_mode)
        if edit_language:
            for field, value in (("name", data.get("name") or triggers[0]), ("description", data.get("description", "")),
                                 ("examples", data.get("examples", [])), ("triggers", triggers[1:]),
                                 ("output", data.get("output")), ("catchphrase", data.get("catchphrase")),
                                 ("failure_text", data.get("failure_text"))):
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
        return result

    def _resolve_action_target(self, raw_target, *, label, expert):
        """One action step's (or a flat single action's) `target` - the
        guided kinds via the existing, strictly-validated
        _resolve_step_target(), or (expert=True) any canonically valid
        target kind (including `template` Jinja and `entity_name`) with
        light shape checks only: the full document still has to pass
        _load_command's own engine-level validation on save (see write()),
        so this only needs to build a well-typed dict, not fully re-prove
        engine correctness."""
        if not isinstance(raw_target, dict):
            raise StudioError(f"{label}: ungültiges Ziel.")
        kind = raw_target.get("kind")
        if not expert:
            return self._resolve_step_target(kind, raw_target.get("value"), label=label)
        if kind not in _VALID_TARGET_KINDS:
            raise StudioError(f"{label}: unbekannte Ziel-Art „{kind}“.")
        target = {**deepcopy(raw_target), "kind": kind}
        if kind == "template":
            value = raw_target.get("value")
            if not (isinstance(value, str) and value.strip()):
                raise StudioError(f"{label}: bitte einen Jinja-Ausdruck für das Ziel angeben.")
            target["value"] = value
        elif kind in ("area_name", "entity_name"):
            capture = raw_target.get("capture")
            if capture is None and isinstance(raw_target.get("value"), str) and raw_target["value"].strip():
                return target
            if not (isinstance(capture, str) and capture.isidentifier()):
                raise StudioError(f"{label}: bitte einen gültigen Platzhalter-Namen für dieses Ziel angeben.")
            target["capture"] = capture
        elif kind in ("area_id", "entity_id", "entity_ids"):
            value = raw_target.get("value")
            if value is not None:
                target["value"] = value
        return target

    def _resolve_source(self, raw_source, *, label):
        """One query step's data source - accepts both the guided builder's
        existing flat `target_kind`/`target_value` wire shape (unchanged, so
        today's guided Combined-Actions-&-Queries step editor keeps working
        byte for byte) and, when `raw_source["expert"]` is set, a nested
        `target: {kind, value|capture}` shape that additionally allows a
        spoken area/entity-name capture per source - the one canonical
        `QuerySourceSpec` shape the guided builder still doesn't offer (see
        the original _resolve_step_target docstring); `sources` never allows
        a `template` target at all (spellbook_engine.py's _SINGLE_SOURCE_
        TARGET_KINDS/_AGGREGATE_SOURCE_TARGET_KINDS have no such entry), so
        unlike an action step this never needs raw Jinja handling."""
        if not isinstance(raw_source, dict):
            raise StudioError(f"{label}: ungültig.")
        source_kind = raw_source.get("kind")
        if source_kind not in _QUERY_SOURCE_KINDS:
            raise StudioError(f"{label}: ungültige Art.")
        is_aggregate = source_kind not in ("state", "attribute")
        expert = bool(raw_source.get("expert"))
        raw_target = raw_source.get("target")
        if isinstance(raw_target, dict):
            target_kind, target_value, capture = raw_target.get("kind"), raw_target.get("value"), raw_target.get("capture")
        else:
            target_kind = raw_source.get("target_kind")
            target_value = raw_source.get("target_value")
            capture = raw_source.get("capture")
        if target_kind == "entity_id" and is_aggregate:
            raise StudioError(f"{label}: diese Auswertung benötigt ein Bereichs- oder Sammelziel.")
        if expert and target_kind in ("area_name", "entity_name"):
            if target_kind == "entity_name" and is_aggregate:
                raise StudioError(f"{label}: „entity_name“ ist für Sammel-/Aggregatziele nicht zulässig.")
            if not capture and isinstance(target_value, str) and target_value.strip():
                target = {"kind": target_kind, "value": target_value}
            elif isinstance(capture, str) and capture.isidentifier():
                target = {"kind": target_kind, "capture": capture}
            else:
                raise StudioError(f"{label}: bitte einen gültigen Platzhalter-Namen für dieses Ziel angeben.")
        else:
            target = self._resolve_step_target(target_kind, target_value, label=label)
        variable = raw_source.get("variable")
        if not (isinstance(variable, str) and variable.isidentifier()):
            raise StudioError(f"{label}: ungültiger Variablenname.")
        # Extension fields and target options (e.g. fallback_source_area) must
        # survive an otherwise ordinary form edit. Canonical controls win.
        target = {**deepcopy(raw_source.get("target_extras", {})), **target}
        source = {**deepcopy(raw_source.get("source_extras", {})),
                  "kind": source_kind, "target": target, "variable": variable}
        if raw_source.get("domain_filter"):
            source["domain_filter"] = raw_source["domain_filter"]
        if raw_source.get("device_class"):
            source["device_class"] = raw_source["device_class"]
        if source_kind == "attribute":
            attribute = raw_source.get("attribute")
            if not (isinstance(attribute, str) and attribute.strip()):
                raise StudioError(f"{label}: Attributname erforderlich.")
            source["attribute"] = attribute
        if is_aggregate and raw_source.get("state_filter"):
            source["state_filter"] = raw_source["state_filter"]
        if source_kind == "state" and raw_source.get("unit_variable"):
            unit_variable = raw_source["unit_variable"]
            if not (isinstance(unit_variable, str) and unit_variable.isidentifier()):
                raise StudioError(f"{label}: ungültiger Einheit-Variablenname.")
            source["unit_variable"] = unit_variable
        return source

    def wizard_spell(self, data, old=None, *, stage: int = 4):
        """The single Wizard/Expert-shared step-list builder (Spellbook
        Studio unification, item 4/17) - replaces the user_spell()/
        steps_spell() split for new saves; both are kept only as the
        `mode: "expert"` flat-editor's own separate legacy/fallback-variant
        path (dispatch()'s existing `expert:`-block feature, unrelated to
        THIS unification - see _preserve_existing_user_variant).

        `stage` (0-4, matching the wizard page reached so far: Basic info,
        Actions & Queries, Trigger, Response, Review & Test) gates only
        the couple of checks that a trigger not yet being defined would
        otherwise fail: below stage 2 (the Trigger page), a missing/empty
        `triggers` list is tolerated instead of raising, and the "pure
        text output needs a response" check is deferred to stage >= 3 (the
        Response page). Every OTHER check in this function - and the
        actual field-level validation of the document this function
        assembles (describe_spell_basics, ActionExecutor.describe_shape
        via wizard_blocker_details, the trigger/capture/response loader
        checks in _load_command) - is exactly the same single
        implementation whether called for a real save (dispatch()'s
        "save_spell", always stage=4, byte-for-byte the same behavior as
        before this parameter existed) or for a per-wizard-step validation
        preview (core.studio_validation.validate_wizard_step, stage=the
        step being left). There is no second, separately-maintained
        fail-fast validation path for the final save - it runs the exact
        same assembly and is validated by the exact same shared checks,
        just gated to their full stage=4 scope.

        `data["steps"]` is a list of 0-12 step dicts. Each one is either
        `{"kind": "preserve", "index": N}` (a step this UI still can't fully
        rebuild - carried over byte for byte from the previous save, same
        escape hatch steps_spell() already had), a guided or expert-authored
        `{"kind": "action", ...}`, or a `{"kind": "query", "sources": [...]}`.

        The number of REMAINING steps alone decides the persisted shape -
        never which steps happen to be guided vs. expert-authored (see
        wizard_incompatibilities()'s module docstring for why that split
        needs no separate save format): 0 -> a flat text/output-only spell,
        1 -> a flat single action or a flat `sources:` query, 2-12 ->
        `steps:`. This is also the exact reverse of steps_spell()'s existing
        one-directional "flatten legacy fields away" migration - it now
        works both ways, and neither direction touches the database unless
        the user's own edit actually changes the step count."""
        old = deepcopy(old or {})
        draft = data.get("translations", {})
        if not isinstance(draft, dict) or set(draft) - set(TEXT_FIELDS + LIST_FIELDS):
            raise StudioError("Invalid translated content")
        old.update(deepcopy(draft))

        triggers = data.get("triggers")
        if stage >= 2:
            if not isinstance(triggers, list) or not triggers or not all(isinstance(t, str) and t.strip() for t in triggers):
                raise StudioError("Mindestens ein gesprochener Trigger ist erforderlich.")
        else:
            # Basic info / Actions & Queries (no Trigger step reached yet):
            # tolerate a missing/empty/invalid triggers list rather than
            # raising, so this same assembly still runs far enough for
            # Step 1's own validator to inspect id/name/tags/actions.
            # Every triggers[0] access below is guarded for the resulting
            # empty list.
            triggers = [t for t in triggers if isinstance(t, str) and t.strip()] if isinstance(triggers, list) else []

        raw_steps = data.get("steps")
        if not isinstance(raw_steps, list):
            raise StudioError("Ungültige Schrittliste.")
        if len(raw_steps) > _MAX_STEPS_PER_COMMAND:
            raise StudioError(f"Maximal {_MAX_STEPS_PER_COMMAND} Schritte sind erlaubt.")

        action_choices = {a["id"]: a for a in user_actions()}
        trigger_placeholders = {name for trigger in triggers for name in re.findall(r"\{(\w+)\}", trigger)}
        old_steps = as_logical_steps(old)
        seen_aliases = set()
        steps: list[dict] = []
        for i, raw_step in enumerate(raw_steps):
            label = f"Schritt {i + 1}"
            if not isinstance(raw_step, dict):
                raise StudioError(f"{label}: ungültig.")

            if raw_step.get("kind") == "preserve":
                index = raw_step.get("index")
                if type(index) is not int or not 0 <= index < len(old_steps):
                    raise StudioError(f"{label}: die übernommene technische Definition existiert nicht mehr.")
                preserved = deepcopy(old_steps[index])
                alias = preserved.get("alias")
                if alias is not None:
                    if alias in seen_aliases:
                        raise StudioError(f"{label}: Name „{alias}“ ist bereits vergeben.")
                    seen_aliases.add(alias)
                steps.append(preserved)
                continue

            alias = raw_step.get("alias")
            if alias == "":
                alias = None
            if alias is not None:
                if not (isinstance(alias, str) and alias.isidentifier()):
                    raise StudioError(f"{label}: ungültiger Name.")
                if alias in seen_aliases:
                    raise StudioError(f"{label}: Name „{alias}“ ist bereits vergeben.")
                if alias in trigger_placeholders:
                    raise StudioError(f"{label}: Name „{alias}“ ist bereits ein Platzhalter im Trigger.")
                seen_aliases.add(alias)

            if raw_step.get("kind") == "action":
                if raw_step.get("expert"):
                    operation = raw_step.get("operation")
                    if not isinstance(operation, str) or not operation:
                        raise StudioError(f"{label}: bitte eine Operation angeben.")
                    arguments = raw_step.get("arguments")
                    if not isinstance(arguments, dict):
                        raise StudioError(f"{label}: Argumente müssen ein Objekt sein.")
                    target = self._resolve_action_target(raw_step.get("target") or {}, label=label, expert=True)
                else:
                    action = action_choices.get(raw_step.get("action"))
                    if action is None:
                        raise StudioError(f"{label}: bitte eine unterstützte Aktion auswählen.")
                    target_kind = raw_step.get("target_kind")
                    # A spoken-area capture is only guided-representable for a
                    # flat single action (the ORIGINAL single-Wizard's own
                    # capability, one fixed capture name per operation via
                    # user_actions()'s target_capture) - _resolve_step_target's
                    # _STEP_TARGET_KINDS deliberately still rejects it for an
                    # actual multi-step list (see that method's docstring), so
                    # this must be handled here, before calling it.
                    if target_kind == "area_name" and len(raw_steps) == 1:
                        capture = raw_step.get("target_capture") or action.get("target_capture") or "area"
                        if not (isinstance(capture, str) and capture.isidentifier()):
                            capture = "area"
                        if raw_step.get("target_value") and not raw_step.get("target_capture"):
                            target = {"kind": "area_name", "value": raw_step["target_value"]}
                        else:
                            target = {"kind": "area_name", "capture": capture}
                            placeholder = "{" + capture + "}"
                            triggers = [t if placeholder in t else t + " " + placeholder for t in triggers]
                    else:
                        target = self._resolve_step_target(target_kind, raw_step.get("target_value"), label=label)
                    operation, arguments = action["operation"], deepcopy(action["arguments"])
                    target = {**deepcopy(raw_step.get("target_extras", {})), **target}
                    if operation == "light.set_color" and "color_arguments" in raw_step:
                        if not _is_freeform_light_color(raw_step["color_arguments"]):
                            raise StudioError(f"{label}: ungültige Farbe.")
                        arguments = deepcopy(raw_step["color_arguments"])
                steps.append({"alias": alias, "operation": operation, "target": target, "arguments": arguments})
            elif raw_step.get("kind") == "query":
                raw_sources = raw_step.get("sources")
                if not isinstance(raw_sources, list) or not raw_sources:
                    raise StudioError(f"{label}: mindestens eine Datenquelle ist erforderlich.")
                sources = [self._resolve_source(source, label=f"{label}, Quelle {j + 1}") for j, source in enumerate(raw_sources)]
                steps.append({"alias": alias, "sources": sources})
            else:
                raise StudioError(f"{label}: unbekannte Schrittart.")

        # Same required-capture rule user_spell() always applied - scoped to
        # a flat single action exactly like before (never extended to
        # steps_spell()'s multi-step case, which has no such check either).
        if len(steps) == 1 and "operation" in steps[0]:
            required_argument_captures = {
                spec["capture"] for spec in steps[0]["arguments"].values()
                if isinstance(spec, dict) and isinstance(spec.get("capture"), str)
            }
            for capture in sorted(required_argument_captures):
                if any("{" + capture + "}" not in trigger for trigger in triggers):
                    raise StudioError(f"Jede Trigger-Formulierung benötigt {{{capture}}} für die gewählte Aktion.")

        output_mode = data.get("output_mode") or old.get("output_mode") or DEFAULT_OUTPUT_MODE
        if output_mode not in OUTPUT_MODES:
            raise StudioError("Ungültiger Output-Modus.")

        assist_redirect = data.get("assist_redirect")
        if assist_redirect is not None and not isinstance(assist_redirect, bool):
            raise StudioError("Ungültiger assist_redirect-Wert.")

        edit_language = data.get("trigger_language")
        first_trigger = triggers[0] if triggers else ""
        if edit_language:
            previous = (old or {}).get("trigger")
            first_trigger = dict(previous) if isinstance(previous, dict) else ({data.get("default_language", edit_language): previous} if previous else {})
            if triggers:
                first_trigger[edit_language] = triggers[0]
        elif old and isinstance(old.get("trigger"), dict):
            language = edit_language or next(iter(old["trigger"]))
            first_trigger = {**old["trigger"], language: first_trigger}

        result = deepcopy(old or {})
        if "expert_variant" in data:
            variant = data["expert_variant"]
            if variant is None:
                result.pop("expert", None)
            elif isinstance(variant, dict):
                result["expert"] = deepcopy(variant)
            else:
                raise StudioError("Expert-Variante muss ein JSON-Objekt sein.")
        for legacy_field in ("operation", "target", "arguments", "sources", "steps", "assist_redirect", "alias"):
            result.pop(legacy_field, None)
        result.update(id=data.get("id") or uuid.uuid4().hex, name=data.get("name") or (triggers[0] if triggers else old.get("name", "")),
                      trigger=first_trigger, triggers=triggers[1:],
                      tags=data.get("tags", old.get("tags", [])), enabled=data.get("enabled", old.get("enabled", True)),
                      output_mode=output_mode)

        if len(steps) == 0:
            if assist_redirect is not None:
                result["assist_redirect"] = assist_redirect
        elif len(steps) == 1 and "sources" in steps[0]:
            result["sources"] = steps[0]["sources"]
        elif len(steps) == 1:
            result["operation"], result["target"], result["arguments"] = steps[0]["operation"], steps[0]["target"], steps[0]["arguments"]
        else:
            result["steps"] = steps
        if len(steps) == 1 and steps[0].get("alias") is not None:
            result["alias"] = steps[0]["alias"]

        if edit_language:
            for field, value in (
                ("name", data.get("name") or (triggers[0] if triggers else old.get("name", ""))),
                ("description", data.get("description", "")),
                                 ("examples", data.get("examples", [])), ("triggers", triggers[1:]),
                                 ("output", data.get("output")), ("catchphrase", data.get("catchphrase")),
                                 ("failure_text", data.get("failure_text"))):
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
        else:
            for field in ("description", "examples", "output", "catchphrase", "failure_text"):
                if field in data:
                    result[field] = deepcopy(data[field])
        # Whether *some* response exists is only decidable once both the
        # Actions & Queries step (are there any steps at all) and the
        # Response step (is there output/catchphrase) have been reached -
        # deferred to stage >= 3 so Step 0-2 validation of an otherwise
        # legitimate in-progress draft doesn't fail on a Response that
        # simply hasn't been written yet. Stage 4 (every real save) is
        # unaffected - stage defaults to 4.
        if stage >= 3 and not steps and not result.get("expert") and not result.get("output") and not result.get("catchphrase"):
            raise StudioError("Eine reine Textausgabe benötigt eine Antwort (Text oder gesprochene Antwort).")
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
            import_issues = _validation_issues(
                incoming,
                data.get("book_language", data.get("language", "en")),
            )
            if import_issues:
                raise StudioError(
                    "Import abgebrochen: Das Spellbook enthält statisch bewiesene "
                    "Sicherheits-Mehrdeutigkeiten."
                )
            revision = await self.write(incoming, None)
            return {"book_id": incoming["id"], "revision": revision, "report": report}
        if operation == "list":
            paths = await self.hass.async_add_executor_job(_list_book_files, self.hass)
            books = []
            for book_id in paths:
                raw, revision = await self.read(book_id)
                books.append({"id": book_id, "name": raw["name"], "count": len(raw["commands"]),
                              "disabled": raw.get("disabled", False),
                              "capability": book_capability(raw), "expert_allowed": self.expert_allowed(book_id),
                              "active_clients": len(self.profiles(book_id)),
                              "description": raw.get("description", ""), "metadata": raw.get("metadata", {}),
                              "revision": revision, "default_language": default_language(raw), "languages": languages(raw),
                              **await self.file_info(book_id, cover=False)})
            return {"books": books, "actions": user_actions(), "operations": list(OPERATION_CATALOG),
                    "operation_targets": {
                        name: sorted(spec.allowed_target_kinds)
                        for name, spec in OPERATION_CATALOG.items()
                    },
                    "operation_arguments": {
                        name: {
                            "required": {key: value.__name__ for key, value in spec.argument_types.items()},
                            "optional": {key: value.__name__ for key, value in spec.optional_argument_types.items()},
                            "variants": [
                                {key: value.__name__ for key, value in variant.items()}
                                for variant in spec.argument_variants
                            ],
                            "int_ranges": spec.int_ranges,
                            "float_ranges": spec.float_ranges,
                        }
                        for name, spec in OPERATION_CATALOG.items()
                    },
                    "output_modes": sorted(OUTPUT_MODES), "default_output_mode": DEFAULT_OUTPUT_MODE,
                    "query_source_capabilities": query_source_capabilities(),
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
        if operation == "expose_entity":
            entity_id = data.get("entity_id")
            if not isinstance(entity_id, str) or "." not in entity_id:
                raise StudioError("Ungültige Entity-ID.")
            from homeassistant.components.homeassistant.exposed_entities import (
                async_expose_entity,
            )
            async_expose_entity(self.hass, "conversation", entity_id, True)
            return {"ok": True}
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
                    # The single, authoritative "can the Wizard build and
                    # re-save this identically" verdict per spell - drives
                    # which editor a saved spell opens in (empty list) and
                    # the per-step "Expert" badges (see wizard_incompatibilities()).
                    "wizard_blockers": {c["id"]: wizard_incompatibilities(c) for c in raw["commands"]},
                    "wizard_blocker_details": {c["id"]: wizard_blocker_details(c) for c in raw["commands"]},
                    "wizard_step_flags": {c["id"]: wizard_step_flags(c) for c in raw["commands"]},
                    # "wizard" | "expert_only" | "invalid" per spell (see
                    # classify_spell) - the frontend's editor-mode-on-open
                    # routing should read this instead of re-deriving the
                    # three-way verdict from wizard_blockers itself.
                    "spell_classification": {c["id"]: classify_spell(c) for c in raw["commands"]},
                    "capability": book_capability(raw),
                    "mcp_report": mcp_capability_report(raw, mark_stale=True),
                    "collision_report": _collision_report(
                        raw, data.get("book_language", data.get("language", "en"))
                    ),
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
            allowed = {"name", "disabled", "description", "author", "version", "language", "languages", "default_language", "examples", "hase_min_version", "tags", "metadata"}
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
        if operation == "validate_step":
            step = data.get("step")
            if not isinstance(step, int) or isinstance(step, bool) or not 0 <= step <= 4:
                raise StudioError("Ungültiger Wizard-Schritt.")
            if data.get("scope") == "action_draft":
                draft_issues = _describe_action_draft(data.get("draft"))
                return {"ok": not draft_issues, "issues": draft_issues}
            spell_payload = data.get("spell")
            if not isinstance(spell_payload, dict):
                raise StudioError("Ungültiger Spell.")
            old = None
            if spell_payload.get("id"):
                old = next((s for s in raw["commands"] if s["id"] == spell_payload.get("id")), None)
            mode = data.get("mode")
            try:
                if mode in (None, "wizard"):
                    candidate = self.wizard_spell(spell_payload, old, stage=step)
                elif mode == "user":
                    candidate = self.user_spell(spell_payload, old)
                elif mode == "steps":
                    candidate = self.steps_spell(spell_payload, old)
                else:
                    candidate = _preserve_existing_user_variant(old, spell_payload, data.get("fallback_mode"))
            except StudioError as err:
                # A genuinely malformed wizard step-editor payload (not a
                # business-rule violation - those are what
                # validate_wizard_step's own field-level checks below
                # report) still can't be assembled into a candidate at
                # all; surfaced as a single structured issue rather than
                # propagating, so the wizard's Next button always gets a
                # normal {ok:false, issues:[...]} response.
                return {"ok": False, "issues": [{
                    "code": "invalid_step_data", "severity": "error", "path": "",
                    "message": str(err), "step_index": None, "classification": None,
                }]}
            owner_spell_id = candidate.get("id")
            issues = validate_wizard_step(step, candidate, book=raw, owner_spell_id=owner_spell_id)
            if step >= 4:
                safety_rows = _validation_issues(
                    _book_with_spell(raw, candidate),
                    data.get("book_language", data.get("language", "en")),
                    owner_spell_id=owner_spell_id,
                )
                issues = issues + [
                    {**row, "path": "", "step_index": None, "classification": None} for row in safety_rows
                ]
            return {"ok": not any(i.get("severity", "error") == "error" for i in issues), "issues": issues}
        if operation in {"validate", "dry_run", "execute", "explain"}:
            candidate = deepcopy(raw)
            # Studio checks and confirmed live tests use the current editor
            # candidate in memory. This does not persist anything.
            if data.get("spell") is not None:
                old = next((s for s in raw["commands"] if s["id"] == data["spell"].get("id")), None)
                if data.get("mode") == "wizard":
                    spell = self.wizard_spell(data["spell"], old)
                elif data.get("mode") == "user":
                    spell = self.user_spell(data["spell"], old)
                elif data.get("mode") == "steps":
                    spell = self.steps_spell(data["spell"], old)
                else:
                    spell = _preserve_existing_user_variant(
                        old, data["spell"], data.get("fallback_mode")
                    )
                candidate = _book_with_spell(raw, spell)
            payload = {**data, "context": context}
            if operation == "explain":
                phrase = _checked_phrase(data.get("phrase", ""))
                check_document(candidate)
                return {"match_explain": _explain_phrase(candidate, phrase, data.get("language", "de"))}
            if operation == "validate":
                # Editor diagnostics still describe the edited spell; safety
                # checks below use its complete candidate book.
                report_candidate = {**candidate, "commands": [spell]} if data.get("spell") is not None else candidate
                result = await self.report(report_candidate, payload)
                if data.get("spell") is not None:
                    # The ordinary editor report intentionally tests only the
                    # draft itself. Safety validation is different: it needs
                    # the complete book with this draft substituted so its
                    # collision partner remains visible.
                    edited_spell = spell
                    edited_id = edited_spell["id"]
                    safety_candidate = _book_with_spell(raw, edited_spell)

                    validation_language = data.get(
                        "book_language", data.get("language", "en")
                    )
                    result["validation_issues"] = _validation_issues(
                        safety_candidate,
                        validation_language,
                        owner_spell_id=edited_id,
                    )
                    result["errors"] = sum(
                        issue.get("severity") == "error"
                        for issue in result["validation_issues"]
                    )

                    # The live, unsaved candidate's own variable catalogue -
                    # lets the Wizard's Response step show accurate chips
                    # while the draft is still being edited, not just after
                    # a real save. Pure/no HA calls, same as spell_variables()
                    # itself.
                    result["spell_variables"] = spell_variables(edited_spell)
                    # The Wizard<->Expert conversion gate reuses the exact
                    # same classifier the editor-routing-on-open and the
                    # per-step Expert badges use - never a second, drifting
                    # definition of "Wizard-representable".
                    result["wizard_blockers"] = wizard_incompatibilities(edited_spell)
                    result["wizard_blocker_details"] = wizard_blocker_details(edited_spell)
                    result["wizard_step_flags"] = wizard_step_flags(edited_spell)
                    result["spell_classification"] = classify_spell(edited_spell)
                    result["editor_spell"] = edited_spell
                return result
            if operation == "execute" and data.get("confirmed") is not True:
                raise StudioError("Echte Ausführung muss bewusst bestätigt werden.")
            if operation == "execute" and raw.get("disabled") is True:
                raise StudioError("Dieses Spellbook ist deaktiviert. Bitte zuerst reaktivieren.")
            if operation == "execute" and data.get("revision") != revision:
                raise StudioError("Das Buch wurde geändert. Bitte erneut testen.")
            return await self.test(candidate, payload, execute=operation == "execute")
        if operation == "check_mcp":
            # "MCP prüfen" - always computed fresh (core.mcp_capability is
            # pure and re-derives the exact same core.mcp_projection.
            # project_command real tools/list uses, never a second
            # authorization model), mirroring "validate"'s own "always
            # recompute, never trust a stale cached verdict" contract for
            # the NUMBERS it reports. It also immediately PERSISTS that
            # fresh result onto every spell's own metadata.mcp via the
            # same write() every other save already goes through - the
            # "automatically saved status" the docs promise, so the book
            # on disk (and every other client reading it) reflects this
            # the instant the button is clicked, not only on the next
            # unrelated edit. The real execution path never reads this
            # cache regardless - see attach_mcp_capability_cache's own
            # docstring.
            new_revision = await self.write(deepcopy(raw), revision)
            raw, revision = await self.read(book_id)
            report = mcp_capability_report(raw, mark_stale=True)
            report["revision"] = new_revision
            return report
        if operation == "export":
            exported = export_book(raw, data.get("ids"), data.get("collection"))
            warnings = []
            for spell in exported["commands"]:
                for variant, path, definition, target in _spell_targets(spell):
                    if target.get("kind") in {"entity_id", "entity_ids", "area_id", "area_name", "template"} and "value" in target:
                        warnings.append({"id": spell["id"], "variant": variant, "path": path,
                                         "kind": target["kind"], "message": "Instanzabhängiges Ziel; vor Import prüfen."})
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
                    for variant, path, definition, target in _spell_targets(spell):
                        kind = target.get("kind")
                        if kind in {"area_id", "area_name"}:
                            candidates = areas
                            key = "name" if kind == "area_name" else "id"
                            category = "areas"
                        elif kind in {"entity_id", "entity_ids", "entity_name"}:
                            domain_filter = definition.get("domain_filter")
                            domains = [domain_filter] if isinstance(domain_filter, str) else self.operation_domains(definition.get("operation"))
                            candidates = self.entities(domains)
                            key = "name" if kind == "entity_name" else "id"
                            category = "entities"
                        else:
                            continue
                        available = [row[key] for row in candidates]
                        for value in _target_values(target):
                            if not isinstance(value, str) or value in available:
                                continue
                            choices = get_close_matches(value, available, n=3, cutoff=.35)
                            suggestions.append({
                                "id": spell["id"], "variant": variant, "path": path, "value": value,
                                "category": category, "key": key, "suggestions": choices,
                                "options": [{"id": row[key], "name": row["name"]} for row in candidates],
                            })
                return {"incoming": incoming, "conflicts": [s["id"] for s in incoming["commands"] if any(o["id"] == s["id"] for o in raw["commands"])],
                        "report": await self.report({**incoming, "id": book_id}, data), "suggestions": suggestions,
                        "expert_allowed": self.expert_allowed(book_id)}
            report = await self.report(merged, data)
            import_issues = _validation_issues(
                merged,
                data.get("book_language", data.get("language", "en")),
            )
            if import_issues:
                raise StudioError(
                    "Import abgebrochen: Das zusammengeführte Spellbook enthält "
                    "statisch bewiesene Sicherheits-Mehrdeutigkeiten."
                )
            revision = await self.write(merged, data.get("revision"))
            return {"revision": revision, "report": report}
        if operation == "delete_spells":
            # Bulk delete: all-or-nothing in ONE write/revision.  Unknown or
            # duplicate ids reject the whole request so the UI can never
            # report a partial deletion as done.
            ids = data.get("spell_ids")
            if (
                not isinstance(ids, list) or not ids
                or not all(isinstance(i, str) and i for i in ids)
                or len(set(ids)) != len(ids)
            ):
                raise StudioError("Ungültige Spell-Auswahl.")
            existing = {spell["id"] for spell in raw["commands"]}
            missing = [i for i in ids if i not in existing]
            if missing:
                raise StudioError("Mindestens ein ausgewählter Spell existiert nicht mehr. Es wurde nichts gelöscht.")
            if data.get("confirmed") is not True:
                raise StudioError("Bitte das Löschen der Auswahl bestätigen.")
            doomed = set(ids)
            raw["commands"] = [spell for spell in raw["commands"] if spell["id"] not in doomed]
            report = await self.report(raw, data)
            revision = await self.write(raw, data.get("revision"))
            return {"revision": revision, "report": report, "deleted": list(ids)}
        if operation in {"save_spell", "delete_spell", "duplicate_spell", "toggle_spell"}:
            sid = data.get("spell_id")
            index = next((i for i, s in enumerate(raw["commands"]) if s["id"] == sid), None)
            old = raw["commands"][index] if index is not None else None
            if operation == "save_spell":
                candidate = data.get("spell")
                if not isinstance(candidate, dict):
                    raise StudioError("Ungültiger Spell.")
                if data.get("mode") == "wizard":
                    spell = self.wizard_spell(candidate, old)
                elif data.get("mode") == "user":
                    spell = self.user_spell(candidate, old)
                elif data.get("mode") == "steps":
                    spell = self.steps_spell(candidate, old)
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

                # preserve_user() exists to stop the OLD flat editor from
                # *incidentally* dropping a spell's user-tier variant while
                # only editing its separate expert:-block fallback. The
                # unified Wizard/Expert step editor has no such incidental
                # path - a step's Expert-only fields are set explicitly and
                # on purpose (item 6) - and a `steps:`-shaped result can't
                # carry an `expert:` block at all (spellbook_engine.py
                # rejects `steps` + `expert` together), so applying it here
                # would corrupt a legitimately Expert-only multi-step spell.
                if data.get("mode") != "wizard":
                    spell = preserve_user(old, spell, remove_user=data.get("remove_user") is True)
                if old is not None:
                    spell["id"] = old["id"]
                    raw["commands"][index] = spell
                else:
                    raw["commands"].append(spell)

                # Interactive ownership rule: the spell being created/edited
                # owns newly exposed safety ambiguity. Existing peers remain
                # authoring-valid and are only referenced by this error.
                safety_confirmation = _owned_safety_confirmation(
                    raw,
                    spell["id"],
                    data.get("book_language", data.get("language", "en")),
                )
                if (
                    safety_confirmation is not None
                    and data.get("allow_safety_ambiguity") is not True
                ):
                    return safety_confirmation
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
