"""Regression coverage for the legacy spellbook.py matcher receiving
the same two fixes as core/matching.py (second-round audit + explicit
follow-up request to port them): per-word fuzzy matching instead of a
whole-phrase blob, and full-consumption of the tokenized message.
"""
import unittest

import test_bridge_delivery as support

spellbook = support.spellbook


class MultiwordLiteralFuzzyMatchTests(unittest.TestCase):
    def test_does_not_fuzzy_match_on_shared_prefix_alone(self):
        segments = spellbook._compile_trigger("löscht die feuer von mittelerde")
        text = "löscht die feuer von Gondor"
        tokens = spellbook.tokenize_with_spans(text)
        self.assertIsNone(spellbook._match_segments(segments, tokens, text))

    def test_still_tolerates_a_single_word_typo(self):
        segments = spellbook._compile_trigger("löscht die feuer von mittelerde")
        text = "löscht die feur von mittelerde"  # "feuer" -> "feur"
        tokens = spellbook.tokenize_with_spans(text)
        result = spellbook._match_segments(segments, tokens, text)
        self.assertIsNotNone(result)
        _captures, _matched_text, is_exact, _score = result
        self.assertFalse(is_exact)


class FullConsumptionTests(unittest.TestCase):
    def test_extra_trailing_word_is_rejected(self):
        # The exact reproduction from the second-round audit's new
        # Potterverse finding, against the legacy matcher this time -
        # "lumos maxima Küche" must not match the area-less "lumos
        # maxima" trigger by silently dropping "Küche".
        segments = spellbook._compile_trigger("lumos maxima")
        text = "lumos maxima Küche"
        tokens = spellbook.tokenize_with_spans(text)
        self.assertIsNone(spellbook._match_segments(segments, tokens, text))

    def test_exact_full_match_still_works(self):
        segments = spellbook._compile_trigger("lumos {area}")
        text = "lumos Küche"
        tokens = spellbook.tokenize_with_spans(text)
        result = spellbook._match_segments(segments, tokens, text)
        self.assertIsNotNone(result)
        captures, _matched_text, is_exact, _score = result
        self.assertEqual(captures, {"area": "Küche"})
        self.assertTrue(is_exact)


if __name__ == "__main__":
    unittest.main()
