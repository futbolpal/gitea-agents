import unittest
from unittest.mock import MagicMock, patch

import main
import subagent


class TestMainState(unittest.TestCase):
    @patch('subagent.run_agent')
    @patch('subagent._load_prompt_template', return_value='{prompt}')
    @patch('subagent.subprocess.run')
    def test_do_work_skips_checkout_when_branch_unspecified(self, mock_run, mock_template, mock_run_agent):
        mock_run_agent.return_value = (MagicMock(returncode=0, stderr=''), '/tmp/output.log')

        subagent.do_work('implement change', '/tmp/repo', MagicMock(), None)

        mock_run.assert_not_called()
        mock_run_agent.assert_called_once()

    @patch('main.shutil.which', return_value='/usr/bin/nice')
    def test_build_subagent_command_uses_nice(self, mock_which):
        config = MagicMock()
        config.subagent_nice_level = 10

        command = main.build_subagent_command(['--issue', '1', 'owner/repo'], config)

        self.assertEqual(
            command,
            ['/usr/bin/nice', '-n', '10', main.sys.executable, 'subagent.py', '--issue', '1', 'owner/repo'],
        )

    @patch('main.shutil.which', return_value=None)
    def test_build_subagent_command_skips_nice_when_unavailable(self, mock_which):
        config = MagicMock()
        config.subagent_nice_level = 10

        command = main.build_subagent_command(['--issue', '1', 'owner/repo'], config)

        self.assertEqual(command, [main.sys.executable, 'subagent.py', '--issue', '1', 'owner/repo'])

    def test_build_subagent_command_skips_nice_when_disabled(self):
        config = MagicMock()
        config.subagent_nice_level = None

        command = main.build_subagent_command(['--issue', '1', 'owner/repo'], config)

        self.assertEqual(command, [main.sys.executable, 'subagent.py', '--issue', '1', 'owner/repo'])

    @patch('subagent.subprocess.run')
    def test_push_branch_accepts_remote_ahead_after_fetch(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=1, stdout="", stderr="rejected", args=['git', 'push', 'origin', 'fix-issue-1']),
            MagicMock(returncode=0, stdout="", stderr="", args=['git', 'fetch', 'origin', 'fix-issue-1']),
            MagicMock(returncode=0, stdout="0 1\n", stderr="", args=['git', 'rev-list', '--left-right', '--count', 'HEAD...origin/fix-issue-1']),
            MagicMock(returncode=0, stdout="", stderr="", args=['git', 'merge', '--ff-only', 'origin/fix-issue-1']),
        ]
        logger = MagicMock()

        subagent._push_branch('/tmp/repo', 'fix-issue-1', logger)

        self.assertEqual(mock_run.call_args_list[1].args[0], ['git', 'fetch', 'origin', 'fix-issue-1'])
        self.assertEqual(mock_run.call_args_list[3].args[0], ['git', 'merge', '--ff-only', 'origin/fix-issue-1'])

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
