import os
import tempfile
import unittest
from unittest.mock import patch

import yaml

from src.config import AppConfig
from src.pipeline import IngestionPipeline
from src.initializer import DEFAULT_CONFIG_TEMPLATE, OPENAI_COMPATIBLE_PRESETS, update_yaml_many
from src.pure_subtitle_extractor import VisualSubtitleExtractor


class InitializerTestCase(unittest.TestCase):
    def test_openai_compatible_presets_cover_common_gateways(self):
        preset_ids = {preset["id"] for preset in OPENAI_COMPATIBLE_PRESETS}
        self.assertIn("openai", preset_ids)
        self.assertIn("oneapi", preset_ids)
        self.assertIn("litellm", preset_ids)
        self.assertIn("ollama", preset_ids)

    def test_builtin_init_template_is_valid_yaml(self):
        data = yaml.safe_load(DEFAULT_CONFIG_TEMPLATE)
        self.assertEqual(data["provider"], "openai_compatible")
        self.assertEqual(data["wiki_dir"], "~/Documents/llm_wiki")
        self.assertEqual(data["qwen"]["ocr_model"], "qwen3-vl-plus")
        self.assertEqual(data["subtitle_ocr"]["mode"], "hybrid")

    def test_update_yaml_many_sets_openai_compatible_provider(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "config.yaml")
            with open(config_path, "w", encoding="utf-8") as f:
                f.write(
                    'provider: "deepseek"\n'
                    "openai_compatible:\n"
                    '  api_key: ""\n'
                    '  api_base: ""\n'
                    '  model: ""\n'
                )

            update_yaml_many(config_path, [
                (None, "provider", "openai_compatible"),
                ("openai_compatible", "api_key", "test-key"),
                ("openai_compatible", "api_base", "http://localhost:4000/v1"),
                ("openai_compatible", "model", "test-model"),
            ])

            with open(config_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)

            self.assertEqual(data["provider"], "openai_compatible")
            self.assertEqual(data["openai_compatible"]["api_key"], "test-key")
            self.assertEqual(data["openai_compatible"]["api_base"], "http://localhost:4000/v1")
            self.assertEqual(data["openai_compatible"]["model"], "test-model")

    def test_builtin_wiki_dir_default_is_user_generic(self):
        config = AppConfig.__new__(AppConfig)
        AppConfig.__init__(config, config_path=None)
        self.assertTrue(config.wiki_dir.endswith(os.path.join("Documents", "llm_wiki")))
        self.assertNotIn(os.path.join("Documents", "antigravity", "llm_wiki"), config.wiki_dir)

    def test_openai_compatible_dashscope_env_resolution(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "config.yaml")
            with open(config_path, "w", encoding="utf-8") as f:
                f.write(
                    'provider: "openai_compatible"\n'
                    "openai_compatible:\n"
                    '  api_key: ""\n'
                    '  api_base: "https://dashscope.aliyuncs.com/compatible-mode/v1"\n'
                    '  model: "qwen-plus"\n'
                )

            with patch.dict(os.environ, {"DASHSCOPE_API_KEY": "dashscope-key"}, clear=False):
                config = AppConfig(config_path=config_path)

            self.assertEqual(config.openai_compat_api_key, "dashscope-key")

    def test_cloud_ocr_requires_visual_api_key(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "config.yaml")
            with open(config_path, "w", encoding="utf-8") as f:
                f.write(
                    'provider: "openai_compatible"\n'
                    'wiki_dir: "~/Documents/llm_wiki"\n'
                    "openai_compatible:\n"
                    '  api_key: "text-key"\n'
                    '  api_base: "http://localhost:4000/v1"\n'
                    '  model: "text-model"\n'
                    "qwen:\n"
                    '  api_key: ""\n'
                    "subtitle_ocr:\n"
                    '  mode: "cloud"\n'
                )

            with patch.dict(os.environ, {"DASHSCOPE_API_KEY": "", "BAILIAN_API_KEY": ""}, clear=False):
                with self.assertRaises(RuntimeError):
                    IngestionPipeline(config_path=config_path)

    def test_hybrid_ocr_without_cloud_key_keeps_local_result(self):
        extractor = VisualSubtitleExtractor(
            api_key="",
            temp_dir=tempfile.mkdtemp(),
            ocr_mode="hybrid",
        )
        extractor.ocr_subtitle_local = lambda image_path, box=None: ("本地字幕", 0.1)

        text = extractor.ocr_subtitle(__file__)

        self.assertEqual(text, "本地字幕")
        self.assertEqual(extractor.ocr_called_count, 0)
        self.assertEqual(extractor.ocr_hybrid_escalated_count, 0)


if __name__ == "__main__":
    unittest.main()
