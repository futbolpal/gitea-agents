# Kilocode Agent for Gitea

An autonomous agent that monitors Gitea repositories for issues, automatically generates code solutions using the kilocode AI tool, and creates pull requests for review.  

Comment on the resulting PR or add review comments to iterate.  The agent will respond to your feedback and update the PR!

## Architecture

The system consists of three main components:

```
┌─────────────┐
│   Gitea API │
└──────┬──────┘
       │
┌──────▼──────┐
│ Main Agent  │
│  (main.py)  │
└──────┬──────┘
       │ Polls for open issues
       │ Reserves issues with labels
       │ Spawns subagents
┌──────▼──────┐
│ Subagent    │
│ (subagent.py│
│   process)  │
└──────┬──────┘
       │ Clones repo
       │ Generates code with kilocode
       │ Commits & pushes changes
       │ Creates PR
       │ Monitors PR comments
       │ Responds to feedback
┌──────▼──────┐
│   Gitea     │
│ Repository  │
│   & PRs     │
└─────────────┘
```

**Control Flow:**
1. Main agent polls Gitea API for open issues
2. Filters qualifying issues (no reserve/acceptance labels)
3. Reserves issue by adding label
4. Spawns subagent subprocess
5. Subagent clones repo, generates code, creates PR
6. Subagent monitors PR for comments and responds
7. Main agent monitors for merged PRs and cleans up

## Prerequisites

- Docker
- Gitea instance with API access
- Valid Gitea token with repo and issue permissions

## Configuration

Set the following environment variables:

- `GITEA_BASE_URL`: Base URL for Gitea API (e.g., `https://gitea.example.com/api/v1`)
- `GITEA_TOKEN`: Personal access token for Gitea API
- `GITEA_REPOS`: Comma-separated list of repositories to monitor (e.g., `owner/repo1,owner/repo2`)
- `POLLING_FREQUENCY`: Polling interval in seconds (default: 60)
- `ISSUE_LABEL_RESERVE`: Label for reserving issues (default: `agent-working`)
- `LOG_LEVEL`: Logging level (default: `INFO`)
- `LOG_FILE`: Log file path (default: `kilocode_agent.log`)
- `AGENT_CLI`: Which CLI to run for code generation (`kilocode` or `codex`, default: `kilocode`)
- `KILOCODE_ARGS`: Override kilocode CLI args (default: `-a -m orchestrator -j`)
- `CODEX_EXEC_ARGS`: Override codex exec args (default: `--full-auto`)
- `CODEX_PROMPT_MODE`: How to pass prompts to codex (`stdin` or `arg`, default: `stdin`)
- `CODEX_MODEL`: Optional codex model name (passed as `-m`)
- `PROMPT_TEMPLATE_PATH`: Path to the prompt template file (default: `prompt_template.txt`)

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

### Local Development

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Install kilocode CLI:
   ```bash
   curl -fsSL https://kilo.ai/install.sh | sh
   ```
3. (Optional) Install Codex CLI:
   ```bash
   npm install -g @openai/codex
   ```

4. Run the agent:
   ```bash
   python main.py
   ```

To use Codex CLI for code generation, set `AGENT_CLI=codex`.

## Codex on a Headless Server

Codex CLI can authenticate either via browser login (`codex --login`) or by using an API key. For headless servers, use the API key flow.

1. Install the CLI:
   ```bash
   npm install -g @openai/codex
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

The test checks:
- Configuration validation
- Gitea API connectivity
- Repository access
- Label creation

## Logging

Logs are written to `kilocode_agent.log` with the format:
```
timestamp - [process_type] - level - message
```

Where `process_type` is either `main` or `subagent` to distinguish log sources.

## Labels

The agent uses the following labels:
- `ISSUE_LABEL_RESERVE`: Applied to issues being worked on by subagents

## Security Notes

- Store Gitea tokens securely
- Ensure the token has appropriate permissions
- The agent will create commits and PRs in monitored repositories
