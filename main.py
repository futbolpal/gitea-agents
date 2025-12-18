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

def is_issue_completed(client, owner, repo_name, issue_number, config, logger):
    """Check if an issue is completed (has the in-review label)."""
    try:
        issue = client.get_issue(owner, repo_name, issue_number)
        labels = [label['name'] for label in issue.get('labels', [])]
        return config.issue_label_in_review in labels
    except Exception as e:
        logger.warning(f"Could not check if issue {issue_number} is completed: {e}")
        return False

def is_comment_completed(client, owner, repo_name, comment_id, logger):
    """Check if a comment is completed (has 'heart' reaction)."""
    try:
        reactions = client.get_comment_reactions(owner, repo_name, comment_id)
        return any(r.get('content') == 'heart' for r in reactions)
    except Exception as e:
        logger.warning(f"Could not check if comment {comment_id} is completed: {e}")
        return False

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
        {"name": config.issue_label_in_review, "color": "ffff00", "description": "Issue has PR created and is under review"}
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
    state_file = '/data/orchestration_state.json'

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
                    active_subprocesses[pid] = info.copy()
                    active_subprocesses[pid]['proc'] = None
            # Convert string keys back to tuples for pr_comment_state
            for key_str, value in persisted_state.get('pr_comment_state', {}).items():
                repo, pr_number = key_str.split('|', 1)
                pr_comment_state[(repo, int(pr_number))] = value
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
                        'repo': info['repo'],
                        'pr_number': info.get('pr_number'),
                        'review_id': info.get('review_id'),
                        'retry_count': info.get('retry_count', 0)
                    } for pid, info in active_subprocesses.items()
                },
                'pr_comment_state': {
                    f"{repo}|{pr_number}": value
                    for (repo, pr_number), value in pr_comment_state.items()
                }
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
                    # Check spawning condition: !reserved || (reserved && !hasProcWorker && !completed)
                    reserved = config.issue_label_reserve in labels
                    active_issue_pids = [pid for pid, info in active_subprocesses.items()
                                       if info['work_item'] == 'issue' and info['id'] == issue['number']]
                    has_proc_worker = len(active_issue_pids) > 0
                    completed = is_issue_completed(client, owner, repo_name, issue['number'], config, logger)

                    should_spawn = not reserved or (reserved and not has_proc_worker and not completed)

                    if should_spawn:
                        if not reserved:
                            logger.info(f"Reserving issue {issue['number']} in {repo}")
                            try:
                                # Reserve the issue
                                new_labels = labels + [config.issue_label_reserve]
                                client.update_issue_labels(owner, repo_name, issue['number'], new_labels)
                            except Exception as e:
                                logger.error(f"Failed to reserve issue {issue['number']}: {e}")
                                continue

                        # Spawn subagent
                        try:
                            proc = subprocess.Popen(['python', 'subagent.py', '--issue', str(issue['number']), repo])
                            active_subprocesses[proc.pid] = {
                                'proc': proc,
                                'work_item': 'issue',
                                'id': issue['number'],
                                'repo': repo,
                                'retry_count': 0
                            }
                            logger.info(f"Spawned subagent for issue {issue['number']} in {repo} (PID: {proc.pid})")
                        except Exception as e:
                            logger.error(f"Failed to spawn subagent for issue {issue['number']}: {e}")
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
                            if reactions:
                                has_eyes = any(r.get('content') == 'eyes' for r in reactions)
                                has_heart = any(r.get('content') == 'heart' for r in reactions)
                            else:
                                has_eyes = has_heart = False
                        except Exception as e:
                            logger.warning(f"Could not check reactions for comment {comment['id']}: {e}")
                            has_eyes = has_heart = False

                        # Check if already addressed (has 'heart' reaction)
                        if has_heart:
                            logger.debug(f"Comment {comment['id']} already addressed (has heart reaction)")
                            last_comment_id = max(last_comment_id, comment['id'])
                            continue

                        # Check spawning condition: !reserved || (reserved && !hasProcWorker && !completed)
                        reserved = has_eyes
                        active_comment_pids = [pid for pid, info in active_subprocesses.items()
                                              if info['work_item'] != 'issue' and info['id'] == comment['id']]
                        has_proc_worker = len(active_comment_pids) > 0
                        completed = is_comment_completed(client, owner, repo_name, comment['id'], logger)

                        should_spawn = not reserved or (reserved and not has_proc_worker and not completed)

                        if should_spawn:
                            # Add 'eyes' reaction if not already
                            if not has_eyes:
                                try:
                                    client.add_comment_reaction(owner, repo_name, comment['id'], 'eyes')
                                    logger.debug(f"Added eyes reaction to comment {comment['id']}")
                                except Exception as e:
                                    logger.warning(f"Could not add eyes reaction to comment {comment['id']}: {e}")

                            # Spawn subagent
                            work_item = comment.get('type', 'pr_comment')
                            try:
                                if work_item == 'review_comment':
                                    review_id = comment.get('pull_request_review_id')
                                    proc = subprocess.Popen(['python', 'subagent.py', '--comment', str(comment['id']), repo, str(pr_number), work_item, str(review_id)])
                                else:
                                    proc = subprocess.Popen(['python', 'subagent.py', '--comment', str(comment['id']), repo, str(pr_number), work_item])
                                active_subprocesses[proc.pid] = {
                                    'proc': proc,
                                    'work_item': work_item,
                                    'id': comment['id'],
                                    'repo': repo,
                                    'pr_number': pr_number,
                                    'retry_count': 0
                                }
                                if work_item == 'review_comment':
                                    active_subprocesses[proc.pid]['review_id'] = review_id
                                logger.info(f"Spawned subagent for {work_item} {comment['id']} on PR #{pr_number} (PID: {proc.pid})")
                            except Exception as e:
                                logger.error(f"Failed to spawn subagent for {work_item} {comment['id']}: {e}")

                        last_comment_id = max(last_comment_id, comment['id'])

                    pr_comment_state[pr_key]['last_comment_id'] = last_comment_id

            except Exception as e:
                logger.error(f"Error querying PRs for repo {repo}: {e}")

        # Clean up finished subprocesses
        finished_pids = [pid for pid, info in active_subprocesses.items() if info['proc'].poll() is not None]
        for pid in finished_pids:
            proc_info = active_subprocesses[pid]
            returncode = proc_info['proc'].returncode
            logger.info(f"Subprocess {pid} for {proc_info['work_item']} {proc_info['id']} finished with returncode {returncode}")
            if returncode == 0:
                if proc_info['work_item'] == 'issue':
                    # Update issue labels: add in_review (keep reserve)
                    owner, repo_name = proc_info['repo'].split('/', 1)
                    issue_number = proc_info['id']
                    try:
                        # Get current labels
                        issue = client.get_issue(owner, repo_name, issue_number)
                        current_labels = [label['name'] for label in issue.get('labels', [])]
                        if config.issue_label_in_review not in current_labels:
                            new_labels = current_labels + [config.issue_label_in_review]
                            client.update_issue_labels(owner, repo_name, issue_number, new_labels)
                            logger.info(f"Updated issue {issue_number} labels: added {config.issue_label_in_review}")
                    except Exception as e:
                        logger.error(f"Failed to update issue labels for {issue_number}: {e}")
                else:
                    # Add heart reaction to indicate addressed
                    owner, repo_name = proc_info['repo'].split('/', 1)
                    try:
                        client.add_comment_reaction(owner, repo_name, proc_info['id'], 'heart')
                        logger.info(f"Added heart reaction to {proc_info['work_item']} {proc_info['id']}")
                    except Exception as e:
                        logger.error(f"Failed to add heart reaction to {proc_info['work_item']} {proc_info['id']}: {e}")
                del active_subprocesses[pid]
            else:
                # Failure, check retry
                if proc_info['retry_count'] < 3:
                    proc_info['retry_count'] += 1
                    logger.info(f"Retrying {proc_info['work_item']} {proc_info['id']} (attempt {proc_info['retry_count']})")
                    try:
                        if proc_info['work_item'] == 'issue':
                            proc = subprocess.Popen(['python', 'subagent.py', '--issue', str(proc_info['id']), proc_info['repo']])
                        else:
                            pr_number = proc_info['pr_number']
                            if proc_info['work_item'] == 'review_comment':
                                review_id = proc_info['review_id']
                                proc = subprocess.Popen(['python', 'subagent.py', '--comment', str(proc_info['id']), proc_info['repo'], str(pr_number), proc_info['work_item'], str(review_id)])
                            else:
                                proc = subprocess.Popen(['python', 'subagent.py', '--comment', str(proc_info['id']), proc_info['repo'], str(pr_number), proc_info['work_item']])
                        proc_info['proc'] = proc
                        # Keep the same pid key? No, new pid.
                        active_subprocesses[proc.pid] = proc_info
                        logger.info(f"Respawned subagent for {proc_info['work_item']} {proc_info['id']} (PID: {proc.pid})")
                    except Exception as e:
                        logger.error(f"Failed to respawn subagent for {proc_info['work_item']} {proc_info['id']}: {e}")
                else:
                    logger.error(f"Subagent for {proc_info['work_item']} {proc_info['id']} failed after 3 attempts")
                del active_subprocesses[pid]

        # Save state periodically
        save_state()

        logger.info(f"Polling cycle completed, sleeping for {config.polling_frequency} seconds")
        time.sleep(config.polling_frequency)

    logger.info("Agent shutdown complete")

if __name__ == '__main__':
    main()
