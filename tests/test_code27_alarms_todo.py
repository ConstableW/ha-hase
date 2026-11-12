"""Alarm to-do list text format, alarm/memo attributes, alarm-switch cleanup."""
from __future__ import annotations

import ast
import unittest
from pathlib import Path
from types import SimpleNamespace as N

PROVIDER = Path(__file__).resolve().parents[1] / "custom_components" / "hase" / "providers" / "code27"


def _exec_pure(path: Path, names: set[str], namespace: dict) -> dict:
    """Run only the named top-level statements of a module (no HA imports)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    keep = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.Assign, ast.AnnAssign, ast.For)):
            targets = []
            if isinstance(node, ast.FunctionDef):
                targets = [node.name]
            elif isinstance(node, ast.Assign):
                targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                targets = [node.target.id]
            elif isinstance(node, ast.For):
                targets = ["__loop__"]
            if (set(targets) & names) or isinstance(node, ast.For):
                keep.append(node)
    exec(compile(ast.Module(body=keep, type_ignores=[]), str(path), "exec"), namespace)
    return namespace


const = _exec_pure(PROVIDER / "const.py", {"ALARM_DAY_BITS", "ALARM_WEEK_ORDER", "alarm_days"}, {})
todo = _exec_pure(
    PROVIDER / "todo.py",
    {"_DAY_ALIASES", "_DAILY_WORDS", "_ONCE_WORDS", "_DAY_LABELS", "_TIME_PREFIX",
     "parse_alarm_title", "parse_alarm_days", "format_alarm_days"},
    {"re": __import__("re"), "ALARM_DAY_BITS": const["ALARM_DAY_BITS"],
     "ALARM_WEEK_ORDER": const["ALARM_WEEK_ORDER"], "alarm_days": const["alarm_days"],
     "_DAY_ALIASES": {}},
)
parse_title, parse_days, fmt_days = (
    todo["parse_alarm_title"], todo["parse_alarm_days"], todo["format_alarm_days"]
)
BITS = const["ALARM_DAY_BITS"]


class AlarmTitleTests(unittest.TestCase):
    def test_time_and_label(self):
        self.assertEqual(parse_title("07:30 Aufstehen"), (7, 30, "Aufstehen"))
        self.assertEqual(parse_title("7.05"), (7, 5, ""))
        self.assertEqual(parse_title("  23:59   Spät  "), (23, 59, "Spät"))

    def test_missing_or_invalid_time_is_rejected(self):
        for bad in ("Aufstehen", "", "25:00 x", "12:60"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_title(bad)


class AlarmDaysTests(unittest.TestCase):
    def test_words(self):
        self.assertEqual(parse_days("täglich"), 127)
        self.assertEqual(parse_days("Daily"), 127)
        self.assertEqual(parse_days("einmalig"), 0)
        self.assertEqual(parse_days(None), 0)
        self.assertEqual(parse_days(""), 0)

    def test_german_and_english_names(self):
        self.assertEqual(parse_days("Mo Mi Fr"), BITS["mon"] | BITS["wed"] | BITS["fri"])
        self.assertEqual(parse_days("sat, sun"), BITS["sat"] | BITS["sun"])

    def test_ranges_including_wrap_around(self):
        weekdays = sum(BITS[d] for d in ("mon", "tue", "wed", "thu", "fri"))
        self.assertEqual(parse_days("Mo-Fr"), weekdays)
        self.assertEqual(parse_days("Sa-Mo"), BITS["sat"] | BITS["sun"] | BITS["mon"])

    def test_unknown_token_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_days("Mo Xx")
        with self.assertRaises(ValueError):
            parse_days("Mo-Xx")

    def test_format_round_trips(self):
        for lang in ("de", "en"):
            for mask in (0, 127, BITS["mon"] | BITS["wed"], BITS["sat"] | BITS["sun"]):
                with self.subTest(lang=lang, mask=mask):
                    self.assertEqual(parse_days(fmt_days(mask, lang)), mask)
        self.assertEqual(fmt_days(0, "de"), "einmalig")
        self.assertEqual(fmt_days(127, "en"), "daily")
        self.assertEqual(fmt_days(BITS["mon"] | BITS["tue"], "de"), "Mo Di")


class AlarmDaysFromMaskTests(unittest.TestCase):
    def test_week_order_starts_on_monday(self):
        self.assertEqual(const["alarm_days"](BITS["sun"] | BITS["mon"]), ["mon", "sun"])
        self.assertEqual(const["alarm_days"](0), [])


if __name__ == "__main__":
    unittest.main()
