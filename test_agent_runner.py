import unittest
import tempfile
import shutil
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import agent_runner


class TestAgentRunner(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    @patch('agent_runner.subprocess.run')
    @patch('agent_runner.shutil.which')
    def test_run_codex_uses_stdin_prompt(self, mock_which, mock_run):
        mock_which.return_value = '/usr/bin/codex'
        mock_run.return_value = MagicMock(returncode=0, stderr="")

        config = SimpleNamespace(
            agent_cli='codex',
            codex_exec_args=['--full-auto'],
            codex_model=None,
            codex_prompt_mode='stdin',
            data_dir=self.temp_dir,
        )

        prompt = "Fix the failing test"
        agent_runner.run_agent(prompt, self.temp_dir, config)

        args, kwargs = mock_run.call_args
        self.assertIn('codex', args[0][0])
        self.assertEqual(args[0][:3], ['codex', 'exec', '-C'])
        self.assertEqual(args[0][3], self.temp_dir)
        self.assertEqual(args[0][4], '--full-auto')
        self.assertEqual(args[0][-1], '-')
        self.assertEqual(kwargs['input'], prompt)

    @patch('agent_runner.subprocess.run')
    @patch('agent_runner.shutil.which')
    def test_run_kilocode_command(self, mock_which, mock_run):
        mock_which.return_value = '/usr/bin/kilocode'
        mock_run.return_value = MagicMock(returncode=0, stderr="")

        config = SimpleNamespace(
            agent_cli='kilocode',
            kilocode_args=['-a', '-m', 'orchestrator', '-j'],
            data_dir=self.temp_dir,
        )

        prompt = "Implement feature"
        agent_runner.run_agent(prompt, self.temp_dir, config)

        args, kwargs = mock_run.call_args
        self.assertEqual(args[0][:4], ['kilocode', '-a', '-m', 'orchestrator'])
        self.assertEqual(kwargs['input'], prompt)

    @patch('agent_runner.shutil.which')
    def test_missing_cli_raises(self, mock_which):
        mock_which.return_value = None
        config = SimpleNamespace(
            agent_cli='kilocode',
            kilocode_args=['-a'],
            data_dir=self.temp_dir,
        )
        with self.assertRaises(FileNotFoundError):
            agent_runner.run_agent("prompt", self.temp_dir, config)


if __name__ == '__main__':
    unittest.main()
