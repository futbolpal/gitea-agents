import unittest
from unittest.mock import MagicMock, patch

import main


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


if __name__ == '__main__':
    unittest.main()
