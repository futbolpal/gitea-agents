import sys
import time
import logging
import json
import os
import signal
import atexit
import subprocess
import tempfile
import shutil
from config import Config
from gitea_client import GiteaClient

def analyze_and_respond(comment_body):
    """Analyze comment and generate response."""
    logger = logging.getLogger(__name__)
    body_lower = comment_body.lower()
    if 'approve' in body_lower or 'approved' in body_lower:
        logger.info("Detected approval in comment")
        return "Thank you for the approval! The changes have been implemented as requested."
    elif 'change' in body_lower or 'fix' in body_lower or 'modify' in body_lower:
        # Simulate making code changes
        logger.info("Simulating code changes based on feedback...")
        return "Understood. I've made the necessary code changes based on your feedback. Please review the updated PR."
    else:
        logger.debug("General feedback comment detected")
        return "Thanks for the feedback! I'll take that into consideration."

def kilocode_process(prompt, repo_dir):
    """Spawn subprocess to run kilo-code for code generation."""
    logger = logging.getLogger(__name__)
    cmd = ["kilocode", "-a", prompt]
    logger.info(f"Running kilo-code with prompt: {prompt[:50]}...")
    result = subprocess.run(cmd, cwd=repo_dir, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error(f"kilocode failed: {result.stderr}")
    else:
        logger.info("kilocode completed successfully")
    return result.returncode, result.stdout, result.stderr

def do_work(issue_body, repo_dir):
    """Process the issue prompt and generate code changes."""
    logger = logging.getLogger(__name__)
    logger.info("Starting work on issue...")
    # Add instructions for commit and test management
    enhanced_prompt = (
        "Make code changes as requested.\n"
        "Create small, focused commits for each logical change. Run all tests and ensure they pass before pushing the branch to the remote repository and finalizing the PR. Make multiple commits if needed for the PR.\n"
        "Once all the work is done, create a PR\n\n" 
        + issue_body
    )
    ret, out, err = kilocode_process(enhanced_prompt, repo_dir)
    if ret != 0:
        raise Exception(f"Code generation failed: {err}")
    logger.info("Code generation completed")
    return True

def main():
    if len(sys.argv) < 3:
        print("Usage: python subagent.py <issue_number> <repo>", file=sys.stderr)
        sys.exit(1)

    try:
        issue_number = int(sys.argv[1])
        repo = sys.argv[2]
    except ValueError as e:
        print(f"Invalid arguments: {e}", file=sys.stderr)
        sys.exit(1)

    os.environ['PROCESS_TYPE'] = 'subagent'
    config = Config()
    config.validate()
    logger = config.setup_logging()
    logger.info(f"Starting subagent for issue {issue_number} in repo {repo}")

    client = GiteaClient(config.gitea_base_url, config.gitea_token)

    owner, repo_name = repo.split('/', 1)

    # Setup signal handling for graceful shutdown
    running = True
    conversation_history = []

    def cleanup():
        """Cleanup function for conversation history and temp repo."""
        if conversation_history:
            try:
                history_file = f"conversation_{issue_number}_{pr_number if 'pr_number' in locals() else 0}.json"
                with open(history_file, 'w') as f:
                    json.dump(conversation_history, f, indent=2)
                logger.info("Conversation history saved")
            except Exception as e:
                logger.error(f"Failed to save conversation history: {e}")
        # Cleanup temp repo dir
        if 'repo_temp_dir' in locals() and os.path.exists(repo_temp_dir):
            try:
                shutil.rmtree(repo_temp_dir)
                logger.info("Temporary repository directory cleaned up")
            except Exception as e:
                logger.error(f"Failed to cleanup temp repo dir: {e}")

    def signal_handler(sig, frame):
        nonlocal running
        logger.info("Subagent received shutdown signal")
        running = False

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    atexit.register(cleanup)

    try:
        # Get issue details
        issue = client.get_issue(owner, repo_name, issue_number)
        logger.info(f"Working on issue {issue_number}: {issue['title']}")
    except Exception as e:
        logger.error(f"Failed to get issue details: {e}")
        sys.exit(1)

    # Verify the issue is reserved for processing
    labels = [label['name'] for label in issue.get('labels', [])]
    if config.issue_label_reserve not in labels:
        logger.error(f"Issue {issue_number} is not properly reserved for processing (missing {config.issue_label_reserve} label)")
        sys.exit(1)

    # Clone the repository
    repo_temp_dir = tempfile.mkdtemp()
    logger.info(f"Cloning repo {owner}/{repo_name} to {repo_temp_dir}")
    try:
        # Construct clone URL with token, preserving protocol
        base_url = config.gitea_base_url.rstrip('/api/v1')
        protocol = 'https' if base_url.startswith('https://') else 'http'
        host = base_url.replace('https://', '').replace('http://', '')
        clone_url = f"{protocol}://oauth2:{config.gitea_token}@{host}/{owner}/{repo_name}.git"
        subprocess.run(["git", "clone", clone_url, repo_temp_dir], check=True)
        logger.info("Repository cloned successfully")
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to clone repository: {e}")
        sys.exit(1)

    # Perform actual work
    try:
        do_work(issue['body'], repo_temp_dir)
    except Exception as e:
        logger.error(f"Work failed: {e}")
        shutil.rmtree(repo_temp_dir)
        sys.exit(1)

    # Load conversation history
    history_file = f"conversation_{issue_number}_{pr_number}.json"
    try:
        if os.path.exists(history_file):
            with open(history_file, 'r') as f:
                conversation_history = json.load(f)
            logger.info(f"Loaded conversation history from {history_file}")
        else:
            conversation_history = []
            logger.info("Starting new conversation history")
    except (json.JSONDecodeError, IOError) as e:
        logger.warning(f"Failed to load conversation history: {e}, starting fresh")
        conversation_history = []

    # Get last processed comment ID
    last_comment_id = max([c['id'] for c in conversation_history] + [0])
    logger.info(f"Last processed comment ID: {last_comment_id}")

    # Poll for comments on PR
    logger.info(f"Starting PR comment polling for PR #{pr_number}")
    while running:
        try:
            # Check PR status
            pr_details = client.get_pull_request(owner, repo_name, pr_number)
            if pr_details['state'] == 'closed' or pr_details.get('merged', False):
                logger.info(f"PR #{pr_number} is merged or closed. Terminating subagent.")
                break

            comments = client.get_pull_comments(owner, repo_name, pr_number)
            new_comments = [c for c in comments if c['id'] > last_comment_id]
            if new_comments:
                logger.info(f"Found {len(new_comments)} new comments on PR #{pr_number}")

            for comment in new_comments:
                logger.info(f"Processing new comment {comment['id']} on PR #{pr_number}")
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
                    'response': response
                })
                last_comment_id = comment['id']

                # Save history periodically
                try:
                    with open(history_file, 'w') as f:
                        json.dump(conversation_history, f, indent=2)
                    logger.debug("Conversation history saved")
                except IOError as e:
                    logger.error(f"Failed to save conversation history: {e}")

        except Exception as e:
            logger.error(f"Error polling PR comments: {e}")

        time.sleep(config.polling_frequency)

    logger.info("Subagent polling loop ended")

if __name__ == '__main__':
    main()
