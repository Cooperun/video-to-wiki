import os
import tempfile
import unittest

from main import parse_args, resolve_input_source
from src.config import AppConfig


class CLITestCase(unittest.TestCase):
    def test_parse_no_ocr_flag(self):
        args = parse_args(["--url", "https://example.com/video", "--no-ocr"])
        self.assertEqual(args.url, "https://example.com/video")
        self.assertTrue(args.no_ocr)

    def test_model_override_available_for_subtitle_extraction(self):
        args = parse_args([
            "--file",
            __file__,
            "--extract-subtitle",
            "--ocr-mode",
            "cloud",
            "--model",
            "qwen-test-model",
        ])
        self.assertTrue(args.extract_subtitle)
        self.assertEqual(args.ocr_mode, "cloud")
        self.assertEqual(args.model, "qwen-test-model")

    def test_missing_file_fails_before_pipeline(self):
        args = parse_args(["--file", "/tmp/video-to-wiki-missing-file.mp4"])
        with self.assertRaises(FileNotFoundError):
            resolve_input_source(args)

    def test_explicit_missing_config_fails(self):
        with self.assertRaises(FileNotFoundError):
            AppConfig(config_path="/tmp/video-to-wiki-missing-config.yaml")

    def test_invalid_ocr_config_fails(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "config.yaml")
            with open(config_path, "w", encoding="utf-8") as f:
                f.write(
                    "provider: deepseek\n"
                    "subtitle_ocr:\n"
                    "  mode: nope\n"
                )

            with self.assertRaises(ValueError):
                AppConfig(config_path=config_path)


if __name__ == "__main__":
    unittest.main()
