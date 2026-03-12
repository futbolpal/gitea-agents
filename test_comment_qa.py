import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from subagent import (
    _answer_comment_if_needed,
    _compose_pr_body,
    _create_or_update_issue_pr,
    _ensure_issue_plan_comment,
    _parse_comment_classification,
)


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

    def test_compose_pr_body_omits_empty_issue_body(self):
        body = _compose_pr_body("## Summary\nBody", 7, None)
        self.assertEqual(body, "## Summary\nBody\n\nCloses #7")

    @patch('subagent._run_codex_text', return_value="## Assessment\nUnderstands the issue.\n\n## Plan\n1. Inspect code.\n2. Implement fix.")
    def test_ensure_issue_plan_comment_posts_generated_comment(self, mock_run_codex_text):
        client = MagicMock()
        client.get_issue_comments.return_value = []
        config = SimpleNamespace(agent_cli='codex')
        issue = {'number': 7, 'title': 'Broken flow', 'body': 'Issue details'}
        logger = MagicMock()

        _ensure_issue_plan_comment(
            client,
            'owner',
            'repo',
            issue,
            'Context:\n- tech: python',
            '/tmp/repo',
            config,
            logger,
        )

        mock_run_codex_text.assert_called_once()
        client.create_issue_comment.assert_called_once()
        body = client.create_issue_comment.call_args.args[3]
        self.assertIn('<!-- kilo-agent-issue-plan -->', body)
        self.assertIn('## Assessment', body)
        self.assertIn('## Plan', body)

    @patch('subagent._run_codex_text')
    def test_ensure_issue_plan_comment_skips_existing_marker(self, mock_run_codex_text):
        client = MagicMock()
        client.get_issue_comments.return_value = [
            {'id': 1, 'body': '<!-- kilo-agent-issue-plan -->\n## Assessment\nAlready posted'}
        ]
        config = SimpleNamespace(agent_cli='codex')
        issue = {'number': 8, 'title': 'Existing plan', 'body': 'Issue details'}
        logger = MagicMock()

        _ensure_issue_plan_comment(
            client,
            'owner',
            'repo',
            issue,
            'Context:\n- tech: python',
            '/tmp/repo',
            config,
            logger,
        )

        mock_run_codex_text.assert_not_called()
        client.create_issue_comment.assert_not_called()

    @patch('subagent._generate_pr_summary', return_value="## Summary\nGenerated")
    def test_create_or_update_issue_pr_updates_existing_pr_body_on_conflict(self, mock_summary):
        client = MagicMock()
        client.create_pull_request.side_effect = Exception("API Error 409: pull request already exists")
        client.get_pulls.return_value = [
            {'number': 42, 'head': {'ref': 'fix-issue-7'}}
        ]
        logger = MagicMock()
        config = SimpleNamespace(agent_cli='kilocode')
        issue = {'title': 'Broken flow', 'body': 'Issue details'}

        pr = _create_or_update_issue_pr(
            client,
            'owner',
            'repo',
            issue,
            7,
            'fix-issue-7',
            'main',
            '/tmp/repo',
            config,
            logger,
        )

        self.assertEqual(pr['number'], 42)
        client.update_pull_request.assert_called_once_with(
            'owner',
            'repo',
            42,
            title='Fix issue #7: Broken flow',
            body='## Summary\nGenerated\n\nCloses #7\n\nIssue details',
            base='main',
        )


if __name__ == '__main__':
    unittest.main()
