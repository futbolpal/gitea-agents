# Gitea Agents

Gitea Agents is an autonomous worker for Gitea repositories. It monitors configured repositories for issues and pull request activity, spawns focused subagents, generates code changes with either `kilocode` or `codex`, opens or updates pull requests, and responds to review feedback.

Comment on an agent-created pull request or leave review comments to iterate. The agent will pick up that feedback, update the branch, and continue the PR conversation.

Some implementation details and operational names still use the older `kilo-agents` naming, including the current image tag and default log filename.

## Architecture

The runtime has three primary pieces:

```
┌─────────────┐
│   Gitea API │
└──────┬──────┘
       │
┌──────▼──────┐
│ Orchestrator│
│  (main.py)  │
└──────┬──────┘
       │ Polls issues, PRs, and comments
       │ Applies reservation/review reactions
       │ Spawns focused subagents
┌──────▼──────┐
│ Subagent    │
│ (subagent.py)
└──────┬──────┘
       │ Clones the target repo
       │ Builds repo-aware prompts
       │ Runs kilocode or codex
       │ Commits, pushes, comments, updates PRs
┌──────▼──────┐
│ Repo / PR / │
│ Issue State │
└─────────────┘
```

**Orchestrator responsibilities**

- Poll configured repositories for open issues, open pull requests, and new PR comments or review comments.
- Reserve issues with labels before work starts.
- Mark comments in progress with reactions and avoid duplicate workers for the same work item.
- Spawn subagents for issue implementation, PR feedback handling, and stale PR updates.
- Track active subprocesses and retry failed work items when appropriate.

**Subagent responsibilities**

- Clone the target repository into a temporary workspace and check out the relevant branch.
- For issue work, generate and post an `Assessment` and `Plan` comment before implementation when `AGENT_CLI=codex`.
- Generate or update code using the configured coding CLI: `kilocode` or `codex`.
- Open or update pull requests for issue work.
- Respond to PR comments and review comments, including code changes when needed.
- Attempt stale PR updates and merge-conflict resolution, and comment when conflicts cannot be resolved automatically.

**Work item types**

- `--issue`: implement an issue in a fresh branch and create or update a PR.
- `--comment`: answer or act on a PR comment or review comment against the PR branch.
- `--update-pr`: refresh a stale PR branch against its base branch.

## Prerequisites

- Docker
- Gitea instance with API access
- Valid Gitea token with repo and issue permissions
- At least one supported coding CLI: `kilocode` or `codex`

## Configuration

Set the following environment variables:

- `GITEA_BASE_URL`: Base URL for Gitea API (e.g., `https://gitea.example.com/api/v1`)
- `GITEA_TOKEN`: Personal access token for Gitea API
- `GITEA_REPOS`: Comma-separated list of repositories to monitor (e.g., `owner/repo1,owner/repo2`)
- `POLLING_FREQUENCY`: Polling interval in seconds (default: 60)
- `ISSUE_LABEL_RESERVE`: Label for reserving issues (default: `agent-working`)
- `ISSUE_LABEL_IN_REVIEW`: Label for issues that already have an open PR (default: `agent-in-review`)
- `LOG_LEVEL`: Logging level (default: `INFO`)
- `LOG_FILE`: Log file path (default: `kilocode_agent.log`)
- `MAX_CONCURRENT_SUBAGENTS`: Max number of active subagents at once (default: `3`)
- `AGENT_CLI`: Which coding CLI to use for code generation (`kilocode` or `codex`, default: `kilocode`)
- `KILOCODE_ARGS`: Override kilocode CLI args (default: `-a -m orchestrator -j`)
- `CODEX_EXEC_ARGS`: Override codex exec args (default: `--full-auto`)
- `CODEX_PROMPT_MODE`: How to pass prompts to codex (`stdin` or `arg`, default: `stdin`)
- `CODEX_MODEL`: Optional codex model name (passed as `-m`)
- `PROMPT_TEMPLATE_PATH`: Path to the prompt template file (default: `prompt_template.txt`)
- `MAX_CONTEXT_CHARS`: Max characters of repo context injected into prompts (default: `8000`)
- `WORKSPACE_DIR`: Directory where subagent clones repositories (default: `/workspace`)
- `SUBAGENT_NICE_LEVEL`: Optional `nice` level applied when launching subagents so descendant CLI processes run at lower priority (default: `10`; set empty to disable)

## Running

### Using Docker (Recommended)

1. Build and push the image:
   ```bash
   pip install -e .
   ship-image
   ```

   Or manually:
   ```bash
   podman build --platform linux/arm64/v8 -t kilo-agents:latest .
   podman tag kilo-agents:latest homenas.tail38254.ts.net:5001/kilo-agents:latest
   podman push homenas.tail38254.ts.net:5001/kilo-agents:latest
   ```

2. Run the container:
   ```bash
   podman run -e GITEA_BASE_URL=... -e GITEA_TOKEN=... -e GITEA_REPOS=... homenas.tail38254.ts.net:5001/kilo-agents:latest
   ```

The image name is still `kilo-agents` today even though the project name is now Gitea Agents.

### Local Development

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Install `kilocode` if you want to run with `AGENT_CLI=kilocode`:
   ```bash
   curl -fsSL https://kilo.ai/install.sh | sh
   ```
3. Install Codex CLI if you want to run with `AGENT_CLI=codex`:
   ```bash
   npm install -g @openai/codex@0.112.0
   ```

4. Run the agent:
   ```bash
   python main.py
   ```

To use Codex for code generation, set `AGENT_CLI=codex`. To stay on Kilocode, leave the default or set `AGENT_CLI=kilocode`.

## Codex on a Headless Server

Codex CLI can authenticate either via browser login (`codex --login`) or by using an API key. For headless servers, use the API key flow.

1. Install the CLI:
   ```bash
   npm install -g @openai/codex@0.112.0
   ```

2. Export an API key in the environment (recommended):
   ```bash
   export OPENAI_API_KEY="<OAI_KEY>"
   ```

3. Verify the CLI is available:
   ```bash
   codex --version
   ```

4. Run this agent with Codex:
   ```bash
   export AGENT_CLI=codex
   python main.py
   ```

## Testing

Run the integration test to verify API connectivity and basic functionality:

```bash
python test_integration.py
```

Run the live issue-plan E2E against `futbolpal/kilo-agents-test`:

```bash
RUN_LIVE_GITEA_E2E=1 python3 -m unittest e2e.test_live_issue_plan_e2e
```

Run the live PR comment Q&A E2E against `futbolpal/kilo-agents-test`:

```bash
RUN_LIVE_GITEA_E2E=1 python3 -m unittest e2e.test_live_comment_qa_e2e
```

The integration test checks:
- Configuration validation
- Gitea API connectivity
- Repository access
- Label creation

The live E2E tests exercise:
- PR comment Q&A handling
- Issue assessment/plan comment generation against `futbolpal/kilo-agents-test`

## Logging

Logs are written to `kilocode_agent.log` by default with the format:
```
timestamp - [process_type] - level - message
```

Where `process_type` is either `main` or `subagent` to distinguish log sources.

## Labels

The agent uses the following labels:
- `ISSUE_LABEL_RESERVE`: Applied when an issue is actively being worked on.
- `ISSUE_LABEL_IN_REVIEW`: Applied after issue work has produced a PR.

## Security Notes

- Store Gitea tokens securely
- Ensure the token has appropriate permissions
- The agent will create commits and PRs in monitored repositories
