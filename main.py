import time
import signal
import subprocess
import logging
import os
import atexit
import json
from config import Config
from gitea_client import GiteaClient
from utils import analyze_and_respond

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
        {"name": config.issue_label_reserve, "color": "ffa500", "description": "Issue being worked on by agent"}
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
    pr_comment_state = {}  # (repo, pr_number) -> {'last_comment_id': int}

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
                    if config.issue_label_reserve not in labels:
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


        # Query for PR comments and reviews
        for repo in config.gitea_repos:
            owner, repo_name = repo.split('/', 1)
            try:
                # Get open PRs
                prs = client.get_pulls(owner, repo_name, state='open')
                for pr in prs:
                    pr_number = pr['number']
                    pr_key = (repo, pr_number)

                    # Initialize state for this PR if not seen before
                    if pr_key not in pr_comment_state:
                        pr_comment_state[pr_key] = {'last_comment_id': 0}

                    last_comment_id = pr_comment_state[pr_key]['last_comment_id']

                    # Get PR comments
                    comments = client.get_pull_comments(owner, repo_name, pr_number)
                    new_comments = [c for c in comments if c['id'] > last_comment_id]

                    # Get reviews and their comments
                    reviews = client.get_pull_reviews(owner, repo_name, pr_number)
                    for review in reviews:
                        if review.get('body'):
                            # Treat review body as a comment
                            review_comment = {
                                'id': review['id'] + 1000000,  # Offset to avoid conflict with comment ids
                                'body': review['body'],
                                'user': review.get('user', {}),
                                'created_at': review.get('submitted_at', review.get('created_at', '')),
                                'type': 'review'
                            }
                            if review_comment['id'] > last_comment_id:
                                new_comments.append(review_comment)

                        # Get review comments
                        review_comments = client.get_pull_review_comments(owner, repo_name, pr_number, review['id'])
                        for rc in review_comments:
                            rc['type'] = 'review_comment'
                            if rc['id'] > last_comment_id:
                                new_comments.append(rc)

                    if new_comments:
                        logger.info(f"Found {len(new_comments)} new comments/reviews on PR #{pr_number}")

                    for comment in new_comments:
                        logger.info(f"Processing new comment/review {comment['id']} on PR #{pr_number}")
                        # Check if already processed (has 'eyes' reaction)
                        try:
                            reactions = client.get_comment_reactions(owner, repo_name, comment['id'])
                            has_eyes = any(r.get('content') == 'eyes' for r in reactions)
                            if has_eyes:
                                logger.debug(f"Comment {comment['id']} already processed (has eyes reaction)")
                                last_comment_id = max(last_comment_id, comment['id'])
                                continue
                        except Exception as e:
                            logger.warning(f"Could not check reactions for comment {comment['id']}: {e}")

                        # Add 'eyes' reaction
                        try:
                            client.add_comment_reaction(owner, repo_name, comment['id'], 'eyes')
                            logger.debug(f"Added eyes reaction to comment {comment['id']}")
                        except Exception as e:
                            logger.warning(f"Could not add eyes reaction to comment {comment['id']}: {e}")

                        # Spawn subagent for this comment
                        try:
                            proc = subprocess.Popen(['python', 'subagent.py', '--comment', str(comment['id']), repo])
                            active_subprocesses.append(proc)
                            logger.info(f"Spawned subagent for comment {comment['id']} on PR #{pr_number} (PID: {proc.pid})")
                        except Exception as e:
                            logger.error(f"Failed to spawn subagent for comment {comment['id']}: {e}")

                        last_comment_id = max(last_comment_id, comment['id'])

                    pr_comment_state[pr_key]['last_comment_id'] = last_comment_id

            except Exception as e:
                logger.error(f"Error querying PRs for repo {repo}: {e}")

        # Clean up finished subprocesses
        active_subprocesses[:] = [proc for proc in active_subprocesses if proc.poll() is None]

        logger.info(f"Polling cycle completed, sleeping for {config.polling_frequency} seconds")
        time.sleep(config.polling_frequency)

    logger.info("Agent shutdown complete")

if __name__ == '__main__':
    main()
