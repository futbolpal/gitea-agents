import time
import signal
import subprocess
import logging
import os
import atexit
import json
import psutil
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
    active_subprocesses = {}  # pid -> {'proc': Popen, 'work_item': str, 'id': int, 'repo': str}
    pr_comment_state = {}  # (repo, pr_number) -> {'last_comment_id': int}
    state_file = 'orchestration_state.json'

    # Load persisted state
    try:
        if os.path.exists(state_file):
            with open(state_file, 'r') as f:
                persisted_state = json.load(f)
            # Check which PIDs are still running
            for pid_str, info in persisted_state.get('active_subprocesses', {}).items():
                pid = int(pid_str)
                if psutil.pid_exists(pid):
                    # Keep the info but can't restore Popen
                    active_subprocesses[pid] = info
                    active_subprocesses[pid]['proc'] = None
            pr_comment_state.update(persisted_state.get('pr_comment_state', {}))
            logger.info(f"Loaded persisted state: {len(active_subprocesses)} active subprocesses")
    except Exception as e:
        logger.warning(f"Could not load persisted state: {e}")
        active_subprocesses = {}
        pr_comment_state = {}

    def save_state():
        """Persist current state to disk."""
        try:
            state = {
                'active_subprocesses': {
                    str(pid): {
                        'work_item': info['work_item'],
                        'id': info['id'],
                        'repo': info['repo']
                    } for pid, info in active_subprocesses.items()
                },
                'pr_comment_state': pr_comment_state
            }
            with open(state_file, 'w') as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            logger.error(f"Could not save state: {e}")

    def cleanup_subprocesses():
        """Cleanup active subprocesses on shutdown."""
        logger.info("Cleaning up active subprocesses...")
        for pid, proc_info in active_subprocesses.items():
            proc = proc_info['proc']
            if proc.poll() is None:  # Still running
                try:
                    proc.terminate()
                    proc.wait(timeout=5)
                    logger.info(f"Terminated subprocess {pid} for {proc_info['work_item']} {proc_info['id']}")
                except subprocess.TimeoutExpired:
                    proc.kill()
                    logger.warning(f"Force killed subprocess {pid}")
                except Exception as e:
                    logger.error(f"Error terminating subprocess {pid}: {e}")

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
                    # Check if already reserved but no active subprocess (work not done)
                    active_issue_pids = [pid for pid, info in active_subprocesses.items()
                                       if info['work_item'] == 'issue' and info['id'] == issue['number']]
                    if config.issue_label_reserve in labels:
                        if not active_issue_pids:
                            logger.info(f"Reserved issue {issue['number']} has no active subprocess, respawning")
                            # Respawn subagent
                            try:
                                proc = subprocess.Popen(['python', 'subagent.py', str(issue['number']), repo])
                                active_subprocesses[proc.pid] = {
                                    'proc': proc,
                                    'work_item': 'issue',
                                    'id': issue['number'],
                                    'repo': repo
                                }
                                logger.info(f"Respawned subagent for reserved issue {issue['number']} in {repo} (PID: {proc.pid})")
                            except Exception as e:
                                logger.error(f"Failed to respawn subagent for issue {issue['number']}: {e}")
                    elif not active_issue_pids:
                        logger.info(f"Reserving issue {issue['number']} in {repo}")
                        try:
                            # Reserve the issue
                            new_labels = labels + [config.issue_label_reserve]
                            client.update_issue_labels(owner, repo_name, issue['number'], new_labels)
                            # Spawn subagent
                            proc = subprocess.Popen(['python', 'subagent.py', str(issue['number']), repo])
                            active_subprocesses[proc.pid] = {
                                'proc': proc,
                                'work_item': 'issue',
                                'id': issue['number'],
                                'repo': repo
                            }
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
                        # Check reactions
                        try:
                            reactions = client.get_comment_reactions(owner, repo_name, comment['id'])
                            has_eyes = any(r.get('content') == 'eyes' for r in reactions)
                            has_heart = any(r.get('content') == 'heart' for r in reactions)
                        except Exception as e:
                            logger.warning(f"Could not check reactions for comment {comment['id']}: {e}")
                            has_eyes = has_heart = False

                        # Check if already addressed (has 'heart' reaction)
                        if has_heart:
                            logger.debug(f"Comment {comment['id']} already addressed (has heart reaction)")
                            last_comment_id = max(last_comment_id, comment['id'])
                            continue

                        # Check if reserved but work not done (has 'eyes' but no active subprocess)
                        active_comment_pids = [pid for pid, info in active_subprocesses.items()
                                             if info['work_item'] == 'comment' and info['id'] == comment['id']]
                        if has_eyes and not active_comment_pids:
                            logger.info(f"Reserved comment {comment['id']} has no active subprocess, respawning")
                            # Respawn subagent
                            try:
                                proc = subprocess.Popen(['python', 'subagent.py', '--comment', str(comment['id']), repo])
                                active_subprocesses[proc.pid] = {
                                    'proc': proc,
                                    'work_item': 'comment',
                                    'id': comment['id'],
                                    'repo': repo
                                }
                                logger.info(f"Respawned subagent for reserved comment {comment['id']} on PR #{pr_number} (PID: {proc.pid})")
                            except Exception as e:
                                logger.error(f"Failed to respawn subagent for comment {comment['id']}: {e}")
                            last_comment_id = max(last_comment_id, comment['id'])
                            continue

                        # New comment, add 'eyes' reaction and spawn
                        if not has_eyes:
                            # Add 'eyes' reaction
                            try:
                                client.add_comment_reaction(owner, repo_name, comment['id'], 'eyes')
                                logger.debug(f"Added eyes reaction to comment {comment['id']}")
                            except Exception as e:
                                logger.warning(f"Could not add eyes reaction to comment {comment['id']}: {e}")

                        # Spawn subagent for this comment
                        try:
                            proc = subprocess.Popen(['python', 'subagent.py', '--comment', str(comment['id']), repo])
                            active_subprocesses[proc.pid] = {
                                'proc': proc,
                                'work_item': 'comment',
                                'id': comment['id'],
                                'repo': repo
                            }
                            logger.info(f"Spawned subagent for comment {comment['id']} on PR #{pr_number} (PID: {proc.pid})")
                        except Exception as e:
                            logger.error(f"Failed to spawn subagent for comment {comment['id']}: {e}")

                        last_comment_id = max(last_comment_id, comment['id'])

                    pr_comment_state[pr_key]['last_comment_id'] = last_comment_id

            except Exception as e:
                logger.error(f"Error querying PRs for repo {repo}: {e}")

        # Clean up finished subprocesses
        finished_pids = [pid for pid, info in active_subprocesses.items() if info['proc'].poll() is not None]
        for pid in finished_pids:
            logger.info(f"Subprocess {pid} for {active_subprocesses[pid]['work_item']} {active_subprocesses[pid]['id']} finished")
            del active_subprocesses[pid]

        # Save state periodically
        save_state()

        logger.info(f"Polling cycle completed, sleeping for {config.polling_frequency} seconds")
        time.sleep(config.polling_frequency)

    logger.info("Agent shutdown complete")

if __name__ == '__main__':
    main()
