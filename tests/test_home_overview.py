"""core.home_overview: the read-only area/device listing behind the MCP
Bridge's built-in `home_overview` tool."""
import unittest

from custom_components.hase.core.home_overview import (
    OverviewArea,
    OverviewEntity,
    TOOL_NAME,
    input_schema,
    render_overview,
)
from custom_components.hase.core.models import AreaPolicy, ScopedAreaPolicy

AREAS = [
    OverviewArea("schlafzimmer", "Schlafzimmer", ("Mordor",)),
    OverviewArea("wohnzimmer", "Wohnzimmer"),
    OverviewArea("kueche", "Küche"),
]
ENTITIES = [
    OverviewEntity("light.nachttisch", "Nachttisch", "on", "schlafzimmer"),
    OverviewEntity("light.decke_schlaf", "Decke", "on", "schlafzimmer"),
    OverviewEntity("light.stehlampe", "Stehlampe", "off", "wohnzimmer"),
    OverviewEntity("cover.wohnzimmer", "Rollo", "open", "wohnzimmer"),
    OverviewEntity("sensor.temp", "Temperatur", "21", "wohnzimmer"),
    OverviewEntity("light.flur", "Flur", "on", None),
]
OPEN = AreaPolicy(allowed_areas=None, unrestricted=True)


def policy(actions=OPEN, queries=OPEN):
    return ScopedAreaPolicy(actions=actions, queries=queries)


class HomeOverviewTests(unittest.TestCase):
    def test_lists_areas_devices_and_states_in_german(self):
        text = render_overview(AREAS, ENTITIES, policy(), language="de")
        self.assertIn("- Schlafzimmer (auch genannt: Mordor)", text)
        self.assertIn("Nachttisch [light.nachttisch] an", text)
        self.assertIn("Stehlampe [light.stehlampe] aus", text)
        self.assertIn("Rollo [cover.wohnzimmer] offen", text)
        self.assertIn("- Küche\n  keine passenden Geräte", text)
        self.assertIn("Ohne Bereich", text)
        self.assertNotIn("sensor.temp", text)

    def test_query_policy_limits_areas_and_hides_unplaced_devices(self):
        restricted = AreaPolicy(allowed_areas=frozenset({"wohnzimmer"}))
        text = render_overview(AREAS, ENTITIES, policy(queries=restricted), language="de")
        self.assertIn("Wohnzimmer", text)
        self.assertNotIn("Schlafzimmer", text)
        self.assertNotIn("light.flur", text)

    def test_area_without_action_permission_is_marked_read_only(self):
        actions = AreaPolicy(allowed_areas=frozenset({"wohnzimmer"}))
        text = render_overview(AREAS, ENTITIES, policy(actions=actions), language="de")
        self.assertIn("- Schlafzimmer (auch genannt: Mordor) (nur lesen", text)
        self.assertIn("- Wohnzimmer\n", text)

    def test_filters_by_area_alias_and_domain(self):
        text = render_overview(AREAS, ENTITIES, policy(), language="de", area="mordor", domain="light")
        self.assertIn("light.nachttisch", text)
        self.assertNotIn("Wohnzimmer", text)
        self.assertNotIn("Ohne Bereich", text)

    def test_unknown_area_names_the_known_ones(self):
        text = render_overview(AREAS, ENTITIES, policy(), language="de", area="Keller")
        self.assertIn("Bereich 'Keller' nicht gefunden", text)
        self.assertIn("Schlafzimmer", text)

    def test_english_and_schema(self):
        self.assertIn("Lights:", render_overview(AREAS, ENTITIES, policy(), language="en"))
        schema = input_schema("de")
        self.assertEqual(schema["required"] if "required" in schema else [], [])
        self.assertEqual(TOOL_NAME, "home_overview")


if __name__ == "__main__":
    unittest.main()
