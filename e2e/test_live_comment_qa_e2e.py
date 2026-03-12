import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

import requests


def _load_env_file(path):
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key, value)


REPO_ROOT = Path(__file__).resolve().parents[1]
_load_env_file(REPO_ROOT / ".env")


@unittest.skipUnless(os.getenv("RUN_LIVE_GITEA_E2E") == "1", "set RUN_LIVE_GITEA_E2E=1 to run live Gitea e2e")
class TestLiveCommentQaE2E(unittest.TestCase):
    repo = "futbolpal/kilo-agents-test"

    def setUp(self):
        self.base_url = os.environ["GITEA_BASE_URL"].rstrip("/")
        if not self.base_url.endswith("/api/v1"):
            self.base_url += "/api/v1"
        self.token = os.environ["GITEA_TOKEN"]
        self.headers = {"Authorization": f"token {self.token}"}
        self.temp_root = tempfile.mkdtemp(prefix="kilo-agent-live-e2e-")
        self.branch = f"e2e-comment-qa-{int(time.time())}"
        self.pr_number = None
        self.comment_id = None

    def tearDown(self):
        shutil.rmtree(self.temp_root, ignore_errors=True)

    def _request(self, method, path, **kwargs):
        response = requests.request(
            method,
            f"{self.base_url}{path}",
            headers=self.headers,
            timeout=30,
            **kwargs,
        )
        response.raise_for_status()
        return response.json() if response.content else None

    def test_question_comment_gets_answered(self):
        clone_base = self.base_url[:-7] if self.base_url.endswith("/api/v1") else self.base_url
        clone_base = clone_base.replace("https://", "http://")
        clone_url = f"{clone_base.replace('http://', f'http://oauth2:{self.token}@')}/{self.repo}.git"
        repo_dir = os.path.join(self.temp_root, "repo")
        subprocess.run(["git", "clone", clone_url, repo_dir], check=True)
        subprocess.run(["git", "checkout", "main"], cwd=repo_dir, check=True)
        subprocess.run(["git", "config", "user.name", "kilo-agent-e2e"], cwd=repo_dir, check=True)
        subprocess.run(["git", "config", "user.email", "kilo-agent-e2e@local"], cwd=repo_dir, check=True)
        subprocess.run(["git", "checkout", "-b", self.branch], cwd=repo_dir, check=True)

        with open(os.path.join(repo_dir, "README.md"), "a", encoding="utf-8") as handle:
            handle.write(f"\n{self.branch}\n")

        subprocess.run(["git", "add", "README.md"], cwd=repo_dir, check=True)
        subprocess.run(["git", "commit", "-m", "Add e2e marker"], cwd=repo_dir, check=True)
        subprocess.run(["git", "push", "origin", self.branch], cwd=repo_dir, check=True)

        pr = self._request(
            "POST",
            f"/repos/{self.repo}/pulls",
            json={
                "title": "E2E comment qa validation",
                "head": self.branch,
                "base": "main",
                "body": "Validation PR for comment Q&A.",
            },
        )
        self.pr_number = pr["number"]

        comment = self._request(
            "POST",
            f"/repos/{self.repo}/issues/{self.pr_number}/comments",
            json={"body": "What does this PR change, and why was this marker added?"},
        )
        self.comment_id = comment["id"]

        env = os.environ.copy()
        env["DATA_DIR"] = os.path.join(self.temp_root, "data")
        env["WORKSPACE_DIR"] = os.path.join(self.temp_root, "workspace")
        env["LOG_FILE"] = os.path.join(self.temp_root, "data", "kilo-agents.log")
        env["HOME"] = os.path.join(self.temp_root, "home")
        env["CODEX_HOME"] = os.environ.get("CODEX_HOME", str(REPO_ROOT / ".codex"))
        env["AGENT_CLI"] = "codex"
        env["CODEX_EXEC_ARGS"] = os.environ.get("CODEX_EXEC_ARGS", "--dangerously-bypass-approvals-and-sandbox")
        os.makedirs(env["DATA_DIR"], exist_ok=True)
        os.makedirs(env["WORKSPACE_DIR"], exist_ok=True)
        os.makedirs(env["HOME"], exist_ok=True)

        subprocess.run(
            [
                os.environ.get("PYTHON", "python3"),
                "subagent.py",
                "--comment",
                str(self.comment_id),
                self.repo,
                str(self.pr_number),
                "pr_comment",
            ],
            cwd=REPO_ROOT,
            env=env,
            check=True,
        )

        comments = self._request("GET", f"/repos/{self.repo}/issues/{self.pr_number}/comments")
        marker_comments = [c for c in comments if "<!-- kilo-agent -->" in (c.get("body") or "")]
        self.assertTrue(marker_comments, "expected the agent to post a reply comment")


if __name__ == "__main__":
    unittest.main()
