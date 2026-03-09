import unittest

from subagent import _parse_comment_classification


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


if __name__ == '__main__':
    unittest.main()
