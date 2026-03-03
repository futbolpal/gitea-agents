import unittest
from unittest.mock import MagicMock, patch

import comment_analyzer


class TestCommentAnalyzer(unittest.TestCase):
    def test_parse_analysis(self):
        text = '{"classification":"question","answer":"Yes.","reason":"Asked why"}'
        parsed = comment_analyzer._parse_analysis(text)
        self.assertEqual(parsed["classification"], "question")
        self.assertEqual(parsed["answer"], "Yes.")

        text = '{"classification":"action","answer":"No","reason":"Change requested"}'
        parsed = comment_analyzer._parse_analysis(text)
        self.assertEqual(parsed["classification"], "action")
        self.assertEqual(parsed["answer"], "")

    def test_analyze_disabled(self):
        config = MagicMock()
        config.comment_analyzer_enabled = False
        config.openrouter_api_key = None
        result = comment_analyzer.analyze_comment("Hi", config)
        self.assertIsNone(result)

    @patch("comment_analyzer._get_model")
    def test_analyze_comment(self, mock_get_model):
        config = MagicMock()
        config.comment_analyzer_enabled = True
        config.openrouter_api_key = "key"
        config.openrouter_base_url = "https://openrouter.ai/api/v1"
        config.openrouter_referrer = None
        config.openrouter_title = "kilo-agent"
        config.comment_analyzer_model = "@preset/kilo-agent-comment-analyzer"

        instance = MagicMock()
        mock_get_model.return_value = instance
        instance.invoke.return_value = MagicMock(content='{"classification":"question","answer":"Answer","reason":"Why"}')

        result = comment_analyzer.analyze_comment("Why?", config)
        self.assertEqual(result["classification"], "question")
        self.assertEqual(result["answer"], "Answer")


if __name__ == '__main__':
    unittest.main()
