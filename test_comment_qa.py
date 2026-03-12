import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from subagent import (
    _build_pr_comment_context,
    _compose_pr_body,
    _create_or_update_issue_pr,
    _ensure_issue_plan_comment,
    _extract_issue_number_from_pr,
    _format_issue_plan_context,
    _generate_comment_answer,
    _get_issue_plan_comment_body,
    _post_comment_answer,
    _parse_comment_classification,
    _sanitize_comment_answer,
)


class TestCommentQA(unittest.TestCase):
    def test_parse_question(self):
        text = '{"classification": "question", "reason": "Asked"}'
        parsed = _parse_comment_classification(text)
        self.assertEqual(parsed["classification"], "question")
        self.assertEqual(parsed["reason"], "Asked")

    def test_parse_action(self):
        text = '{"classification": "action", "reason": "Fix"}'
        parsed = _parse_comment_classification(text)
        self.assertEqual(parsed["classification"], "action")
        self.assertEqual(parsed["reason"], "Fix")

    def test_parse_invalid(self):
        self.assertIsNone(_parse_comment_classification("not json"))

    def test_post_comment_answer_posts_pr_comment(self):
        client = MagicMock()
        logger = MagicMock()

        posted = _post_comment_answer(
            client,
            "owner",
            "repo",
            10,
            "pr_comment",
            None,
            None,
            101,
            "What does this PR change?",
            "Because it validates the PR.",
            logger,
        )

        self.assertTrue(posted)
        client.create_pull_comment.assert_called_once()
        body = client.create_pull_comment.call_args.args[3]
        self.assertIn("<!-- kilo-agent -->", body)
        self.assertIn("Addressing:", body)
        self.assertIn("> What does this PR change?", body)
        self.assertIn("Because it validates the PR.", body)

    def test_post_comment_answer_inline_falls_back(self):
        client = MagicMock()
        client.create_pull_review_comment.side_effect = Exception("boom")
        logger = MagicMock()

        posted = _post_comment_answer(
            client,
            "owner",
            "repo",
            10,
            "review_comment",
            "README.md",
            3,
            102,
            "Can you explain this line?",
            "Answer text",
            logger,
        )

        self.assertTrue(posted)
        client.create_pull_review_comment.assert_called_once()
        client.create_pull_comment.assert_called_once()

    def test_sanitize_comment_answer_removes_local_file_links(self):
        answer = "This PR changes [README.md](/tmp/worktree/README.md) and [notes](file:///tmp/worktree/notes.txt)."
        sanitized = _sanitize_comment_answer(answer)
        self.assertEqual(sanitized, "This PR changes `README.md` and `notes`.")

    def test_compose_pr_body_omits_empty_issue_body(self):
        body = _compose_pr_body("## Summary\nBody", 7, None)
        self.assertEqual(body, "## Summary\nBody\n\nCloses #7")

    def test_build_pr_comment_context_includes_pr_metadata(self):
        pr = {
            "number": 12,
            "title": "Add marker",
            "body": "Closes #7",
            "base": {"ref": "main"},
            "head": {"ref": "feature"},
        }
        context = _build_pr_comment_context(
            pr,
            "on README.md at line 3",
            "Issue Assessment And Plan:\n## Assessment\n- relevant area",
        )

        self.assertIn("PR Summary Context:", context)
        self.assertIn("- pr: #12 Add marker", context)
        self.assertIn("- branches: feature -> main", context)
        self.assertIn("- comment_context:", context)
        self.assertIn("on README.md at line 3", context)
        self.assertIn("Issue Assessment And Plan:", context)

    @patch('subagent._run_codex_text', return_value="This PR updates README.md to add the marker for validation.")
    def test_generate_comment_answer_uses_repo_aware_prompt(self, mock_run_codex_text):
        logger = MagicMock()
        config = SimpleNamespace(agent_cli='codex')
        answer = _generate_comment_answer(
            "What does this PR change?",
            "PR Summary Context:\n- pr: #12 Add marker",
            "/tmp/repo",
            config,
            logger,
        )

        self.assertEqual(answer, "This PR updates README.md to add the marker for validation.")
        prompt = mock_run_codex_text.call_args.args[0]
        self.assertIn("Inspect files, git diff, and repository history as needed", prompt)
        self.assertIn("What does this PR change?", prompt)
        self.assertIn("PR Summary Context:", prompt)

    def test_extract_issue_number_from_pr_prefers_closes_reference(self):
        pr = {
            "body": "## Summary\nStuff\n\nCloses #14",
            "head": {"ref": "fix-issue-99"},
        }
        self.assertEqual(_extract_issue_number_from_pr(pr), 14)

    def test_extract_issue_number_from_pr_falls_back_to_branch_name(self):
        pr = {
            "body": "No linked issue here",
            "head": {"ref": "fix-issue-27"},
        }
        self.assertEqual(_extract_issue_number_from_pr(pr), 27)

    def test_get_issue_plan_comment_body_returns_latest_marked_comment(self):
        client = MagicMock()
        client.get_issue_comments.return_value = [
            {"id": 1, "body": "plain comment"},
            {"id": 2, "body": "<!-- kilo-agent-issue-plan -->\n## Assessment\n- first"},
            {"id": 3, "body": "<!-- kilo-agent-issue-plan -->\n## Assessment\n- latest"},
        ]
        logger = MagicMock()

        body = _get_issue_plan_comment_body(client, "owner", "repo", 7, logger)

        self.assertIn("- latest", body)

    def test_format_issue_plan_context_strips_marker(self):
        body = "<!-- kilo-agent-issue-plan -->\n## Assessment\n- one\n\n## Plan\n1. two"
        formatted = _format_issue_plan_context(body)
        self.assertTrue(formatted.startswith("Issue Assessment And Plan:\n## Assessment"))
        self.assertNotIn("<!-- kilo-agent-issue-plan -->", formatted)

    @patch('subagent._run_codex_text', return_value="## Assessment\n- Understands the issue.\n- Notes the relevant code path.\n\n## Plan\n1. Inspect code.\n2. Implement fix.")
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
        self.assertIn('- Understands the issue.', body)
        self.assertIn('## Plan', body)
        prompt = mock_run_codex_text.call_args.args[0]
        self.assertIn('Assessment, use a short flat bullet list', prompt)

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
