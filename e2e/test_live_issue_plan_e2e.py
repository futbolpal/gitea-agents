import os
import shutil
import signal
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
class TestLiveIssuePlanE2E(unittest.TestCase):
    repo = "futbolpal/kilo-agents-test"
    marker = "<!-- kilo-agent-issue-plan -->"

    def setUp(self):
        self.base_url = os.environ["GITEA_BASE_URL"].rstrip("/")
        if not self.base_url.endswith("/api/v1"):
            self.base_url += "/api/v1"
        self.token = os.environ["GITEA_TOKEN"]
        self.headers = {"Authorization": f"token {self.token}"}
        self.temp_root = tempfile.mkdtemp(prefix="kilo-agent-issue-plan-e2e-")
        self.issue_number = None
        self.agent_proc = None

    def tearDown(self):
        if self.agent_proc is not None and self.agent_proc.poll() is None:
            self.agent_proc.send_signal(signal.SIGTERM)
            try:
                self.agent_proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.agent_proc.kill()
                self.agent_proc.wait(timeout=5)
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

    def test_issue_plan_comment_uses_bulleted_assessment(self):
        issue = self._request(
            "POST",
            f"/repos/{self.repo}/issues",
            json={
                "title": f"E2E bullet assessment {int(time.time())}",
                "body": "Validate that the generated Assessment section is formatted as a bullet list.",
            },
        )
        self.issue_number = issue["number"]

        env = os.environ.copy()
        env["GITEA_REPOS"] = self.repo
        env["POLLING_FREQUENCY"] = "1"
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

        self.agent_proc = subprocess.Popen(
            [os.environ.get("PYTHON", "python3"), "main.py"],
            cwd=REPO_ROOT,
            env=env,
        )

        found = None
        deadline = time.time() + 180
        while time.time() < deadline:
            comments = self._request("GET", f"/repos/{self.repo}/issues/{self.issue_number}/comments") or []
            for comment in comments:
                body = comment.get("body") or ""
                if self.marker in body:
                    found = comment
                    break
            if found:
                break
            if self.agent_proc.poll() is not None:
                break
            time.sleep(2)

        if not found:
            logs = ""
            try:
                logs = Path(env["LOG_FILE"]).read_text(encoding="utf-8", errors="replace")
            except Exception:
                pass
            self.fail("Did not observe generated plan comment on test issue.\n\n" + logs[-12000:])

        body = found.get("body") or ""
        self.assertIn("## Assessment", body)
        self.assertIn("## Plan", body)

        assessment_chunk = body.split("## Plan", 1)[0]
        assessment_lines = [line.strip() for line in assessment_chunk.splitlines() if line.strip()]
        bullet_lines = [line for line in assessment_lines if line.startswith("- ")]
        self.assertGreaterEqual(
            len(bullet_lines),
            2,
            f"expected at least two assessment bullets, got:\n{body}",
        )


if __name__ == "__main__":
    unittest.main()
