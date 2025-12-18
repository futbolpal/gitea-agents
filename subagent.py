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
from utils import analyze_and_respond

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
        "Do not create any new issues or pull requests. Only make code changes as requested.\n"
        "Create small, focused commits for each logical change. Run all tests and ensure they pass before pushing the branch to the remote repository and finalizing the PR. Make multiple commits if needed for the PR.\n\n"
        + issue_body
    )
    ret, out, err = kilocode_process(enhanced_prompt, repo_dir)
    if ret != 0:
        raise Exception(f"Code generation failed: {err}")
    logger.info("Code generation completed")
    return True

def main():
    if len(sys.argv) < 3:
        print("Usage: python subagent.py <issue_number> <repo> OR python subagent.py --comment <comment_id> <repo>", file=sys.stderr)
        sys.exit(1)

    comment_id = None
    issue_number = None
    repo = None

    if sys.argv[1] == '--comment':
        if len(sys.argv) < 4:
            print("Usage: python subagent.py --comment <comment_id> <repo>", file=sys.stderr)
            sys.exit(1)
        try:
            comment_id = int(sys.argv[2])
            repo = sys.argv[3]
        except ValueError as e:
            print(f"Invalid arguments: {e}", file=sys.stderr)
            sys.exit(1)
    else:
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
    if issue_number:
        logger.info(f"Starting subagent for issue {issue_number} in repo {repo}")
    else:
        logger.info(f"Starting subagent for comment {comment_id} in repo {repo}")

    client = GiteaClient(config.gitea_base_url, config.gitea_token)

    owner, repo_name = repo.split('/', 1)

    # Setup signal handling for graceful shutdown
    running = True
    conversation_history = []

    def cleanup():
        """Cleanup function for conversation history and temp repo."""
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

    if comment_id:
        # Handle comment processing
        try:
            # Get comment details (assuming it's an issue comment)
            comment = client._make_request('GET', f'{client.base_url}/repos/{owner}/{repo_name}/issues/comments/{comment_id}')
            logger.info(f"Processing comment {comment_id}: {comment['body'][:50]}...")

            # Analyze and respond
            response = analyze_and_respond(comment['body'])
            if response:
                try:
                    # For now, assume it's a PR comment and respond
                    # TODO: Determine if it's issue or PR comment and respond appropriately
                    client.create_pull_comment(owner, repo_name, comment_id, response)
                    logger.info(f"Responded to comment {comment_id}: {response[:50]}...")

                    # Add 'heart' reaction to indicate addressed
                    client.add_comment_reaction(owner, repo_name, comment_id, 'heart')
                    logger.info(f"Added heart reaction to comment {comment_id}")
                except Exception as e:
                    logger.error(f"Failed to respond to comment {comment_id}: {e}")

            logger.info(f"Subagent completed processing comment {comment_id}")
            sys.exit(0)

        except Exception as e:
            logger.error(f"Failed to process comment {comment_id}: {e}")
            sys.exit(1)

    # Issue processing
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

    # Handle branch creation, commits, and pushing since kilo-code may not do it
    head_branch = f"fix-issue-{issue_number}"
    try:
        logger.debug(f"Creating branch {head_branch}")
        # Create and checkout branch
        subprocess.run(['git', 'checkout', '-b', head_branch], cwd=repo_temp_dir, check=True)
        logger.info(f"Created and checked out branch {head_branch}")

        logger.debug("Adding changes to git")
        # Add all changes
        subprocess.run(['git', 'add', '.'], cwd=repo_temp_dir, check=True)

        # Check if there are staged changes
        result = subprocess.run(['git', 'diff', '--cached', '--quiet'], cwd=repo_temp_dir)
        logger.debug(f"Git diff result: {result.returncode}")
        if result.returncode != 0:  # There are changes
            logger.debug("Committing changes")
            # Commit
            subprocess.run(['git', 'commit', '-m', f'Fix issue #{issue_number}: {issue["title"]}'], cwd=repo_temp_dir, check=True)
            logger.info(f"Committed changes for issue {issue_number}")

            logger.debug(f"Pushing branch {head_branch}")
            # Push
            subprocess.run(['git', 'push', 'origin', head_branch], cwd=repo_temp_dir, check=True)
            logger.info(f"Pushed branch {head_branch} to remote")
        else:
            logger.warning("No changes to commit")
            # Still create PR if branch exists, but since no push, branch won't exist
            # For now, assume changes are made
    except subprocess.CalledProcessError as e:
        logger.error(f"Git operation failed: {e}")
        shutil.rmtree(repo_temp_dir)
        sys.exit(1)
    try:
        logger.debug(f"Creating PR with head={head_branch}")
        # Get the default branch for the repository
        repo_info = client.get_repo(owner, repo_name)
        default_branch = repo_info.get('default_branch', 'main')
        logger.info(f"Using default branch: {default_branch}")
        pr = client.create_pull_request(
            owner, repo_name,
            f"Fix issue #{issue_number}: {issue['title']}",
            head_branch,
            default_branch,
            f"Closes #{issue_number}\n\n{issue['body']}"
        )
        pr_number = pr['number']
        logger.info(f"Created PR #{pr_number} for issue {issue_number}")
    except Exception as e:
        logger.error(f"Failed to create PR for issue {issue_number}: {e}")
        sys.exit(1)

    logger.info(f"Subagent completed work for issue {issue_number}, PR #{pr_number} created")

if __name__ == '__main__':
    main()
