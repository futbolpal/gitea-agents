import time
import signal
import subprocess
import logging
import os
import atexit
from config import Config
from gitea_client import GiteaClient

def main():
    os.environ['PROCESS_TYPE'] = 'main'
    config = Config()
    config.validate()
    logger = config.setup_logging()
    logger.info("Starting Kilocode Agent")

    client = GiteaClient(config.gitea_base_url, config.gitea_token)

    # Validate API connection
    try:
        logger.debug(f"Validating API connection with base_url: {client.base_url}")
        logger.debug(f"GITEA_REPOS: {config.gitea_repos}")
        if not config.gitea_repos:
            logger.error("No repositories configured")
            return
        first_repo = config.gitea_repos[0]
        logger.debug(f"Using first repo for validation: {first_repo}")
        owner, repo_name = first_repo.split('/', 1)
        logger.debug(f"Parsed owner: {owner}, repo: {repo_name}")
        # Basic API connectivity check
        client.get_issues(owner, repo_name, state='open', limit=1)
        logger.info("API connection validated successfully")
    except Exception as e:
        logger.error(f"Failed to validate API connection: {e}")
        logger.debug(f"Exception type: {type(e).__name__}, details: {e}")
        return

    # Ensure required labels exist in all repositories
    required_labels = [
        {"name": config.issue_label_reserve, "color": "ffa500", "description": "Issue being worked on by agent"},
        {"name": config.issue_label_acceptance, "color": "008000", "description": "Work completed and ready for review"}
    ]
    for repo in config.gitea_repos:
        owner, repo_name = repo.split('/', 1)
        try:
            existing_labels = client.get_labels(owner, repo_name)
            existing_names = {label['name'] for label in existing_labels}
            for label in required_labels:
                if label['name'] not in existing_names:
                    try:
                        client.create_label(owner, repo_name, **label)
                        logger.info(f"Created label '{label['name']}' in {repo}")
                    except Exception as e:
                        logger.warning(f"Failed to create label '{label['name']}' in {repo}: {e}")
        except Exception as e:
            logger.error(f"Failed to check/create labels in {repo}: {e}")

    running = True
    active_subprocesses = []

    def cleanup_subprocesses():
        """Cleanup active subprocesses on shutdown."""
        logger.info("Cleaning up active subprocesses...")
        for proc in active_subprocesses:
            if proc.poll() is None:  # Still running
                try:
                    proc.terminate()
                    proc.wait(timeout=5)
                    logger.info(f"Terminated subprocess {proc.pid}")
                except subprocess.TimeoutExpired:
                    proc.kill()
                    logger.warning(f"Force killed subprocess {proc.pid}")
                except Exception as e:
                    logger.error(f"Error terminating subprocess {proc.pid}: {e}")

    def signal_handler(sig, frame):
        nonlocal running
        logger.info("Received shutdown signal, initiating graceful shutdown...")
        running = False
        cleanup_subprocesses()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    atexit.register(cleanup_subprocesses)

    while running:
        logger.info("Starting polling cycle")
        for repo in config.gitea_repos:
            owner, repo_name = repo.split('/', 1)
            try:
                logger.debug(f"Fetching issues for {repo}")
                issues = client.get_issues(owner, repo_name, state='open')
                logger.info(f"Found {len(issues)} open issues in {repo}")
                for issue in issues:
                    labels = [label['name'] for label in issue.get('labels', [])]
                    if config.issue_label_reserve not in labels and config.issue_label_acceptance not in labels:
                        logger.info(f"Reserving issue {issue['number']} in {repo}")
                        try:
                            # Reserve the issue
                            new_labels = labels + [config.issue_label_reserve]
                            client.update_issue_labels(owner, repo_name, issue['number'], new_labels)
                            # Spawn subagent
                            proc = subprocess.Popen(['python', 'subagent.py', str(issue['number']), repo])
                            active_subprocesses.append(proc)
                            logger.info(f"Spawned subagent for issue {issue['number']} in {repo} (PID: {proc.pid})")
                        except Exception as e:
                            logger.error(f"Failed to reserve or spawn subagent for issue {issue['number']}: {e}")
            except Exception as e:
                logger.error(f"Error processing repo {repo}: {e}")

        # Check for merged PRs and tag issues
        for repo in config.gitea_repos:
            owner, repo_name = repo.split('/', 1)
            try:
                logger.debug(f"Checking merged PRs for {repo}")
                pulls = client.get_pulls(owner, repo_name, state='closed')
                for pr in pulls:
                    if pr.get('merged'):
                        logger.info(f"Detected merged PR #{pr['number']} in {repo}")
                        # Extract issue number from PR title/body (simple regex for #123)
                        import re
                        issue_match = re.search(r'#(\d+)', pr.get('title', '') + ' ' + pr.get('body', ''))
                        if issue_match:
                            issue_number = int(issue_match.group(1))
                            try:
                                # Tag the issue with acceptance
                                issue = client.get_issue(owner, repo_name, issue_number)
                                labels = [label['name'] for label in issue.get('labels', [])]
                                if config.issue_label_acceptance not in labels:
                                    new_labels = labels + [config.issue_label_acceptance]
                                    client.update_issue_labels(owner, repo_name, issue_number, new_labels)
                                    logger.info(f"Tagged issue #{issue_number} with acceptance label")
                            except Exception as e:
                                logger.error(f"Error tagging issue #{issue_number} for merged PR #{pr['number']}: {e}")
            except Exception as e:
                logger.error(f"Error checking PRs for repo {repo}: {e}")

        # Clean up finished subprocesses
        active_subprocesses[:] = [proc for proc in active_subprocesses if proc.poll() is None]

        logger.info(f"Polling cycle completed, sleeping for {config.polling_frequency} seconds")
        time.sleep(config.polling_frequency)

    logger.info("Agent shutdown complete")

if __name__ == '__main__':
    main()
