"""Studio access-control contract - v1.

Determines who may open the Spellbook Studio, independent of the existing
per-book Expert-execution permission and independent of sidebar visibility.
Home Assistant's own users/groups are the source of truth; this module never
invents a separate HASE account system, and never crashes on a stale id a
user or group was deleted after being granted access.

No `homeassistant` import anywhere in this module.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

ACCESS_MODE_ALL = "all"
ACCESS_MODE_ADMIN = "admin"
ACCESS_MODE_CUSTOM = "custom"
ACCESS_MODES = frozenset({ACCESS_MODE_ALL, ACCESS_MODE_ADMIN, ACCESS_MODE_CUSTOM})

# Matches today's de-facto behavior (Studio was admin-only before this
# feature existed), so an update never unexpectedly locks anyone out or
# widens access until an administrator explicitly opens Nutzerverwaltung
# and changes it.
DEFAULT_ACCESS_MODE = ACCESS_MODE_ADMIN

AccessReason = Literal["admin", "all", "user", "group", "denied"]


@dataclass(frozen=True)
class AccessConfig:
    mode: str = DEFAULT_ACCESS_MODE
    allowed_user_ids: frozenset[str] = frozenset()
    allowed_group_ids: frozenset[str] = frozenset()


def default_access_config() -> AccessConfig:
    return AccessConfig()


def _string_set(value) -> frozenset[str]:
    if not isinstance(value, list):
        return frozenset()
    return frozenset(v for v in value if isinstance(v, str) and v)


def load_access_config(raw: dict | None) -> AccessConfig:
    """Tolerant parsing of the stored config. Never raises."""
    if not isinstance(raw, dict):
        return default_access_config()
    mode = raw.get("mode")
    if mode not in ACCESS_MODES:
        mode = DEFAULT_ACCESS_MODE
    return AccessConfig(
        mode=mode,
        allowed_user_ids=_string_set(raw.get("allowed_user_ids")),
        allowed_group_ids=_string_set(raw.get("allowed_group_ids")),
    )


def access_config_to_storage(config: AccessConfig) -> dict:
    return {
        "mode": config.mode,
        "allowed_user_ids": sorted(config.allowed_user_ids),
        "allowed_group_ids": sorted(config.allowed_group_ids),
    }


def sanitize_ids(ids: Iterable[str], known_ids: Iterable[str]) -> frozenset[str]:
    """Drop ids that no longer refer to a real HA user/group.

    Used only when saving new configuration, so a deleted user or group is
    cleaned up the next time an administrator touches the settings, without
    ever needing to treat a stale id found elsewhere as an error.
    """
    known = set(known_ids)
    return frozenset(i for i in ids if i in known)


def is_authorized(*, is_admin: bool, user_id: str, group_ids: Iterable[str], config: AccessConfig) -> bool:
    """Deterministic Studio authorization. See access_reason() for the "why"."""
    return access_reason(is_admin=is_admin, user_id=user_id, group_ids=group_ids, config=config) != "denied"


def access_reason(*, is_admin: bool, user_id: str, group_ids: Iterable[str], config: AccessConfig) -> AccessReason:
    if is_admin:
        return "admin"
    if config.mode == ACCESS_MODE_ALL:
        return "all"
    if config.mode == ACCESS_MODE_ADMIN:
        return "denied"
    if config.mode == ACCESS_MODE_CUSTOM:
        if user_id in config.allowed_user_ids:
            return "user"
        if set(group_ids) & config.allowed_group_ids:
            return "group"
        return "denied"
    return "denied"
