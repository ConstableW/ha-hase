"""Shared trigger regressions retained from the former frontend matcher.

Per-word fuzzy matching and full consumption now exercise core/matching.py.
"""
import unittest

import test_bridge_delivery as support

spellbook = support.matching


class MultiwordLiteralFuzzyMatchTests(unittest.TestCase):
    def test_does_not_fuzzy_match_on_shared_prefix_alone(self):
        segments = spellbook.compile_trigger("löscht die feuer von mittelerde")
        text = "löscht die feuer von Gondor"
        tokens = spellbook.tokenize_with_spans(text)
        self.assertIsNone(spellbook.match_segments(segments, tokens, text))

    def test_still_tolerates_a_single_word_typo(self):
        segments = spellbook.compile_trigger("löscht die feuer von mittelerde")
        text = "löscht die feur von mittelerde"  # "feuer" -> "feur"
        tokens = spellbook.tokenize_with_spans(text)
        result = spellbook.match_segments(segments, tokens, text)
        self.assertIsNotNone(result)
        self.assertFalse(result.is_exact)


class FullConsumptionTests(unittest.TestCase):
    def test_extra_trailing_word_is_rejected(self):
        # The exact reproduction from the second-round audit's new
        # earlier finding, against the shared matcher -
        # "lumos maxima Küche" must not match the area-less "lumos
        # maxima" trigger by silently dropping "Küche".
        segments = spellbook.compile_trigger("lumos maxima")
        text = "lumos maxima Küche"
        tokens = spellbook.tokenize_with_spans(text)
        self.assertIsNone(spellbook.match_segments(segments, tokens, text))

    def test_exact_full_match_still_works(self):
        segments = spellbook.compile_trigger("lumos {area}")
        text = "lumos Küche"
        tokens = spellbook.tokenize_with_spans(text)
        result = spellbook.match_segments(segments, tokens, text)
        self.assertIsNotNone(result)
        self.assertEqual(result.captures, {"area": "Küche"})
        self.assertTrue(result.is_exact)


if __name__ == "__main__":
    unittest.main()
