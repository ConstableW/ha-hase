"""Pure authorization semantics for Studio access control - no `homeassistant` import required."""
import importlib
import unittest

import test_core_engine as core  # noqa: F401 - loads core/ as "hase_spellbook_core" once.

access_control = importlib.import_module("hase_spellbook_core.access_control")
ACCESS_MODE_ADMIN = access_control.ACCESS_MODE_ADMIN
ACCESS_MODE_ALL = access_control.ACCESS_MODE_ALL
ACCESS_MODE_CUSTOM = access_control.ACCESS_MODE_CUSTOM
DEFAULT_ACCESS_MODE = access_control.DEFAULT_ACCESS_MODE
AccessConfig = access_control.AccessConfig
access_config_to_storage = access_control.access_config_to_storage
access_reason = access_control.access_reason
default_access_config = access_control.default_access_config
is_authorized = access_control.is_authorized
load_access_config = access_control.load_access_config
sanitize_ids = access_control.sanitize_ids


def check(*, is_admin=False, user_id="u1", group_ids=(), config):
    return is_authorized(is_admin=is_admin, user_id=user_id, group_ids=group_ids, config=config)


def reason(*, is_admin=False, user_id="u1", group_ids=(), config):
    return access_reason(is_admin=is_admin, user_id=user_id, group_ids=group_ids, config=config)


class AllModeTests(unittest.TestCase):
    config = AccessConfig(mode=ACCESS_MODE_ALL)

    def test_admin_allowed(self):
        self.assertTrue(check(is_admin=True, config=self.config))
        self.assertEqual(reason(is_admin=True, config=self.config), "admin")

    def test_normal_user_allowed(self):
        self.assertTrue(check(is_admin=False, config=self.config))
        self.assertEqual(reason(is_admin=False, config=self.config), "all")


class AdminModeTests(unittest.TestCase):
    config = AccessConfig(mode=ACCESS_MODE_ADMIN)

    def test_admin_allowed(self):
        self.assertTrue(check(is_admin=True, config=self.config))

    def test_normal_user_denied(self):
        self.assertFalse(check(is_admin=False, config=self.config))
        self.assertEqual(reason(is_admin=False, config=self.config), "denied")


class CustomModeTests(unittest.TestCase):
    config = AccessConfig(
        mode=ACCESS_MODE_CUSTOM,
        allowed_user_ids=frozenset({"u-allowed"}),
        allowed_group_ids=frozenset({"g-family"}),
    )

    def test_admin_always_allowed(self):
        self.assertTrue(check(is_admin=True, user_id="anyone", config=self.config))
        self.assertEqual(reason(is_admin=True, user_id="anyone", config=self.config), "admin")

    def test_explicitly_allowed_user(self):
        self.assertTrue(check(user_id="u-allowed", config=self.config))
        self.assertEqual(reason(user_id="u-allowed", config=self.config), "user")

    def test_user_in_allowed_group(self):
        self.assertTrue(check(user_id="unlisted", group_ids=["g-family"], config=self.config))
        self.assertEqual(reason(user_id="unlisted", group_ids=["g-family"], config=self.config), "group")

    def test_unrelated_user_denied(self):
        self.assertFalse(check(user_id="stranger", group_ids=["g-other"], config=self.config))
        self.assertEqual(reason(user_id="stranger", group_ids=["g-other"], config=self.config), "denied")

    def test_removed_user_id_is_safe(self):
        # An id that no longer matches any real HA user simply never matches.
        self.assertFalse(check(user_id="deleted-user-id", config=self.config))

    def test_removed_group_id_is_safe(self):
        self.assertFalse(check(user_id="someone", group_ids=["deleted-group-id"], config=self.config))

    def test_explicit_user_and_group_membership_combine(self):
        self.assertTrue(check(user_id="u-allowed", group_ids=["deleted-group-id"], config=self.config))


class ConfigParsingTests(unittest.TestCase):
    def test_default_is_admin_only(self):
        self.assertEqual(DEFAULT_ACCESS_MODE, ACCESS_MODE_ADMIN)
        self.assertEqual(default_access_config(), AccessConfig(mode=ACCESS_MODE_ADMIN))

    def test_missing_stored_config_falls_back_to_default(self):
        self.assertEqual(load_access_config(None), default_access_config())
        self.assertEqual(load_access_config({}), default_access_config())

    def test_malformed_stored_config_is_tolerated(self):
        config = load_access_config({"mode": "not-a-real-mode", "allowed_user_ids": "not-a-list", "allowed_group_ids": 42})
        self.assertEqual(config.mode, ACCESS_MODE_ADMIN)
        self.assertEqual(config.allowed_user_ids, frozenset())
        self.assertEqual(config.allowed_group_ids, frozenset())

    def test_roundtrip_through_storage(self):
        config = AccessConfig(mode=ACCESS_MODE_CUSTOM, allowed_user_ids=frozenset({"b", "a"}), allowed_group_ids=frozenset({"g2", "g1"}))
        stored = access_config_to_storage(config)
        self.assertEqual(stored, {"mode": "custom", "allowed_user_ids": ["a", "b"], "allowed_group_ids": ["g1", "g2"]})
        self.assertEqual(load_access_config(stored), config)

    def test_non_string_entries_in_lists_are_dropped(self):
        config = load_access_config({"mode": "custom", "allowed_user_ids": ["ok", 1, None, ""], "allowed_group_ids": ["g", 2]})
        self.assertEqual(config.allowed_user_ids, frozenset({"ok"}))
        self.assertEqual(config.allowed_group_ids, frozenset({"g"}))


class SanitizeIdsTests(unittest.TestCase):
    def test_drops_unknown_ids(self):
        self.assertEqual(sanitize_ids(["a", "b", "gone"], {"a", "b"}), frozenset({"a", "b"}))

    def test_empty_known_ids_drops_everything(self):
        self.assertEqual(sanitize_ids(["a"], set()), frozenset())


if __name__ == "__main__":
    unittest.main()
