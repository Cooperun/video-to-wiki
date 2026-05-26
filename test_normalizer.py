import json
import os
import tempfile
import unittest

from src.normalizer import TermNormalizer


class TestTermNormalizer(unittest.TestCase):
    def test_static_and_dynamic_mappings_are_applied(self):
        normalizer = TermNormalizer()

        text = "DPC V4 Pro can run in cloud code, and pain mode is useful."
        normalized, corrections = normalizer.normalize(
            text,
            dynamic_mapping={"pain mode": "Plan Mode"},
        )

        self.assertIn("DeepSeek V4 Pro", normalized)
        self.assertIn("Claude Code", normalized)
        self.assertIn("Plan Mode", normalized)
        self.assertEqual(len(corrections), 3)

    def test_custom_corrections_are_persisted_and_reloaded(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "custom_corrections.json")
            normalizer = TermNormalizer(custom_corrections_path=path)

            normalizer.save_custom_corrections({"ducic": "DeepSeek"})

            with open(path, "r", encoding="utf-8") as f:
                saved = json.load(f)
            self.assertEqual(saved["ducic"], "DeepSeek")

            reloaded = TermNormalizer(custom_corrections_path=path)
            normalized, corrections = reloaded.normalize("ducic is mentioned here")

            self.assertIn("DeepSeek", normalized)
            self.assertEqual(corrections[0]["original_typo"], "ducic")

    def test_json_response_parser_handles_fences_and_thinking(self):
        normalizer = TermNormalizer()

        parsed = normalizer._parse_json_response(
            '<think>ignore this</think>\n```json\n{"clothcode": "Claude Code",}\n```'
        )

        self.assertEqual(parsed, {"clothcode": "Claude Code"})


if __name__ == "__main__":
    unittest.main()
