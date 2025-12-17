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
    active_prs = {}  # issue_number -> {'pr_number': int, 'last_comment_id': int, 'conversation_history': list}

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
                            active_prs[str(issue['number'])] = {'repo': repo, 'pr_number': None, 'last_comment_id': 0, 'conversation_history': []}
                            logger.info(f"Spawned subagent for issue {issue['number']} in {repo} (PID: {proc.pid})")
                        except Exception as e:
                            logger.error(f"Failed to reserve or spawn subagent for issue {issue['number']}: {e}")
            except Exception as e:
                logger.error(f"Error processing repo {repo}: {e}")


        # Check for new PR files from subagents
        for issue_number in list(active_prs.keys()):
            pr_file = f"pr_{issue_number}.txt"
            if os.path.exists(pr_file):
                try:
                    with open(pr_file, "r") as f:
                        pr_number = int(f.read().strip())
                    active_prs[issue_number]['pr_number'] = pr_number
                    logger.info(f"Found PR #{pr_number} for issue {issue_number}")
                    os.remove(pr_file)  # Clean up
                except (ValueError, IOError) as e:
                    logger.error(f"Error reading PR file for issue {issue_number}: {e}")

        # Poll for comments on active PRs
        for issue_number, pr_data in list(active_prs.items()):
            pr_number = pr_data.get('pr_number')
            if not pr_number:
                continue
            repo = pr_data['repo']
            owner, repo_name = repo.split('/', 1)

            # Load conversation history
            history_file = f"conversation_{issue_number}_{pr_number}.json"
            conversation_history = pr_data['conversation_history']
            if not conversation_history:
                try:
                    if os.path.exists(history_file):
                        with open(history_file, 'r') as f:
                            conversation_history = json.load(f)
                        pr_data['conversation_history'] = conversation_history
                        logger.info(f"Loaded conversation history from {history_file}")
                    else:
                        conversation_history = []
                        pr_data['conversation_history'] = conversation_history
                        logger.info("Starting new conversation history")
                except (json.JSONDecodeError, IOError) as e:
                    logger.warning(f"Failed to load conversation history: {e}, starting fresh")
                    conversation_history = []
                    pr_data['conversation_history'] = conversation_history

            last_comment_id = pr_data['last_comment_id']

            try:
                # Check PR status
                pr_details = client.get_pull_request(owner, repo_name, pr_number)
                if pr_details['state'] == 'closed' or pr_details.get('merged', False):
                    logger.info(f"PR #{pr_number} is merged or closed. Removing from active PRs.")
                    del active_prs[issue_number]
                    continue

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
                    # Analyze and respond
                    response = analyze_and_respond(comment['body'])
                    if response:
                        try:
                            client.create_pull_comment(owner, repo_name, pr_number, response)
                            logger.info(f"Responded to comment {comment['id']}: {response[:50]}...")
                        except Exception as e:
                            logger.error(f"Failed to create comment response: {e}")
                            response = None  # Don't add to history if failed
                    # Add to history
                    conversation_history.append({
                        'id': comment['id'],
                        'body': comment['body'],
                        'response': response,
                        'type': comment.get('type', 'comment')
                    })
                    last_comment_id = max(last_comment_id, comment['id'])

                pr_data['last_comment_id'] = last_comment_id

                # Save history periodically
                try:
                    with open(history_file, 'w') as f:
                        json.dump(conversation_history, f, indent=2)
                    logger.debug("Conversation history saved")
                except IOError as e:
                    logger.error(f"Failed to save conversation history: {e}")

            except Exception as e:
                logger.error(f"Error polling PR comments for {repo} PR #{pr_number}: {e}")

        # Clean up finished subprocesses
        active_subprocesses[:] = [proc for proc in active_subprocesses if proc.poll() is None]

        logger.info(f"Polling cycle completed, sleeping for {config.polling_frequency} seconds")
        time.sleep(config.polling_frequency)

    logger.info("Agent shutdown complete")

if __name__ == '__main__':
    main()
