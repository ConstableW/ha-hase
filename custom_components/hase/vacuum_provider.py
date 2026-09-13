"""Vacuum providers: declarative descriptions of how to discover a robot
vacuum's rooms and how to start cleaning one, used by the shared
`vacuum.clean_area` operation through HaServiceCaller.

Why this exists as its own pluggable layer rather than code27 just calling
`roborock.get_maps` / `vacuum.send_command` directly: every vacuum brand's
Home Assistant integration has its own discovery service and its own
command vocabulary for "clean this one room" - there's no shared HA
concept for room-level cleaning the way there's a shared `light.turn_on`.
Hardcoding "roborock" into bridge.py would mean anyone with a different
brand (or Home Assistant adding one later) needs a code change; a provider
file needs a YAML addition instead - the same reasoning as spellbooks
being YAML instead of a hardcoded phrase table.

Provider files are discovered the same way spellbooks are: bundled under
vacuum_providers/*.yaml next to this file, and user-authored under
<config>/hase_vacuum_providers/*.yaml (user directory wins on an id
collision). All installed providers are tried when resolving a
"vacuum.clean_area" operation - a house can mix vacuum brands.

Schema (see vacuum_providers/roborock.yaml for the reference example):
  id, name:      like a spellbook's.
  platform:      the entity registry `platform` string (the integration
                 domain that set up the entity) used to find this
                 provider's vacuum entities - e.g. "roborock". Entities are
                 always discovered dynamically from the registry by this
                 match, never hardcoded by entity_id.
  discover:      how to fetch room/segment data. `source` (default
                 "service") picks which of three shapes the rest of this
                 section has:
    source: service (default) - domain, service: the HA service to call
                      (e.g. roborock.get_maps), with `target: {entity_id:
                      [<all this provider's vacuum entities>]}` and
                      return_response: true.
      maps_key, flag_key, name_key, rooms_key:
                      the response is expected to be a dict keyed by
                      vacuum entity_id, each value having `maps_key`
                      (a list), each map having `flag_key` (an opaque
                      per-map identifier), `name_key` (a human map/floor
                      label) and `rooms_key` (a dict of
                      segment_id -> room_name). Configurable rather than
                      hardcoded so a provider with the same general shape
                      under different field names doesn't need new
                      Python - a fundamentally different response shape
                      still would.
    source: entity_attributes - for a vendor with no dedicated discovery
                      service (e.g. Dreame), room data instead lives
                      directly on each vacuum entity's own state
                      attributes: entity_domain (normally "vacuum"),
                      rooms_key (the attribute holding the room list/
                      dict), room_id_key and room_name_key (the fields
                      identifying each room within it).
    source: unavailable - an honest placeholder for a vendor with no
                      discoverable room mapping at all as of this
                      writing (e.g. Xiaomi Miio, per HA's own docs,
                      which points users at a separate `miiocli`
                      command-line tool instead). Loads as a valid
                      provider that simply never contributes a room
                      match, rather than being rejected as broken -
                      `reason` is free-text documentation only.
  select_map:    optional. If the vacuum has more than one map (e.g. one
                 per floor) and room-cleaning only works on whichever map
                 is currently active, this names the select entity (by
                 its registry `translation_key`, resolved per-vacuum via
                 device_id - never a hardcoded entity_id, same reasoning
                 as everywhere else in this file) to switch first. Its
                 options are expected to match `name_key` values.
  clean:         how to start cleaning one segment.
    domain, service:  the HA service to call (e.g. vacuum.send_command).
    data:             a template dict merged into the service call data.
                      Any value that is exactly the string "{segment_id}"
                      is substituted with the resolved segment id as an
                      int (falling back to the raw string if it isn't
                      numeric) rather than a naive string .format() - the
                      device's own API expects a real number, not "17".
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

BUILTIN_DIR = Path(__file__).parent / "vacuum_providers"
USER_SUBDIR = "hase_vacuum_providers"


@dataclass
class VacuumProvider:
    id: str
    name: str
    platform: str
    discover: dict[str, Any]
    clean: dict[str, Any]
    select_map: dict[str, Any] | None = None


def _valid_service_ref(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("domain"), str) and bool(value["domain"].strip())
        and isinstance(value.get("service"), str) and bool(value["service"].strip())
    )


def _valid_discover(discover: Any, provider_id: str) -> bool:
    """discover.source picks which of three shapes the rest of discover
    must have - default "service" is the original Roborock-style
    get_maps call; "entity_attributes" reads room data straight off a
    vacuum entity's own state attributes (no dedicated discovery service
    exists for every vendor, e.g. Dreame); "unavailable" is an honest
    placeholder for a vendor with no discoverable room mapping at all
    (e.g. Xiaomi Miio, per HA's own docs) - such a provider loads fine
    and is simply never able to contribute a room match, rather than
    being rejected outright as a broken config."""
    if not isinstance(discover, dict):
        _LOGGER.warning("code27 vacuum provider %r: discover missing, skipping", provider_id)
        return False
    source = discover.get("source", "service")

    if source == "service":
        if not _valid_service_ref(discover):
            _LOGGER.warning(
                "code27 vacuum provider %r: discover.domain/service missing, skipping",
                provider_id,
            )
            return False
        for key in ("maps_key", "flag_key", "name_key", "rooms_key"):
            if not (isinstance(discover.get(key), str) and discover[key].strip()):
                _LOGGER.warning(
                    "code27 vacuum provider %r: discover.%s missing, skipping", provider_id, key
                )
                return False
        return True

    if source == "entity_attributes":
        for key in ("entity_domain", "rooms_key", "room_id_key", "room_name_key"):
            if not (isinstance(discover.get(key), str) and discover[key].strip()):
                _LOGGER.warning(
                    "code27 vacuum provider %r: discover.%s missing, skipping", provider_id, key
                )
                return False
        return True

    if source == "unavailable":
        return True

    _LOGGER.warning(
        "code27 vacuum provider %r: unknown discover.source %r, skipping", provider_id, source
    )
    return False


def _load_provider(raw: dict) -> VacuumProvider | None:
    if not isinstance(raw, dict):
        return None
    provider_id = raw.get("id")
    name = raw.get("name")
    platform = raw.get("platform")
    discover = raw.get("discover")
    clean = raw.get("clean")
    select_map = raw.get("select_map")

    if not (isinstance(provider_id, str) and provider_id.strip()):
        _LOGGER.warning("code27 vacuum provider: missing id, skipping: %r", raw)
        return None
    if not (isinstance(name, str) and name.strip()):
        _LOGGER.warning("code27 vacuum provider %r: missing name, skipping", provider_id)
        return None
    if not (isinstance(platform, str) and platform.strip()):
        _LOGGER.warning("code27 vacuum provider %r: missing platform, skipping", provider_id)
        return None
    if not _valid_discover(discover, provider_id):
        return None
    if not _valid_service_ref(clean):
        _LOGGER.warning(
            "code27 vacuum provider %r: clean.domain/service missing, skipping", provider_id
        )
        return None
    if not isinstance(clean.get("data"), dict):
        _LOGGER.warning(
            "code27 vacuum provider %r: clean.data must be a mapping, skipping", provider_id
        )
        return None
    if select_map is not None and not (
        isinstance(select_map, dict)
        and isinstance(select_map.get("translation_key"), str)
        and select_map["translation_key"].strip()
    ):
        _LOGGER.warning(
            "code27 vacuum provider %r: select_map.translation_key missing, ignoring select_map",
            provider_id,
        )
        select_map = None

    return VacuumProvider(
        id=provider_id,
        name=name,
        platform=platform,
        discover=discover,
        clean=clean,
        select_map=select_map,
    )


def _spellbook_style_dirs(hass: HomeAssistant) -> list[Path]:
    return [BUILTIN_DIR, Path(hass.config.path(USER_SUBDIR))]


def _list_provider_files(hass: HomeAssistant) -> dict[str, Path]:
    files: dict[str, Path] = {}
    for directory in _spellbook_style_dirs(hass):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.yaml")) + sorted(directory.glob("*.yml")):
            try:
                raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("code27 vacuum provider: could not parse %s: %s", path, err)
                continue
            provider_id = raw.get("id") if isinstance(raw, dict) else None
            if not provider_id:
                continue
            files[provider_id] = path
    return files


async def async_list_providers(hass: HomeAssistant) -> list[VacuumProvider]:
    """All installed, valid vacuum providers - a house can mix brands, so
    (unlike spellbooks, which are one-selected-at-a-time) every provider
    found is returned and all of them are tried when resolving an area."""

    def _load() -> list[VacuumProvider]:
        providers = []
        for path in _list_provider_files(hass).values():
            try:
                raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("code27 vacuum provider: could not parse %s: %s", path, err)
                continue
            provider = _load_provider(raw)
            if provider is not None:
                providers.append(provider)
        return providers

    return await hass.async_add_executor_job(_load)


def resolve_clean_service_data(provider: VacuumProvider, segment_id: str) -> dict[str, Any]:
    """Build the clean.data payload, substituting the literal placeholder
    "{segment_id}" with the resolved segment id as an int where possible -
    the device APIs seen so far (Roborock) expect a real number in the
    params list, not a string, so this is a typed substitution, not a
    generic .format() that would always produce a string."""
    try:
        typed_segment_id: Any = int(segment_id)
    except (TypeError, ValueError):
        typed_segment_id = segment_id

    def _substitute(value: Any) -> Any:
        if value == "{segment_id}":
            return typed_segment_id
        if isinstance(value, list):
            return [_substitute(v) for v in value]
        if isinstance(value, dict):
            return {k: _substitute(v) for k, v in value.items()}
        return value

    return _substitute(provider.clean["data"])
