import unittest
from unittest.mock import patch, MagicMock, mock_open
import os
import sys
import tempfile
import json
import subprocess

class TestIntegration(unittest.TestCase):
    def setUp(self):
        # Set mock environment variables
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
            'LOG_BACKUP_COUNT': '5'
        }
        for key, value in self.mock_env.items():
            os.environ[key] = value

    def tearDown(self):
        # Clean up environment variables
        for key in self.mock_env:
            if key in os.environ:
                del os.environ[key]

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

    def test_subagent_analyze_and_respond(self):
        """Test the analyze_and_respond function in subagent."""
        from subagent import analyze_and_respond

        # Test approval
        response = analyze_and_respond("This looks good, approved!")
        self.assertIn("approval", response.lower())

        # Test change request
        response = analyze_and_respond("Please change the variable name")
        self.assertIn("changes", response.lower())

        # Test general feedback
        response = analyze_and_respond("Nice work")
        self.assertIn("thanks", response.lower())

if __name__ == '__main__':
    unittest.main()