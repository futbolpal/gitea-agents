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
from agent_runner import run_agent

def _strip_api_suffix(base_url):
    if base_url.endswith('/api/v1'):
        return base_url[:-7]
    return base_url

def _safe_run(cmd, cwd):
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        output = result.stdout.strip()
        if output:
            return output
    except Exception:
        return ""
    return ""

def _read_first_existing(repo_dir, filenames, max_chars):
    for name in filenames:
        path = os.path.join(repo_dir, name)
        if os.path.isfile(path):
            try:
                with open(path, 'r', encoding='utf-8', errors='replace') as handle:
                    return handle.read(max_chars).strip()
            except Exception:
                continue
    return ""

def _detect_stack(repo_dir):
    markers = {
        "python": ["pyproject.toml", "requirements.txt", "setup.py"],
        "node": ["package.json", "pnpm-lock.yaml", "yarn.lock"],
        "go": ["go.mod"],
        "rust": ["Cargo.toml"],
        "ruby": ["Gemfile"],
        "java": ["pom.xml", "build.gradle", "build.gradle.kts"],
        "dotnet": ["*.csproj", "*.fsproj"],
        "php": ["composer.json"],
    }
    detected = []
    for label, files in markers.items():
        for pattern in files:
            if "*" in pattern:
                matches = [f for f in os.listdir(repo_dir) if f.endswith(pattern.split("*")[-1])]
                if matches:
                    detected.append(label)
                    break
            else:
                if os.path.exists(os.path.join(repo_dir, pattern)):
                    detected.append(label)
                    break
    return detected

def _build_context(repo_dir, config, issue=None, pr=None, comment=None):
    max_chars = config.max_context_chars
    parts = []

    parts.append("Context:")
    tech = _detect_stack(repo_dir)
    if tech:
        parts.append(f"- tech: {', '.join(sorted(set(tech)))}")

    readme = _read_first_existing(
        repo_dir,
        ["README.md", "README.txt", "README.rst"],
        max_chars // 4
    )
    if readme:
        parts.append("- readme_excerpt:")
        parts.append(readme)

    git_status = _safe_run(["git", "status", "--short"], repo_dir)
    if git_status:
        parts.append("- git_status:")
        parts.append(git_status)

    git_last = _safe_run(["git", "log", "-1", "--oneline"], repo_dir)
    if git_last:
        parts.append(f"- last_commit: {git_last}")

    if issue:
        parts.append(f"- issue: #{issue.get('number')} {issue.get('title', '').strip()}")
        labels = [l.get('name') for l in issue.get('labels', []) if l.get('name')]
        if labels:
            parts.append(f"- issue_labels: {', '.join(labels)}")

    if pr:
        parts.append(f"- pr: #{pr.get('number')} {pr.get('title', '').strip()}")
        head = pr.get('head', {}).get('ref')
        base = pr.get('base', {}).get('ref')
        if head or base:
            parts.append(f"- pr_branches: {head} -> {base}")

    if comment:
        parts.append(f"- comment_type: {comment.get('type')}")
        if comment.get('path'):
            parts.append(f"- comment_path: {comment.get('path')}")
        if comment.get('position'):
            parts.append(f"- comment_line: {comment.get('position')}")
        if comment.get('diff_hunk'):
            parts.append("- comment_diff:")
            parts.append(comment.get('diff_hunk'))

    context = "\n".join(parts).strip()
    if len(context) > max_chars:
        context = context[:max_chars].rstrip()
    return context

def _make_repo_temp_dir(config, logger):
    preferred_dir = config.workspace_dir
    if preferred_dir:
        try:
            return tempfile.mkdtemp(dir=preferred_dir)
        except Exception as exc:
            logger.warning("Failed to create temp repo in %s: %s", preferred_dir, exc)
    return tempfile.mkdtemp()

def _load_prompt_template(config):
    default_template = (
        "Do not create any new issues or pull requests. Only make code changes as requested.\n"
        "Create small, focused commits for each logical change.\n"
        "Make multiple commits if needed for the PR.\n"
        "Start by examining any changes on the current branch to understand the work that has already been done.\n"
        "\n"
        "{prompt}"
    )
    path = config.prompt_template_path
    if not path:
        return default_template
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            template = handle.read().strip()
        if not template:
            return default_template
        return template
    except FileNotFoundError:
        return default_template
    except Exception as exc:
        logger = logging.getLogger(__name__)
        logger.warning("Failed to load prompt template from %s: %s", path, exc)
        return default_template

def do_work(prompt, repo_dir, config, head_branch):
    """Process the prompt and generate code changes."""
    logger = logging.getLogger(__name__)
    logger.info("Starting work...")
    logger.info(f"Checking out branch {head_branch}")
    subprocess.run(['git', 'checkout', head_branch], cwd=repo_dir, check=True)
    # Add instructions for commit and test management
    template = _load_prompt_template(config)
    enhanced_prompt = template.format(prompt=prompt)
    result, output_path = run_agent(enhanced_prompt, repo_dir, config)
    if result.returncode != 0:
        logger.error("Agent CLI failed, output at %s", output_path)
        raise Exception(f"Code generation failed: {result.stderr}")
    logger.info("Code generation completed (output at %s)", output_path)
    return True

def main():
    if len(sys.argv) < 4:
        print("Usage: python subagent.py --issue <issue_number> <repo> OR python subagent.py --comment <comment_id> <repo> <pr_number> <type> [review_id]", file=sys.stderr)
        sys.exit(1)

    comment_id = None
    issue_number = None
    repo = None
    pr_number = None
    comment_type = None
    review_id = None

    if sys.argv[1] == '--issue':
        if len(sys.argv) < 4:
            print("Usage: python subagent.py --issue <issue_number> <repo>", file=sys.stderr)
            sys.exit(1)
        try:
            issue_number = int(sys.argv[2])
            repo = sys.argv[3]
        except ValueError as e:
            print(f"Invalid arguments: {e}", file=sys.stderr)
            sys.exit(1)
    elif sys.argv[1] == '--comment':
        if len(sys.argv) < 6:
            print("Usage: python subagent.py --comment <comment_id> <repo> <pr_number> <type> [review_id]", file=sys.stderr)
            sys.exit(1)
        try:
            comment_id = int(sys.argv[2])
            repo = sys.argv[3]
            pr_number = int(sys.argv[4])
            comment_type = sys.argv[5]
            if comment_type == 'review_comment':
                if len(sys.argv) < 7:
                    print("Usage: python subagent.py --comment <comment_id> <repo> <pr_number> review_comment <review_id>", file=sys.stderr)
                    sys.exit(1)
                review_id = int(sys.argv[6])
        except ValueError as e:
            print(f"Invalid arguments: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        print("Invalid mode. Use --issue or --comment", file=sys.stderr)
        sys.exit(1)

    os.environ['PROCESS_TYPE'] = 'subagent'
    config = Config()
    config.validate()
    logger = config.setup_logging()
    if issue_number:
        logger.info(f"Starting subagent for issue {issue_number} in repo {repo}")
    else:
        logger.info(f"Starting subagent for comment {comment_id} on PR #{pr_number} in repo {repo}")

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
        # Handle comment processing - make code changes on PR branch
        try:
            # Get comment details based on type
            if comment_type == 'pr_comment':
                comment = client._make_request('GET', f'{client.base_url}/repos/{owner}/{repo_name}/issues/comments/{comment_id}')
                body = comment['body']
                context = ""
            elif comment_type == 'review_comment':
                comment = client.get_pull_review_comment(owner, repo_name, pr_number, review_id, comment_id)
                body = comment['body']
                path = comment.get('path', '')
                position = comment.get('position')
                diff_hunk = comment.get('diff_hunk', '')

                context_parts = []
                if path:
                    if position:
                        context_parts.append(f"on {path} at line {position}")
                    else:
                        context_parts.append(f"on {path}")

                if diff_hunk:
                    context_parts.append(f"diff:\n{diff_hunk}")

                context = (" " + " | ".join(context_parts)) if context_parts else ""
            else:
                raise ValueError(f"Unknown comment type: {comment_type}")

            logger.info(f"Processing {comment_type} {comment_id}: {body[:50]}...")

            # Get PR details to get head branch
            pr = client.get_pull_request(owner, repo_name, pr_number)
            head_branch = pr['head']['ref']
            logger.info(f"Updating branch {head_branch} for PR #{pr_number}")
        except Exception as e:
            logger.error(f"Failed to get comment/review details: {e}")
            sys.exit(1)

        # Clone the repository
        repo_temp_dir = _make_repo_temp_dir(config, logger)
        logger.info(f"Cloning repo {owner}/{repo_name} to {repo_temp_dir}")
        try:
            # Construct clone URL with token
            base_url = _strip_api_suffix(config.gitea_base_url)
            protocol = 'https' if base_url.startswith('https://') else 'http'
            host = base_url.replace('https://', '').replace('http://', '')
            clone_url = f"{protocol}://oauth2:{config.gitea_token}@{host}/{owner}/{repo_name}.git"
            subprocess.run(["git", "clone", clone_url, repo_temp_dir], check=True)
            logger.info("Repository cloned successfully")
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to clone repository: {e}")
            sys.exit(1)

        # Checkout the PR branch
        try:
            logger.info(f"Checking out branch {head_branch}")
            subprocess.run(['git', 'checkout', head_branch], cwd=repo_temp_dir, check=True)
            logger.info(f"Checked out branch {head_branch}")
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to checkout branch {head_branch}: {e}")
            shutil.rmtree(repo_temp_dir)
            sys.exit(1)

        # Perform work
        try:
            prompt = f"Address this feedback{context}: {body}"
            comment_context = {
                'type': comment_type,
                'path': comment.get('path') if isinstance(comment, dict) else None,
                'position': comment.get('position') if isinstance(comment, dict) else None,
                'diff_hunk': comment.get('diff_hunk') if isinstance(comment, dict) else None,
            }
            context_block = _build_context(repo_temp_dir, config, pr=pr, comment=comment_context)
            combined_prompt = f"{context_block}\n\n{prompt}" if context_block else prompt
            do_work(combined_prompt, repo_temp_dir, config, head_branch)
        except Exception as e:
            logger.error(f"Work failed: {e}")
            shutil.rmtree(repo_temp_dir)
            sys.exit(1)

        # Handle commits and pushing
        try:
            logger.debug("Adding changes to git")
            subprocess.run(['git', 'add', '.'], cwd=repo_temp_dir, check=True)

            result = subprocess.run(['git', 'diff', '--cached', '--quiet'], cwd=repo_temp_dir)
            if result.returncode != 0:  # There are changes
                logger.debug("Committing changes")
                subprocess.run(['git', 'commit', '-m', f'Address {comment_type} #{comment_id} on PR #{pr_number}'], cwd=repo_temp_dir, check=True)
                logger.info(f"Committed changes for {comment_type} {comment_id}")

                logger.debug(f"Pushing branch {head_branch}")
                subprocess.run(['git', 'push', 'origin', head_branch], cwd=repo_temp_dir, check=True)
                logger.info(f"Pushed branch {head_branch} to remote")
            else:
                logger.warning("No changes to commit")
        except subprocess.CalledProcessError as e:
            logger.error(f"Git operation failed: {e}")
            shutil.rmtree(repo_temp_dir)
            sys.exit(1)

        logger.info(f"Subagent completed work for {comment_type} {comment_id} on PR #{pr_number}")
        sys.exit(0)

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
    repo_temp_dir = _make_repo_temp_dir(config, logger)
    logger.info(f"Cloning repo {owner}/{repo_name} to {repo_temp_dir}")
    try:
        # Construct clone URL with token, preserving protocol
        base_url = _strip_api_suffix(config.gitea_base_url)
        protocol = 'https' if base_url.startswith('https://') else 'http'
        host = base_url.replace('https://', '').replace('http://', '')
        clone_url = f"{protocol}://oauth2:{config.gitea_token}@{host}/{owner}/{repo_name}.git"
        subprocess.run(["git", "clone", clone_url, repo_temp_dir], check=True)
        logger.info("Repository cloned successfully")
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to clone repository: {e}")
        sys.exit(1)

    # Perform actual work
    head_branch = f"fix-issue-{issue_number}"
    try:
        logger.debug(f"Creating branch {head_branch}")
        subprocess.run(['git', 'checkout', '-b', head_branch], cwd=repo_temp_dir, check=True)
        logger.info(f"Created and checked out branch {head_branch}")
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to create branch {head_branch}: {e}")
        shutil.rmtree(repo_temp_dir)
        sys.exit(1)

    try:
        context_block = _build_context(repo_temp_dir, config, issue=issue)
        combined_prompt = f"{context_block}\n\n{issue['body']}" if context_block else issue['body']
        do_work(combined_prompt, repo_temp_dir, config, head_branch)
    except Exception as e:
        logger.error(f"Work failed: {e}")
        shutil.rmtree(repo_temp_dir)
        sys.exit(1)

    # Handle commits and pushing since kilo-code may not do it
    changes_made = False
    try:
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
            changes_made = True
        else:
            logger.warning("No changes to commit")
    except subprocess.CalledProcessError as e:
        logger.error(f"Git operation failed: {e}")
        shutil.rmtree(repo_temp_dir)
        sys.exit(1)

    if changes_made:
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
            logger.info(f"Subagent completed work for issue {issue_number}, PR #{pr_number} created")
        except Exception as e:
            logger.error(f"Failed to create PR for issue {issue_number}: {e}")
            sys.exit(1)
    else:
        logger.info(f"Subagent completed work for issue {issue_number}, no changes made, no PR created")

if __name__ == '__main__':
    main()
