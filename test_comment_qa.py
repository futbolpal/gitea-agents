import unittest
from unittest.mock import MagicMock

from subagent import _answer_comment_if_needed, _parse_comment_classification


class TestCommentQA(unittest.TestCase):
    def test_parse_question(self):
        text = '{"classification": "question", "answer": "Answer", "reason": "Asked"}'
        parsed = _parse_comment_classification(text)
        self.assertEqual(parsed["classification"], "question")
        self.assertEqual(parsed["answer"], "Answer")

    def test_parse_action(self):
        text = '{"classification": "action", "answer": "No", "reason": "Fix"}'
        parsed = _parse_comment_classification(text)
        self.assertEqual(parsed["classification"], "action")
        self.assertEqual(parsed["answer"], "")

    def test_parse_invalid(self):
        self.assertIsNone(_parse_comment_classification("not json"))

    def test_answer_comment_if_needed_posts_pr_comment(self):
        client = MagicMock()
        logger = MagicMock()
        analysis = {"classification": "question", "answer": "Because it validates the PR.", "reason": "question"}

        classification = _answer_comment_if_needed(
            client,
            "owner",
            "repo",
            10,
            "pr_comment",
            None,
            None,
            101,
            analysis,
            logger,
        )

        self.assertEqual(classification, "question")
        client.create_pull_comment.assert_called_once()
        body = client.create_pull_comment.call_args.args[3]
        self.assertIn("<!-- kilo-agent -->", body)

    def test_answer_comment_if_needed_inline_falls_back(self):
        client = MagicMock()
        client.create_pull_review_comment.side_effect = Exception("boom")
        logger = MagicMock()
        analysis = {"classification": "both", "answer": "Answer text", "reason": "mixed"}

        classification = _answer_comment_if_needed(
            client,
            "owner",
            "repo",
            10,
            "review_comment",
            "README.md",
            3,
            102,
            analysis,
            logger,
        )

        self.assertEqual(classification, "both")
        client.create_pull_review_comment.assert_called_once()
        client.create_pull_comment.assert_called_once()


if __name__ == '__main__':
    unittest.main()
