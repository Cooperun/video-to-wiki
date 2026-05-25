import unittest
from unittest.mock import MagicMock, patch
import sys
import os

# Adjust path to import src
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from src.config import AppConfig
from src.providers.openai_compatible import OpenAICompatibleProvider

class TestOpenAICompatible(unittest.TestCase):
    def test_config_parsing(self):
        # Verify config defaults and setting initialization
        config = AppConfig()
        self.assertEqual(config.openai_compat_api_key, "")
        self.assertEqual(config.openai_compat_api_base, "")
        self.assertEqual(config.openai_compat_model, "")
        self.assertEqual(config.openai_compat_structuring_prompt, "")

    @patch('src.providers.openai_compatible.OpenAI')
    def test_provider_invocation(self, mock_openai_class):
        # Setup mock client
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client
        
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "```markdown\n# Custom Wiki Notes\n```"
        mock_client.chat.completions.create.return_value = mock_response

        # Instantiate provider
        provider = OpenAICompatibleProvider(
            api_key="mock-key",
            api_base="https://api.mock-gateway.com/v1",
            model="mock-gpt-4o",
            structuring_prompt="Title: {video_title}\nText: {transcript_text}"
        )

        result = provider.generate_text_wiki(
            transcript_text="Hello world ASR",
            video_title="Mock Video Title"
        )

        # Verify output parsing and prompt structure
        self.assertEqual(result, "# Custom Wiki Notes")
        
        # Verify OpenAI called correctly
        mock_client.chat.completions.create.assert_called_once()
        called_kwargs = mock_client.chat.completions.create.call_args[1]
        self.assertEqual(called_kwargs["model"], "mock-gpt-4o")
        self.assertIn("Title: Mock Video Title", called_kwargs["messages"][0]["content"])
        self.assertIn("Text: Hello world ASR", called_kwargs["messages"][0]["content"])

if __name__ == "__main__":
    unittest.main()
