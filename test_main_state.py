import unittest
from unittest.mock import MagicMock, patch

import main
import subagent


class TestMainState(unittest.TestCase):
    def test_prune_stale_processes_removes_missing_pid(self):
        active = {
            123: {'proc': None, 'work_item': 'issue', 'id': 1, 'repo': 'owner/repo'}
        }
        logger = MagicMock()
        with patch('main.psutil.pid_exists', return_value=False):
            main.prune_stale_processes(active, logger)
        self.assertEqual(active, {})

    def test_prune_stale_processes_keeps_live_subagent(self):
        active = {
            456: {'proc': None, 'work_item': 'issue', 'id': 2, 'repo': 'owner/repo'}
        }
        logger = MagicMock()
        with patch('main.psutil.pid_exists', return_value=True), \
             patch('main.is_subagent_pid', return_value=True):
            main.prune_stale_processes(active, logger)
        self.assertIn(456, active)

    def test_is_comment_from_bot(self):
        config = MagicMock()
        config.gitea_bot_username = "kilo-bot"
        comment = {"id": 1, "user": {"username": "kilo-bot"}}
        self.assertTrue(main.is_comment_from_bot(comment, config))

        comment = {"id": 2, "user": {"username": "someone-else"}}
        self.assertFalse(main.is_comment_from_bot(comment, config))

    def test_is_comment_self_authored(self):
        comment = {"id": 1, "body": "Hello\n<!-- kilo-agent -->\nThanks"}
        self.assertTrue(main.is_comment_self_authored(comment))

        comment = {"id": 2, "body": "Hello world"}
        self.assertFalse(main.is_comment_self_authored(comment))

    def test_is_pr_stale(self):
        client = MagicMock()
        client.get_pull_request.return_value = {
            "base": {"ref": "main"},
            "head": {"ref": "feature"},
        }
        client.compare_commits.return_value = {"behind_by": 3}
        logger = MagicMock()
        self.assertTrue(main.is_pr_stale(client, "owner", "repo", 1, logger))

        client.compare_commits.return_value = {"behind_by": 0}
        self.assertFalse(main.is_pr_stale(client, "owner", "repo", 1, logger))

    def test_has_unresolved_conflict_comment(self):
        client = MagicMock()
        comment = {
            "id": 10,
            "body": "<!-- kilo-agent -->\nmerge conflicts\nConflicting files:\n- a.txt"
        }
        client.get_pull_comments.return_value = [comment]
        client.get_comment_reactions.return_value = []
        logger = MagicMock()
        self.assertTrue(main.has_unresolved_conflict_comment(client, "owner", "repo", 1, logger))

        client.get_comment_reactions.return_value = [{"content": "heart"}]
        self.assertFalse(main.has_unresolved_conflict_comment(client, "owner", "repo", 1, logger))

    @patch('subagent._git_output')
    def test_create_branch_from_remote_base(self, mock_git_output):
        mock_git_output.side_effect = [
            MagicMock(returncode=0, stdout="", stderr="", args=['git', 'fetch', 'origin', 'main']),
            MagicMock(returncode=0, stdout="", stderr="", args=['git', 'checkout', '-B', 'fix-issue-1', 'origin/main']),
        ]
        logger = MagicMock()

        subagent._create_branch_from_remote_base('/tmp/repo', 'main', 'fix-issue-1', logger)

        self.assertEqual(mock_git_output.call_args_list[0].args[1], ['git', 'fetch', 'origin', 'main'])
        self.assertEqual(
            mock_git_output.call_args_list[1].args[1],
            ['git', 'checkout', '-B', 'fix-issue-1', 'origin/main'],
        )


if __name__ == '__main__':
    unittest.main()
