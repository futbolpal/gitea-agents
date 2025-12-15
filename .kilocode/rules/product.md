# Kilocode Agent for Gitea

An experience that allows agents to automatically code solutions to Gitea Issues and generate PRs.

## Definitions

- **Agent**: The main application that polls for issues and manages subagents.
- **Subagent**: A subprocess spawned by the agent to handle work on a specific issue, including PR creation and feedback monitoring.
- Owner: The human building the product

## Goal

The agent polls for new or unlabeled repo issues to initiate work.
For each qualifying issue, the agent:

- reserves the issue by tagging with ISSUE_LABEL_RESERVE
- spawns a subagent to complete the task.
- when a PR is merged, the associated issue should be tagged with ISSUE_LABEL_ACCEPTANCE

When the subagent completes its work, it creates a pull request (PR).
The subagent then begins polling for comments on the PR.

Subagents monitor PR comments and respond to feedback (e.g., by making code changes or requesting clarification).
Subagents persist their state (e.g., task progress, conversation history) using a combination of Issue ID and PR ID.

## Orchestration Workflow

### Overview
The orchestration layer (main agent) manages the overall process of monitoring repositories, spawning subagents for issues, and coordinating their lifecycle.

### Workflow Steps

#### 1. Initialization
- Load configuration from environment variables or config file.
- Validate Gitea API connection using GITEA_BASE_URL.
- Initialize logging and state storage.

#### 2. Polling Loop
- Run continuously with interval POLLING_FREQUENCY seconds.
- For each repository in GITEA_REPOS:
  - Query Gitea API for issues (open, unlabeled, not already reserved).
  - Filter qualifying issues (e.g., exclude those with certain labels).

#### 3. Issue Processing
- For each qualifying issue:
  - Apply ISSUE_LABEL_RESERVE to reserve it.
  - Spawn a subagent subprocess, passing Issue ID and config.
  - Log the subagent creation.

#### 4. Subagent Management
- Subagents run independently; orchestration layer does not directly manage their internal state.
- Periodically check for completed subagents (e.g., via process status or shared state).

#### 5. PR Merge Handling
- Poll for merged PRs associated with issues.
- When a PR is merged:
  - Tag the corresponding issue with ISSUE_LABEL_ACCEPTANCE.
  - Clean up any subagent resources if applicable.

#### 6. Error Handling
- Handle API failures (e.g., retry with backoff).
- Log errors and notify owner if critical.
- Restart failed subagents if configured.

#### 7. Shutdown
- Gracefully terminate polling on signal.
- Wait for active subagents to complete or force kill after timeout.

### Sequence Diagram (Text-based)
```
Agent Start -> Load Config -> Validate API
Loop: Poll Issues -> Filter Qualifying -> Reserve Issue -> Spawn Subagent
Subagent: Work -> Create PR -> Poll Comments -> Respond
PR Merged -> Tag Issue Acceptance -> Cleanup
```

## Configuration

- GITEA_BASE_URL - base URL for Gitea API interactions
- GITEA_REPOS - comma-delimited list of repositories to monitor (e.g., "owner/repo1,owner/repo2")
- POLLING_FREQUENCY - frequency in seconds for checking issues and PR comments (default: 60)
- ISSUE_LABEL_RESERVE - label applied to issues being worked on by a subagent (e.g., "agent-working")
- ISSUE_LABEL_ACCEPTANCE - the label applied to issues where the work has been done but the owner should review.
