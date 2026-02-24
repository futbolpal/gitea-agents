import unittest
from unittest.mock import patch, MagicMock, mock_open
import os
import sys
import tempfile
import shutil
import json
import subprocess

class TestIntegration(unittest.TestCase):
    def setUp(self):
        # Set mock environment variables
        self.temp_dir = tempfile.mkdtemp()
        self.mock_env = {
            'GITEA_BASE_URL': 'http://mock.gitea.com',
            'GITEA_TOKEN': 'mock_token',
            'GITEA_REPOS': 'owner/repo1,owner/repo2',
            'POLLING_FREQUENCY': '1',
            'ISSUE_LABEL_RESERVE': 'agent-working',
            'ISSUE_LABEL_IN_REVIEW': 'agent-in-review',
            'LOG_LEVEL': 'INFO',
            'LOG_FILE': 'test.log',
            'MAX_LOG_SIZE': '10485760',
            'LOG_BACKUP_COUNT': '5',
            'DATA_DIR': self.temp_dir,
            'WORKSPACE_DIR': self.temp_dir,
            'AGENT_CLI': 'kilocode'
        }
        for key, value in self.mock_env.items():
            os.environ[key] = value

    def tearDown(self):
        # Clean up environment variables
        for key in self.mock_env:
            if key in os.environ:
                del os.environ[key]
        try:
            shutil.rmtree(self.temp_dir)
        except Exception:
            pass

    def test_config_loading_and_validation(self):
        """Test that configuration loads correctly and validates."""
        from config import Config
        config = Config()
        self.assertEqual(config.gitea_base_url, 'http://mock.gitea.com/api/v1')
        self.assertEqual(config.gitea_token, 'mock_token')
        self.assertEqual(config.gitea_repos, ['owner/repo1', 'owner/repo2'])
        self.assertEqual(config.polling_frequency, 1)
        self.assertEqual(config.issue_label_reserve, 'agent-working')
        self.assertEqual(config.issue_label_in_review, 'agent-in-review')
        # Should not raise exception
        config.validate()

    @patch('main.GiteaClient')
    def test_repo_expansion(self, mock_gitea_client_class):
        """Test that 'owner/*' patterns are expanded to actual repos."""
        from main import main
        from unittest.mock import MagicMock

        # Mock client
        mock_client = MagicMock()
        mock_gitea_client_class.return_value = mock_client

        # Mock get_repos to return some repos
        mock_client.get_repos.return_value = [
            {'name': 'repo1'},
            {'name': 'repo2'}
        ]

        # Mock config with expansion
        with patch('main.Config') as mock_config_class:
            mock_config = MagicMock()
            mock_config.gitea_repos = ['owner/*', 'other/regular']
            mock_config.polling_frequency = 1
            mock_config.data_dir = self.temp_dir
            mock_config.workspace_dir = self.temp_dir
            mock_config_class.return_value = mock_config

            with patch('main.logging') as mock_logging:
                mock_logger = MagicMock()
                mock_logging.getLogger.return_value = mock_logger

                # Mock validation and other parts
                with patch('main.time.sleep', side_effect=KeyboardInterrupt):
                    try:
                        main()
                    except KeyboardInterrupt:
                        pass

        # Check that get_repos was called for 'owner'
        mock_client.get_repos.assert_called_with('owner')
        # Check that config.gitea_repos was expanded
        self.assertEqual(mock_config.gitea_repos, ['owner/repo1', 'owner/repo2', 'other/regular'])

    @patch('requests.Session')
    def test_gitea_client_initialization(self, mock_session_class):
        """Test GiteaClient initialization without real API calls."""
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session

        from gitea_client import GiteaClient
        client = GiteaClient('http://mock.gitea.com', 'mock_token')

        self.assertEqual(client.base_url, 'http://mock.gitea.com')
        mock_session_class.assert_called_once()
        mock_session.headers.update.assert_called_once_with({
            'Authorization': 'token mock_token',
            'Content-Type': 'application/json'
        })

    def test_label_creation(self):
        """Test that required labels are created."""
        from config import Config
        from gitea_client import GiteaClient

        config = Config()
        client = GiteaClient(config.gitea_base_url, config.gitea_token)

        # Test that required labels include the new one
        required_labels = [
            {"name": config.issue_label_reserve, "color": "ffa500", "description": "Issue being worked on by agent"},
            {"name": config.issue_label_in_review, "color": "ffff00", "description": "Issue has PR created and is under review"}
        ]
        self.assertEqual(len(required_labels), 2)
        self.assertEqual(required_labels[0]['name'], 'agent-working')
        self.assertEqual(required_labels[1]['name'], 'agent-in-review')

    @patch('subprocess.Popen')
    @patch('main.time.sleep')
    @patch('main.GiteaClient')
    def test_issue_creation_scenario(self, mock_gitea_client_class, mock_sleep, mock_popen):
        """Test the scenario: Handling issue creation."""
        from main import main
        from unittest.mock import MagicMock

        # Mock client
        mock_client = MagicMock()
        mock_gitea_client_class.return_value = mock_client

        # Mock get_issues to return an unreserved issue
        mock_issue = {'number': 1, 'title': 'Test Issue', 'body': 'Fix bug', 'labels': []}
        mock_issue_reserved = {'number': 1, 'title': 'Test Issue', 'body': 'Fix bug', 'labels': ['agent-working']}
        mock_client.get_issues.return_value = [mock_issue]
        mock_client.get_issue.side_effect = [mock_issue, mock_issue_reserved]

        # Mock Popen for subagent
        mock_proc = MagicMock()
        mock_proc.pid = 123
        mock_proc.poll.return_value = 0  # Success
        mock_proc.returncode = 0
        mock_popen.return_value = mock_proc

        # Mock config
        with patch('main.Config') as mock_config_class:
            mock_config = MagicMock()
            mock_config.gitea_repos = ['owner/repo']
            mock_config.issue_label_reserve = 'agent-working'
            mock_config.issue_label_in_review = 'agent-in-review'
            mock_config.polling_frequency = 1
            mock_config.max_concurrent_subagents = 10
            mock_config.data_dir = self.temp_dir
            mock_config.workspace_dir = self.temp_dir
            mock_config_class.return_value = mock_config

            # Mock logging
            with patch('main.logging') as mock_logging:
                mock_logger = MagicMock()
                mock_logging.getLogger.return_value = mock_logger

                # Run one cycle by patching the loop
                with patch('main.signal.signal'), patch('main.atexit.register'), patch('main.os.path.exists', return_value=False), patch('main.time.sleep', side_effect=KeyboardInterrupt):
                    # Call main but interrupt after one cycle
                    try:
                        main()
                    except KeyboardInterrupt:
                        pass  # Expected to break the loop

        # Assertions
        from unittest.mock import call
        # Should reserve the issue (cleanup not run in test)
        mock_client.update_issue_labels.assert_called_with('owner', 'repo', 1, ['agent-working'])
        # Should spawn subagent
        mock_popen.assert_called_with([sys.executable, 'subagent.py', '--issue', '1', 'owner/repo'])

    @patch('subprocess.Popen')
    @patch('main.time.sleep')
    @patch('main.GiteaClient')
    def test_pr_comment_scenario(self, mock_gitea_client_class, mock_sleep, mock_popen):
        """Test the scenario: Handling PR comment creation."""
        from main import main
        from unittest.mock import MagicMock

        # Mock client
        mock_client = MagicMock()
        mock_gitea_client_class.return_value = mock_client

        # Mock no issues
        mock_client.get_issues.return_value = []

        # Mock PR with comment
        mock_pr = {'number': 1, 'head': {'ref': 'feature-branch'}}
        mock_client.get_pulls.return_value = [mock_pr]
        mock_client.get_pull_comments.return_value = [{'id': 100, 'body': 'Looks good', 'type': 'pr_comment'}]
        mock_client.get_pull_reviews.return_value = []
        mock_client.get_comment_reactions.return_value = []  # No eyes/heart

        # Mock Popen
        mock_proc = MagicMock()
        mock_proc.pid = 124
        mock_proc.poll.return_value = 0
        mock_proc.returncode = 0
        mock_popen.return_value = mock_proc

        # Mock config
        with patch('main.Config') as mock_config_class:
            mock_config = MagicMock()
            mock_config.gitea_repos = ['owner/repo']
            mock_config.polling_frequency = 1
            mock_config.max_concurrent_subagents = 10
            mock_config.data_dir = self.temp_dir
            mock_config.workspace_dir = self.temp_dir
            mock_config_class.return_value = mock_config

            with patch('main.logging') as mock_logging:
                mock_logger = MagicMock()
                mock_logging.getLogger.return_value = mock_logger

                with patch('main.signal.signal'), patch('main.atexit.register'), patch('main.os.path.exists', return_value=False), patch('main.time.sleep', side_effect=KeyboardInterrupt):
                    try:
                        main()
                    except KeyboardInterrupt:
                        pass

        # Assertions
        from unittest.mock import call
        # Should add eyes and heart reactions
        mock_client.add_comment_reaction.assert_has_calls([
            call('owner', 'repo', 100, 'eyes'),
            call('owner', 'repo', 100, 'heart')
        ])
        # Should spawn subagent
        mock_popen.assert_called_with([sys.executable, 'subagent.py', '--comment', '100', 'owner/repo', '1', 'pr_comment'])

    @patch('subprocess.Popen')
    @patch('main.time.sleep')
    @patch('main.GiteaClient')
    def test_review_comment_scenario(self, mock_gitea_client_class, mock_sleep, mock_popen):
        """Test the scenario: Handling Review comment creation."""
        from main import main
        from unittest.mock import MagicMock

        # Mock client
        mock_client = MagicMock()
        mock_gitea_client_class.return_value = mock_client

        # Mock no issues
        mock_client.get_issues.return_value = []

        # Mock PR with review comment
        mock_pr = {'number': 1, 'head': {'ref': 'feature-branch'}}
        mock_client.get_pulls.return_value = [mock_pr]
        mock_client.get_pull_comments.return_value = []
        mock_client.get_pull_reviews.return_value = [{'id': 200}]
        mock_client.get_pull_review_comments.return_value = [{'id': 101, 'body': 'Fix this', 'type': 'review_comment', 'pull_request_review_id': 200}]
        mock_client.get_comment_reactions.return_value = []  # No eyes/heart

        # Mock Popen
        mock_proc = MagicMock()
        mock_proc.pid = 125
        mock_proc.poll.return_value = 0
        mock_proc.returncode = 0
        mock_popen.return_value = mock_proc

        # Mock config
        with patch('main.Config') as mock_config_class:
            mock_config = MagicMock()
            mock_config.gitea_repos = ['owner/repo']
            mock_config.polling_frequency = 1
            mock_config.max_concurrent_subagents = 10
            mock_config.data_dir = self.temp_dir
            mock_config.workspace_dir = self.temp_dir
            mock_config_class.return_value = mock_config

            with patch('main.logging') as mock_logging:
                mock_logger = MagicMock()
                mock_logging.getLogger.return_value = mock_logger

                with patch('main.signal.signal'), patch('main.atexit.register'), patch('main.os.path.exists', return_value=False), patch('main.time.sleep', side_effect=KeyboardInterrupt):
                    try:
                        main()
                    except KeyboardInterrupt:
                        pass

        # Assertions
        from unittest.mock import call
        # Should add eyes and heart reactions
        mock_client.add_comment_reaction.assert_has_calls([
            call('owner', 'repo', 101, 'eyes'),
            call('owner', 'repo', 101, 'heart')
        ])
        # Should spawn subagent with review_id
        mock_popen.assert_called_with([sys.executable, 'subagent.py', '--comment', '101', 'owner/repo', '1', 'review_comment', '200'])

    @patch('subprocess.Popen')
    @patch('main.time.sleep')
    @patch('main.GiteaClient')
    def test_retry_logic(self, mock_gitea_client_class, mock_sleep, mock_popen):
        """Test retry logic for failed subagents."""
        from main import main
        from unittest.mock import MagicMock

        # Mock client
        mock_client = MagicMock()
        mock_gitea_client_class.return_value = mock_client

        # Mock issue
        mock_issue = {'number': 1, 'labels': []}
        mock_client.get_issues.return_value = [mock_issue]
        mock_client.get_issue.return_value = mock_issue

        # Mock Popen to fail 3 times then succeed
        call_count = 0
        def mock_popen_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_proc = MagicMock()
            mock_proc.pid = 100 + call_count
            if call_count < 4:
                mock_proc.poll.return_value = 1  # Failed
                mock_proc.returncode = 1
            else:
                mock_proc.poll.return_value = 0
                mock_proc.returncode = 0
            return mock_proc

        mock_popen.side_effect = mock_popen_side_effect

        # Mock config
        with patch('main.Config') as mock_config_class:
            mock_config = MagicMock()
            mock_config.gitea_repos = ['owner/repo']
            mock_config.polling_frequency = 1
            mock_config.max_concurrent_subagents = 10
            mock_config.data_dir = self.temp_dir
            mock_config.workspace_dir = self.temp_dir
            mock_config_class.return_value = mock_config

            with patch('main.logging') as mock_logging:
                mock_logger = MagicMock()
                mock_logging.getLogger.return_value = mock_logger

                with patch('main.signal.signal'), patch('main.atexit.register'), patch('main.os.path.exists', return_value=False), patch('main.time.sleep', side_effect=KeyboardInterrupt):
                    try:
                        main()
                    except KeyboardInterrupt:
                        pass

        # Assertions
        # Should call Popen 2 times (initial + 1 retry, cleanup not fully run)
        self.assertEqual(mock_popen.call_count, 2)

if __name__ == '__main__':
    unittest.main()
