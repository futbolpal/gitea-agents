import os
import re
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
class TestLiveIssueBranchFlowE2E(unittest.TestCase):
    repo = "futbolpal/kilo-agents-test"

    def setUp(self):
        self.base_url = os.environ["GITEA_BASE_URL"].rstrip("/")
        if not self.base_url.endswith("/api/v1"):
            self.base_url += "/api/v1"
        self.token = os.environ["GITEA_TOKEN"]
        self.headers = {"Authorization": f"token {self.token}"}
        clone_base = self.base_url[:-7] if self.base_url.endswith("/api/v1") else self.base_url
        clone_base = clone_base.replace("https://", "http://")
        self.clone_url = f"{clone_base.replace('http://', f'http://oauth2:{self.token}@')}/{self.repo}.git"
        self.temp_root = tempfile.mkdtemp(prefix="kilo-agent-issue-branch-flow-e2e-")
        self.issue_number = None
        self.pr_number = None
        self.head_branch = None

    def tearDown(self):
        if self.pr_number is not None:
            try:
                self._request("PATCH", f"/repos/{self.repo}/issues/{self.pr_number}", json={"state": "closed"})
            except Exception:
                pass
        if self.head_branch is not None:
            try:
                self._delete_remote_branch(self.head_branch)
            except Exception:
                pass
        if self.issue_number is not None:
            try:
                self._request("PATCH", f"/repos/{self.repo}/issues/{self.issue_number}", json={"state": "closed"})
            except Exception:
                pass
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

    def _ensure_label(self, name, color, description):
        labels = self._request("GET", f"/repos/{self.repo}/labels") or []
        if any((label.get("name") or "") == name for label in labels):
            return
        self._request(
            "POST",
            f"/repos/{self.repo}/labels",
            json={"name": name, "color": color, "description": description},
        )

    def _latest_release_branch(self):
        branches = self._request("GET", f"/repos/{self.repo}/branches") or []
        candidates = []
        for branch in branches:
            name = branch.get("name") or ""
            match = re.fullmatch(r"release/(\d+)\.(\d+)", name)
            if match:
                candidates.append((int(match.group(1)), int(match.group(2)), name))
        if not candidates:
            self.fail("Expected at least one release/x.y branch in the e2e repo")
        return max(candidates)[2]

    def _delete_remote_branch(self, branch):
        existing = subprocess.run(
            ["git", "ls-remote", "--heads", self.clone_url, branch],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ).stdout.strip()
        if not existing:
            return
        subprocess.run(
            ["git", "push", self.clone_url, "--delete", branch],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def test_issue_pr_targets_latest_release_branch_from_agents_instructions(self):
        expected_base = self._latest_release_branch()
        marker = f"e2e-branch-flow-{int(time.time())}"

        self._ensure_label("agent-working", "ffa500", "Issue being worked on by agent")

        issue = self._request(
            "POST",
            f"/repos/{self.repo}/issues",
            json={
                "title": f"E2E branch flow {marker}",
                "body": (
                    "Append the following marker as a new line in README.md:\n\n"
                    f"{marker}\n\n"
                    "Keep the change minimal."
                ),
            },
        )
        self.issue_number = issue["number"]

        self._request(
            "PUT",
            f"/repos/{self.repo}/issues/{self.issue_number}/labels",
            json={"labels": ["agent-working"]},
        )
        self._delete_remote_branch(f"fix-issue-{self.issue_number}")

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
                "--issue",
                str(self.issue_number),
                self.repo,
            ],
            cwd=REPO_ROOT,
            env=env,
            check=True,
            timeout=900,
        )

        pulls = self._request("GET", f"/repos/{self.repo}/pulls?state=open") or []
        matching_prs = [
            pr for pr in pulls
            if f"Closes #{self.issue_number}" in (pr.get("body") or "")
        ]
        self.assertTrue(matching_prs, f"expected an open PR linked to issue #{self.issue_number}")

        pr = matching_prs[0]
        self.pr_number = pr["number"]
        self.head_branch = pr.get("head", {}).get("ref")
        self.assertEqual(pr.get("base", {}).get("ref"), expected_base)
        self.assertNotEqual(pr.get("head", {}).get("ref"), expected_base)
        self.assertIn(f"Closes #{self.issue_number}", pr.get("body") or "")


if __name__ == "__main__":
    unittest.main()
