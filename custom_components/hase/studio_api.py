"""HA-authenticated Studio WebSocket commands and bundled sidebar panel."""
import logging
from pathlib import Path

import voluptuous as vol
from homeassistant.components import frontend, panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig

from .core.studio import StudioError
from .studio import SpellbookStudio
from .studio_access import StudioAccessControl

_LOGGER = logging.getLogger(__name__)

# Access-control config itself is the one deliberate management surface that
# stays admin-only even for a user who is otherwise fully authorized to use
# Studio - see studio_command()'s second gate below.
_ACCESS_MANAGEMENT_OPERATIONS = frozenset({"access_get", "access_set"})


@websocket_api.websocket_command({
    vol.Required("type"): "hase/studio",
    vol.Required("operation"): vol.In({"delete_book", "reset_book", "manage_language", "ui_settings", "list", "areas", "entities", "cover", "read", "create_book", "save_metadata", "expert_permission", "validate_all", "import_book_preview", "import_book_apply",
                                      "validate", "dry_run", "execute", "export", "import_preview",
                                      "import_apply", "save_spell", "delete_spell", "duplicate_spell", "toggle_spell",
                                      "access_get", "access_set"}),
    vol.Optional("data", default=dict): dict,
})
@websocket_api.async_response
async def studio_command(hass, connection, msg):
    # One canonical authorization choke point for the entire `hase/studio`
    # command, instead of scattering `is_admin`/authorization checks across
    # every operation branch. The authenticated connection's own user is the
    # only source of truth - never a frontend-supplied id.
    access = hass.data.get("hase_studio_access")
    if access is None or not access.is_authorized(connection.user):
        connection.send_error(msg["id"], "unauthorized", "Kein Zugriff auf HASE Studio.")
        return
    if msg["operation"] in _ACCESS_MANAGEMENT_OPERATIONS and not connection.user.is_admin:
        connection.send_error(msg["id"], "unauthorized", "Nur Administratoren dürfen die Zugriffsverwaltung ändern.")
        return
    try:
        if msg["operation"] == "access_get":
            result = await access.snapshot()
        elif msg["operation"] == "access_set":
            data = msg["data"]
            result = await access.async_set(data.get("mode"), data.get("allowed_user_ids", []), data.get("allowed_group_ids", []))
        else:
            studio = hass.data["hase_studio"]
            if not studio.entries:
                raise StudioError("Die HASE-Integration ist nicht geladen.")
            result = await studio.dispatch(msg["operation"], msg["data"], connection.context(msg))
    except StudioError as err:
        connection.send_error(msg["id"], "studio_validation", str(err))
    except Exception:
        _LOGGER.exception("Studio request failed")
        connection.send_error(msg["id"], "studio_error", "Die Anfrage konnte nicht abgeschlossen werden. Bitte Eingaben prüfen und erneut laden.")
    else:
        connection.send_result(msg["id"], result)


async def async_setup_studio(hass):
    if "hase_studio" in hass.data:
        return
    studio = SpellbookStudio(hass)
    await studio.async_load()
    access = StudioAccessControl(hass)
    await access.async_load()
    await hass.http.async_register_static_paths([
        StaticPathConfig("/hase-studio", str(Path(__file__).parent / "frontend"), False),
        StaticPathConfig("/hase-studio-brand", str(Path(__file__).parent / "brand"), False),
    ])
    await _register_panel(hass)
    hass.data["hase_studio"] = studio
    hass.data["hase_studio_access"] = access
    websocket_api.async_register_command(hass, studio_command)
    frontend.add_extra_js_url(hass, "/hase-studio/assist-entry.js?v=0.3.10-navfix")


async def _register_panel(hass):
    # require_admin=False: the route itself must be reachable by any
    # authenticated user so an ALL/CUSTOM-authorized non-admin can open it.
    # Real enforcement is studio_command()'s WebSocket-level check above -
    # this panel flag is not a security boundary.
    await panel_custom.async_register_panel(
        hass, frontend_url_path="hase", webcomponent_name="hase-spellbook-studio",
        sidebar_title="HASE" if hass.data.get("hase_studio_sidebar", False) else None,
        sidebar_icon="mdi:book-open-page-variant",
        module_url="/hase-studio/studio.js?v=0.3.16-access1", require_admin=False,
    )
    hass.data["hase_studio_panel"] = True


def async_update_panel(hass):
    """Change sidebar visibility without removing the /hase route."""
    frontend.async_register_built_in_panel(
        hass, component_name="custom", frontend_url_path="hase",
        sidebar_title="HASE" if hass.data.get("hase_studio_sidebar", False) else None,
        sidebar_icon="mdi:book-open-page-variant", require_admin=False, update=True,
        config={"_panel_custom": {"name": "hase-spellbook-studio", "embed_iframe": False,
                                  "trust_external": False,
                                  "module_url": "/hase-studio/studio.js?v=0.3.16-access1"}},
    )


async def async_entry_loaded(hass, entry):
    studio = hass.data["hase_studio"]
    if not hass.data.get("hase_studio_panel"):
        await _register_panel(hass)
    studio.entries.add(entry.entry_id)
    try:
        await studio.revalidate_entry(entry)
    except Exception:  # Diagnostics must not prevent an otherwise working client loading.
        _LOGGER.exception("Spellbook revalidation after reload failed")


def async_entry_unloaded(hass, entry):
    studio = hass.data["hase_studio"]
    studio.entries.discard(entry.entry_id)
    if not studio.entries and hass.data.get("hase_studio_panel"):
        frontend.async_remove_panel(hass, "hase")
        hass.data["hase_studio_panel"] = False
