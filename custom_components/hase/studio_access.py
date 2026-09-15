"""Home Assistant adapter for Studio access control.

Wraps `core.access_control`'s pure authorization semantics with Home
Assistant's real users/groups (`hass.auth`) and persistence (`Store`), the
same split `studio.py`/`core/studio.py` already use for Spellbook business
logic. This module never becomes a second account system: every id it stores
is a stable Home Assistant user or group id.
"""
from __future__ import annotations

from .core.access_control import (
    ACCESS_MODES,
    AccessConfig,
    access_config_to_storage,
    access_reason,
    default_access_config,
    is_authorized,
    load_access_config,
    sanitize_ids,
)
from .core.studio import StudioError


class StudioAccessControl:
    def __init__(self, hass):
        from homeassistant.helpers.storage import Store
        self.hass = hass
        self.store = Store(hass, 1, "hase.studio_access")
        self.config: AccessConfig = default_access_config()

    async def async_load(self):
        self.config = load_access_config(await self.store.async_load())

    def is_authorized(self, user) -> bool:
        if user is None:
            return False
        return is_authorized(
            is_admin=bool(getattr(user, "is_admin", False)),
            user_id=user.id,
            group_ids=[g.id for g in user.groups],
            config=self.config,
        )

    async def _known_users(self):
        users = await self.hass.auth.async_get_users()
        return [u for u in users if not u.system_generated]

    async def snapshot(self) -> dict:
        users = await self._known_users()
        groups: dict[str, dict] = {}
        for user in users:
            for group in user.groups:
                row = groups.setdefault(group.id, {"id": group.id, "name": group.name, "member_count": 0})
                row["member_count"] += 1

        group_rows = [
            {**row, "allowed": row["id"] in self.config.allowed_group_ids}
            for row in sorted(groups.values(), key=lambda r: r["name"].casefold())
        ]

        user_rows = []
        for user in users:
            group_ids = [g.id for g in user.groups]
            reason = access_reason(is_admin=user.is_admin, user_id=user.id, group_ids=group_ids, config=self.config)
            user_rows.append({
                "id": user.id, "name": user.name, "is_admin": user.is_admin,
                "group_ids": group_ids, "access": reason,
                "allowed": user.id in self.config.allowed_user_ids,
            })
        user_rows.sort(key=lambda row: (not row["is_admin"], row["name"].casefold()))

        return {"config": access_config_to_storage(self.config), "groups": group_rows, "users": user_rows}

    async def async_set(self, mode, allowed_user_ids, allowed_group_ids) -> dict:
        if mode not in ACCESS_MODES:
            raise StudioError("Ungültiger Zugriffsmodus.")
        if not isinstance(allowed_user_ids, list) or any(not isinstance(v, str) for v in allowed_user_ids):
            raise StudioError("Ungültige Benutzerliste.")
        if not isinstance(allowed_group_ids, list) or any(not isinstance(v, str) for v in allowed_group_ids):
            raise StudioError("Ungültige Gruppenliste.")

        users = await self._known_users()
        known_user_ids = {u.id for u in users}
        known_group_ids = {g.id for u in users for g in u.groups}

        self.config = AccessConfig(
            mode=mode,
            allowed_user_ids=sanitize_ids(allowed_user_ids, known_user_ids),
            allowed_group_ids=sanitize_ids(allowed_group_ids, known_group_ids),
        )
        await self.store.async_save(access_config_to_storage(self.config))
        return await self.snapshot()
