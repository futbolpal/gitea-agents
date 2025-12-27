# Kilocode Agent for Gitea

An experience that allows agents to automatically code solutions to Gitea Issues and make code changes.

## Definitions

- **Orchestration**: The main application that polls for issues and manages subagents.
- **Subagent**: A subprocess spawned by the agent to handle work on a specific issue or comment.
- Owner: The human building the product

## Goal

Orchestration polls for new, unlabeled issues to initiate work.

The agent polls for new or unlabeled repo issues to initiate work.
For each qualifying issue, the agent:

- reserves the issue by tagging with ISSUE_LABEL_RESERVE
- spawns a subagent to complete the task.

When the subagent completes its work, it makes code changes and commits them.

The agent does not create new issues or pull requests. It only makes code changes as requested in existing issues.

Create small, focused commits for each logical change. Run all tests and ensure they pass before committing changes.

## Orchestration Workflow

### Overview

The orchestration layer manages the overall process of monitoring repositories, spawning subagents for coding tasks, and coordinating their lifecycle.

### Workflow Steps

#### 1. Initialization

- Load configuration from environment variables or config file.
- Validate Gitea API connection using GITEA_BASE_URL.
- Initialize logging and state storage.

#### 2. Polling Loop

- Run continuously with interval POLLING_FREQUENCY seconds.
- For each repository in GITEA_REPOS:
  - Query Gitea API for issues (open, unlabeled).
  - For each issue, check spawning condition: !reserved || (reserved && !hasProcWorker && !completed)
    - Issue is completed if it has ISSUE_LABEL_IN_REVIEW label.

#### 3. Work Processing

- For each qualifying issue:
  - Apply ISSUE_LABEL_RESERVE to reserve it.
  - Spawn a subagent subprocess, passing Issue ID and context.
  - Log the subagent creation.

#### 4. Subagent Management

- Subagents run independently; orchestration layer spawns them for issues and comments.
- Track active subprocesses by PID with associated work item (issue/comment ID and repo).
- Periodically check for completed subagents (e.g., via process status) and clean up.
- Use subprocess PID to track progress and release 'lock' if subagent dies.

#### 5. Error Handling

- Handle API failures (e.g., retry with backoff).
- Log errors and notify owner if critical.
- Restart failed subagents if configured.

#### 7. Shutdown

- Gracefully terminate polling on signal.
- Wait for active subagents to complete or force kill after timeout.

### Sequence Diagram (Text-based)

```
Agent Start -> Load Config -> Validate API
Loop: Poll Issues -> Filter Qualifying -> Reserve -> Spawn Subagent
Subagent: Work -> Commit Changes -> Exit
Issue Resolved -> Cleanup
```

## Configuration

- GITEA_BASE_URL - base URL for Gitea API interactions
- GITEA_REPOS - comma-delimited list of repositories to monitor (e.g., "owner/repo1,owner/repo2")
- POLLING_FREQUENCY - frequency in seconds for checking issues and PR comments (default: 60)
- ISSUE_LABEL_RESERVE - label applied to issues being worked on by a subagent (e.g., "agent-working")
- ISSUE_LABEL_IN_REVIEW - label applied to issues that have PRs created and are under review (e.g., "agent-in-review")
