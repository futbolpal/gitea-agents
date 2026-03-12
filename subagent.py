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
import re
from config import Config
from gitea_client import GiteaClient
from agent_runner import run_agent, run_codex

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

def _ensure_git_identity(repo_dir, config, logger):
    def _get_config(key):
        try:
            result = subprocess.run(
                ["git", "config", "--get", key],
                cwd=repo_dir,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            return result.stdout.strip()
        except Exception:
            return ""

    name = _get_config("user.name")
    email = _get_config("user.email")

    if not name:
        subprocess.run(["git", "config", "user.name", config.git_user_name], cwd=repo_dir, check=False)
        logger.info("Configured git user.name")
    if not email:
        subprocess.run(["git", "config", "user.email", config.git_user_email], cwd=repo_dir, check=False)
        logger.info("Configured git user.email")

def _get_git_head(repo_dir):
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_dir,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except Exception:
        return ""

def _push_branch(repo_dir, branch, logger):
    def _run_push(args):
        return subprocess.run(
            args,
            cwd=repo_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    result = _run_push(['git', 'push', 'origin', branch])
    if result.returncode == 0:
        return

    message = (result.stderr or result.stdout).strip()
    logger.error("Git push failed: %s", message)
    if any(key in message for key in ("non-fast-forward", "fetch first", "rejected")):
        logger.warning("Retrying push with --force-with-lease")
        retry = _run_push(['git', 'push', '--force-with-lease', 'origin', branch])
        if retry.returncode == 0:
            logger.info("Force push with lease succeeded")
            return
        retry_message = (retry.stderr or retry.stdout).strip()
        logger.error("Git push retry failed: %s", retry_message)
        raise subprocess.CalledProcessError(
            retry.returncode,
            retry.args,
            output=retry.stdout,
            stderr=retry.stderr,
        )

    raise subprocess.CalledProcessError(
        result.returncode,
        result.args,
        output=result.stdout,
        stderr=result.stderr,
    )

def _git_output(repo_dir, args):
    result = subprocess.run(
        args,
        cwd=repo_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result

def _create_branch_from_remote_base(repo_dir, base_branch, head_branch, logger):
    fetch_result = _git_output(repo_dir, ['git', 'fetch', 'origin', base_branch])
    if fetch_result.returncode != 0:
        raise subprocess.CalledProcessError(
            fetch_result.returncode,
            fetch_result.args,
            output=fetch_result.stdout,
            stderr=fetch_result.stderr,
        )

    logger.debug("Creating branch %s from origin/%s", head_branch, base_branch)
    checkout_result = _git_output(
        repo_dir,
        ['git', 'checkout', '-B', head_branch, f'origin/{base_branch}'],
    )
    if checkout_result.returncode != 0:
        raise subprocess.CalledProcessError(
            checkout_result.returncode,
            checkout_result.args,
            output=checkout_result.stdout,
            stderr=checkout_result.stderr,
        )

def _branch_is_behind_base(repo_dir, base_ref):
    fetch = _git_output(repo_dir, ['git', 'fetch', 'origin', base_ref])
    if fetch.returncode != 0:
        raise subprocess.CalledProcessError(fetch.returncode, fetch.args, output=fetch.stdout, stderr=fetch.stderr)
    rev = _git_output(repo_dir, ['git', 'rev-list', '--left-right', '--count', f'HEAD...origin/{base_ref}'])
    if rev.returncode != 0:
        raise subprocess.CalledProcessError(rev.returncode, rev.args, output=rev.stdout, stderr=rev.stderr)
    counts = rev.stdout.strip().split()
    if len(counts) != 2:
        return False
    behind = int(counts[1])
    return behind > 0

def _merge_base_into_head(repo_dir, base_ref):
    return _git_output(repo_dir, ['git', 'merge', f'origin/{base_ref}'])

def _merge_conflicts(repo_dir):
    result = _git_output(repo_dir, ['git', 'diff', '--name-only', '--diff-filter=U'])
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.splitlines() if line.strip()]

def _merge_in_progress(repo_dir):
    return os.path.exists(os.path.join(repo_dir, '.git', 'MERGE_HEAD'))

def _finalize_merge(repo_dir, base_ref, head_branch, logger):
    if not _merge_in_progress(repo_dir):
        return
    subprocess.run(['git', 'add', '.'], cwd=repo_dir, check=True)
    subprocess.run(
        ['git', 'commit', '-m', f'Merge {base_ref} into {head_branch}'],
        cwd=repo_dir,
        check=True,
    )
    logger.info("Committed merge of %s into %s", base_ref, head_branch)

def _comment_merge_failure(client, owner, repo_name, pr_number, base_ref, conflicts, error):
    conflict_list = "\n".join(f"- {path}" for path in conflicts) if conflicts else "- (unknown)"
    marker = "<!-- kilo-agent -->"
    body = (
        f"{marker}\n"
        "I attempted to update this branch with the latest changes from "
        f"`{base_ref}`, but hit merge conflicts I could not resolve automatically.\n\n"
        "Conflicting files:\n"
        f"{conflict_list}\n\n"
        "Please resolve these conflicts manually, then re-run the agent.\n\n"
        f"Error details: {error}"
    )
    client.create_pull_comment(owner, repo_name, pr_number, body)

COMMENT_REPLY_MARKER = "<!-- kilo-agent -->"
ISSUE_PLAN_COMMENT_MARKER = "<!-- kilo-agent-issue-plan -->"

COMMENT_CLASSIFIER_SYSTEM_PROMPT = (
    "You are a PR comment triage assistant. Return JSON only. "
    "Schema: {\"classification\": \"question\"|\"action\"|\"both\"|\"ignore\", "
    "\"answer\": string, \"reason\": string}. "
    "If classification is action or ignore, answer must be empty. "
    "If classification is question or both, provide a concise answer based only on the comment text. "
    "If unclear, ask a short clarification question in the answer."
)

PR_SUMMARY_SYSTEM_PROMPT = (
    "You are generating a pull request summary. Output Markdown only. "
    "Include sections: Summary, Why, Testing. Be concise and factual. "
    "Do not mention internal agent tooling."
)

ISSUE_PLAN_SYSTEM_PROMPT = (
    "You are reviewing a repository issue before implementation. Output Markdown only. "
    "Include exactly two sections titled 'Assessment' and 'Plan'. "
    "In Assessment, summarize the problem, relevant code areas, and any key uncertainty. "
    "In Plan, provide a short numbered list of concrete implementation steps. "
    "Do not claim work is already complete. Do not include code fences."
)


def _extract_json_block(text):
    if not text:
        return None
    match = re.search(r"\{.*\}", text, re.DOTALL)
    return match.group(0) if match else None


def _parse_comment_classification(text):
    blob = _extract_json_block(text)
    if not blob:
        return None
    try:
        data = json.loads(blob)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    classification = data.get("classification")
    answer = (data.get("answer") or "").strip()
    reason = (data.get("reason") or "").strip()
    if classification not in ("question", "action", "both", "ignore"):
        return None
    if classification in ("action", "ignore"):
        answer = ""
    return {"classification": classification, "answer": answer, "reason": reason}


def _git_porcelain(repo_dir):
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo_dir,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout.strip()


def _ensure_clean_repo(repo_dir, logger):
    status = _git_porcelain(repo_dir)
    if not status:
        return
    logger.warning("Repo dirty after codex run, resetting changes")
    subprocess.run(["git", "reset", "--hard"], cwd=repo_dir, check=False)
    subprocess.run(["git", "clean", "-fd"], cwd=repo_dir, check=False)


def _run_codex_text(prompt, repo_dir, config, logger):
    result, output_path = run_codex(prompt, repo_dir, config)
    try:
        with open(output_path, "r", encoding="utf-8") as handle:
            output = handle.read()
    except Exception:
        output = ""
    if result.returncode != 0:
        logger.error("Codex text run failed: %s", result.stderr)
        return None
    _ensure_clean_repo(repo_dir, logger)
    return output


def _classify_comment(comment_body, context, repo_dir, config, logger):
    prompt = (
        f"{COMMENT_CLASSIFIER_SYSTEM_PROMPT}\n\n"
        "Comment:\n"
        f"{comment_body}\n\n"
        "Context:\n"
        f"{context}\n"
    )
    output = _run_codex_text(prompt, repo_dir, config, logger)
    if not output:
        return None
    parsed = _parse_comment_classification(output)
    if not parsed:
        logger.warning("Failed to parse comment classification output: %s", output)
    return parsed


def _answer_comment_if_needed(
    client,
    owner,
    repo_name,
    pr_number,
    comment_type,
    path,
    position,
    comment_id,
    analysis,
    logger,
):
    if not analysis:
        return None

    classification = analysis.get("classification")
    answer = analysis.get("answer", "").strip()
    if classification not in ("question", "both") or not answer:
        return classification

    response_body = f"{COMMENT_REPLY_MARKER}\n{answer}"
    if comment_type == "review_comment" and path and position is not None:
        try:
            client.create_pull_review_comment(
                owner,
                repo_name,
                pr_number,
                response_body,
                path=path,
                position=position,
            )
            logger.info("Posted inline answer on PR #%s", pr_number)
            logger.info("Answered comment %s as %s", comment_id, classification)
            return classification
        except Exception as e:
            logger.warning("Inline reply failed, falling back to PR comment: %s", e)

    client.create_pull_comment(owner, repo_name, pr_number, response_body)
    logger.info("Answered comment %s as %s", comment_id, classification)
    return classification


def _fallback_pr_summary(diffstat, files_changed):
    lines = [
        "## Summary",
        "Changes in:",
    ]
    for path in files_changed:
        lines.append(f"- {path}")
    lines += [
        "",
        "## Why",
        "See linked issue for context.",
        "",
        "## Testing",
        "- Not run (not specified).",
    ]
    if diffstat:
        lines += ["", "## Diffstat", diffstat.strip()]
    return "\n".join(lines)


def _compose_pr_body(summary, issue_number, issue_body):
    parts = [summary.strip() if summary else ""]
    parts.append(f"Closes #{issue_number}")
    body = (issue_body or "").strip()
    if body:
        parts.append(body)
    return "\n\n".join(part for part in parts if part)


def _generate_issue_plan(issue, context, repo_dir, config, logger):
    if config.agent_cli != "codex":
        logger.info("Skipping issue plan comment because AGENT_CLI=%s", config.agent_cli)
        return None

    prompt = (
        f"{ISSUE_PLAN_SYSTEM_PROMPT}\n\n"
        f"Issue Number: {issue.get('number')}\n"
        f"Issue Title: {issue.get('title')}\n"
        f"Issue Body:\n{issue.get('body')}\n\n"
        f"Repository Context:\n{context}\n"
    )
    output = _run_codex_text(prompt, repo_dir, config, logger)
    if not output:
        return None

    body = output.strip()
    if not body:
        return None
    return f"{ISSUE_PLAN_COMMENT_MARKER}\n{body}"


def _ensure_issue_plan_comment(client, owner, repo_name, issue, context, repo_dir, config, logger):
    issue_number = issue['number']
    try:
        comments = client.get_issue_comments(owner, repo_name, issue_number) or []
        for comment in comments:
            body = comment.get('body') or ""
            if ISSUE_PLAN_COMMENT_MARKER in body:
                logger.debug("Issue #%s already has a generated plan comment", issue_number)
                return

        plan_body = _generate_issue_plan(issue, context, repo_dir, config, logger)
        if not plan_body:
            logger.warning("No issue plan comment generated for issue #%s", issue_number)
            return

        client.create_issue_comment(owner, repo_name, issue_number, plan_body)
        logger.info("Posted generated plan comment for issue #%s in %s/%s", issue_number, owner, repo_name)
    except Exception as e:
        logger.warning("Failed to post generated plan comment for issue #%s: %s", issue_number, e)


def _generate_pr_summary(issue, base_branch, repo_dir, config, logger):
    diffstat = subprocess.run(
        ["git", "diff", "--stat", f"origin/{base_branch}...HEAD"],
        cwd=repo_dir,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    files_changed = subprocess.run(
        ["git", "diff", "--name-only", f"origin/{base_branch}...HEAD"],
        cwd=repo_dir,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout.strip().splitlines()

    prompt = (
        f"{PR_SUMMARY_SYSTEM_PROMPT}\n\n"
        f"Issue Title: {issue.get('title')}\n"
        f"Issue Body:\n{issue.get('body')}\n\n"
        f"Diffstat:\n{diffstat}\n\n"
        f"Files Changed:\n" + "\n".join(files_changed)
    )
    if config.agent_cli == "codex":
        output = _run_codex_text(prompt, repo_dir, config, logger)
        if output:
            return output.strip()
    return _fallback_pr_summary(diffstat, files_changed)


def _create_or_update_issue_pr(
    client,
    owner,
    repo_name,
    issue,
    issue_number,
    head_branch,
    default_branch,
    repo_dir,
    config,
    logger,
):
    title = f"Fix issue #{issue_number}: {issue['title']}"
    summary = _generate_pr_summary(issue, default_branch, repo_dir, config, logger)
    pr_body = _compose_pr_body(summary, issue_number, issue.get('body'))

    try:
        return client.create_pull_request(
            owner,
            repo_name,
            title,
            head_branch,
            default_branch,
            pr_body,
        )
    except Exception as e:
        message = str(e)
        if "pull request already exists" not in message and "API Error 409" not in message:
            raise

    prs = client.get_pulls(owner, repo_name, state='open')
    match = next((pr for pr in prs if pr.get('head', {}).get('ref') == head_branch), None)
    if not match:
        raise Exception(f"Existing PR for head {head_branch} not found after conflict")

    pr_number = match.get('number')
    client.update_pull_request(
        owner,
        repo_name,
        pr_number,
        title=title,
        body=pr_body,
        base=default_branch,
    )
    logger.info("Updated existing PR #%s for head %s", pr_number, head_branch)
    return match

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
        print("Usage: python subagent.py --issue <issue_number> <repo> OR python subagent.py --comment <comment_id> <repo> <pr_number> <type> [review_id] OR python subagent.py --update-pr <repo> <pr_number>", file=sys.stderr)
        sys.exit(1)

    comment_id = None
    issue_number = None
    repo = None
    pr_number = None
    comment_type = None
    review_id = None
    update_pr_number = None

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
    elif sys.argv[1] == '--update-pr':
        if len(sys.argv) < 4:
            print("Usage: python subagent.py --update-pr <repo> <pr_number>", file=sys.stderr)
            sys.exit(1)
        try:
            repo = sys.argv[2]
            update_pr_number = int(sys.argv[3])
        except ValueError as e:
            print(f"Invalid arguments: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        print("Invalid mode. Use --issue, --comment, or --update-pr", file=sys.stderr)
        sys.exit(1)

    os.environ['PROCESS_TYPE'] = 'subagent'
    config = Config()
    config.validate()
    logger = config.setup_logging()
    config.log_config(logger)
    if issue_number:
        logger.info(f"Starting subagent for issue {issue_number} in repo {repo}")
    elif comment_id:
        logger.info(f"Starting subagent for comment {comment_id} on PR #{pr_number} in repo {repo}")
    elif update_pr_number:
        logger.info(f"Starting subagent to update stale PR #{update_pr_number} in repo {repo}")
    else:
        logger.info("Starting subagent")

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

    if update_pr_number:
        pr_number = update_pr_number
        try:
            pr = client.get_pull_request(owner, repo_name, pr_number)
            base_ref = pr.get('base', {}).get('ref')
            head_branch = pr.get('head', {}).get('ref')
            if not base_ref or not head_branch:
                logger.error("PR #%s missing base or head ref", pr_number)
                sys.exit(1)
        except Exception as e:
            logger.error(f"Failed to get PR #{pr_number} details: {e}")
            sys.exit(1)

        repo_temp_dir = _make_repo_temp_dir(config, logger)
        logger.info(f"Cloning repo {owner}/{repo_name} to {repo_temp_dir}")
        try:
            base_url = _strip_api_suffix(config.gitea_base_url)
            protocol = 'https' if base_url.startswith('https://') else 'http'
            host = base_url.replace('https://', '').replace('http://', '')
            clone_url = f"{protocol}://{config.gitea_username}:{config.gitea_token}@{host}/{owner}/{repo_name}.git"
            subprocess.run(["git", "clone", clone_url, repo_temp_dir], check=True)
            logger.info("Repository cloned successfully")
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to clone repository: {e}")
            sys.exit(1)

        try:
            logger.info(f"Checking out branch {head_branch}")
            subprocess.run(['git', 'checkout', head_branch], cwd=repo_temp_dir, check=True)
            logger.info(f"Checked out branch {head_branch}")
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to checkout branch {head_branch}: {e}")
            shutil.rmtree(repo_temp_dir)
            sys.exit(1)

        try:
            if not _branch_is_behind_base(repo_temp_dir, base_ref):
                logger.info("PR #%s is already up to date with %s", pr_number, base_ref)
                shutil.rmtree(repo_temp_dir)
                sys.exit(0)

            logger.info("PR #%s is behind %s; attempting merge", pr_number, base_ref)
            merge_result = _merge_base_into_head(repo_temp_dir, base_ref)
            if merge_result.returncode != 0:
                conflicts = _merge_conflicts(repo_temp_dir)
                logger.warning("Merge conflicts detected: %s", ", ".join(conflicts) if conflicts else "unknown")
                merge_prompt = (
                    f"Resolve merge conflicts after merging origin/{base_ref} into {head_branch}. "
                    "Only resolve conflict markers; do not change unrelated code."
                )
                do_work(merge_prompt, repo_temp_dir, config, head_branch)
                conflicts = _merge_conflicts(repo_temp_dir)
                if conflicts:
                    _comment_merge_failure(client, owner, repo_name, pr_number, base_ref, conflicts, "Merge conflicts remain")
                    logger.error("Merge conflicts remain after attempted resolution")
                    shutil.rmtree(repo_temp_dir)
                    sys.exit(0)
                _finalize_merge(repo_temp_dir, base_ref, head_branch, logger)

            _push_branch(repo_temp_dir, head_branch, logger)
            logger.info("Pushed merged branch %s for PR #%s", head_branch, pr_number)
            shutil.rmtree(repo_temp_dir)
            sys.exit(0)
        except Exception as e:
            try:
                conflicts = _merge_conflicts(repo_temp_dir)
                _comment_merge_failure(client, owner, repo_name, pr_number, base_ref, conflicts, e)
            except Exception as comment_error:
                logger.error("Failed to comment about merge failure: %s", comment_error)
            shutil.rmtree(repo_temp_dir)
            sys.exit(0)

    if comment_id:
        # Handle comment processing - make code changes on PR branch
        try:
            # Get comment details based on type
            if comment_type == 'pr_comment':
                comment = client._make_request('GET', f'{client.base_url}/repos/{owner}/{repo_name}/issues/comments/{comment_id}')
                body = comment['body']
                context = ""
                path = None
                position = None
                diff_hunk = ""
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
            clone_url = f"{protocol}://{config.gitea_username}:{config.gitea_token}@{host}/{owner}/{repo_name}.git"
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

        classification = None
        if config.agent_cli == "codex":
            analysis = _classify_comment(body, context, repo_temp_dir, config, logger)
            if analysis:
                classification = _answer_comment_if_needed(
                    client,
                    owner,
                    repo_name,
                    pr_number,
                    comment_type,
                    path,
                    position,
                    comment_id,
                    analysis,
                    logger,
                )

        if classification == "question":
            logger.info("Comment classified as question only; skipping code changes")
            shutil.rmtree(repo_temp_dir)
            sys.exit(0)

        base_ref = pr.get('base', {}).get('ref')
        if base_ref:
            try:
                if _branch_is_behind_base(repo_temp_dir, base_ref):
                    logger.info("Branch %s is behind %s; attempting merge", head_branch, base_ref)
                    merge_result = _merge_base_into_head(repo_temp_dir, base_ref)
                    if merge_result.returncode != 0:
                        conflicts = _merge_conflicts(repo_temp_dir)
                        logger.warning("Merge conflicts detected: %s", ", ".join(conflicts) if conflicts else "unknown")
                        merge_prompt = (
                            f"Resolve merge conflicts after merging origin/{base_ref} into {head_branch}. "
                            "Only resolve conflict markers; do not change unrelated code."
                        )
                        do_work(merge_prompt, repo_temp_dir, config, head_branch)
                        conflicts = _merge_conflicts(repo_temp_dir)
                        if conflicts:
                            _comment_merge_failure(client, owner, repo_name, pr_number, base_ref, conflicts, "Merge conflicts remain")
                            logger.error("Merge conflicts remain after attempted resolution")
                            shutil.rmtree(repo_temp_dir)
                            sys.exit(0)
                        _finalize_merge(repo_temp_dir, base_ref, head_branch, logger)
                    else:
                        logger.info("Merge completed cleanly")
            except Exception as e:
                try:
                    conflicts = _merge_conflicts(repo_temp_dir)
                    _comment_merge_failure(client, owner, repo_name, pr_number, base_ref, conflicts, e)
                except Exception as comment_error:
                    logger.error("Failed to comment about merge failure: %s", comment_error)
                shutil.rmtree(repo_temp_dir)
                sys.exit(0)

        head_before = _get_git_head(repo_temp_dir)

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
            _ensure_git_identity(repo_temp_dir, config, logger)
            logger.debug("Adding changes to git")
            subprocess.run(['git', 'add', '.'], cwd=repo_temp_dir, check=True)

            result = subprocess.run(['git', 'diff', '--cached', '--quiet'], cwd=repo_temp_dir)
            if result.returncode != 0:  # There are changes
                logger.debug("Committing changes")
                subprocess.run(['git', 'commit', '-m', f'Address {comment_type} #{comment_id} on PR #{pr_number}'], cwd=repo_temp_dir, check=True)
                logger.info(f"Committed changes for {comment_type} {comment_id}")

            head_after = _get_git_head(repo_temp_dir)
            if head_before and head_after and head_before != head_after:
                logger.debug(f"Pushing branch {head_branch}")
                _push_branch(repo_temp_dir, head_branch, logger)
                logger.info(f"Pushed branch {head_branch} to remote")
            else:
                logger.warning("No new commits to push")
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
        clone_url = f"{protocol}://{config.gitea_username}:{config.gitea_token}@{host}/{owner}/{repo_name}.git"
        subprocess.run(["git", "clone", clone_url, repo_temp_dir], check=True)
        logger.info("Repository cloned successfully")
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to clone repository: {e}")
        sys.exit(1)

    # Perform actual work
    head_branch = f"fix-issue-{issue_number}"
    try:
        repo_info = client.get_repo(owner, repo_name)
        default_branch = repo_info.get('default_branch', 'main')
        logger.info("Using default branch as issue base: %s", default_branch)
    except Exception as e:
        logger.error(f"Failed to get repository details: {e}")
        shutil.rmtree(repo_temp_dir)
        sys.exit(1)

    try:
        _create_branch_from_remote_base(repo_temp_dir, default_branch, head_branch, logger)
        logger.info(f"Created and checked out branch {head_branch}")
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to create branch {head_branch}: {e}")
        shutil.rmtree(repo_temp_dir)
        sys.exit(1)

    head_before = _get_git_head(repo_temp_dir)

    try:
        context_block = _build_context(repo_temp_dir, config, issue=issue)
        _ensure_issue_plan_comment(client, owner, repo_name, issue, context_block, repo_temp_dir, config, logger)
        combined_prompt = f"{context_block}\n\n{issue['body']}" if context_block else issue['body']
        do_work(combined_prompt, repo_temp_dir, config, head_branch)
    except Exception as e:
        logger.error(f"Work failed: {e}")
        shutil.rmtree(repo_temp_dir)
        sys.exit(1)

    # Handle commits and pushing since kilo-code may not do it
    changes_made = False
    try:
        _ensure_git_identity(repo_temp_dir, config, logger)
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

        head_after = _get_git_head(repo_temp_dir)
        if head_before and head_after and head_before != head_after:
            logger.debug(f"Pushing branch {head_branch}")
            # Push
            _push_branch(repo_temp_dir, head_branch, logger)
            logger.info(f"Pushed branch {head_branch} to remote")
            changes_made = True
        else:
            logger.warning("No new commits to push")
    except subprocess.CalledProcessError as e:
        logger.error(f"Git operation failed: {e}")
        shutil.rmtree(repo_temp_dir)
        sys.exit(1)

    if changes_made:
        try:
            logger.debug(f"Creating PR with head={head_branch}")
            logger.info(f"Using default branch: {default_branch}")
            pr = _create_or_update_issue_pr(
                client,
                owner,
                repo_name,
                issue,
                issue_number,
                head_branch,
                default_branch,
                repo_temp_dir,
                config,
                logger,
            )
            pr_number = pr['number']
            logger.info(f"Subagent completed work for issue {issue_number}, PR #{pr_number} ready")
        except Exception as e:
            logger.error(f"Failed to create PR for issue {issue_number}: {e}")
            sys.exit(1)
    else:
        logger.info(f"Subagent completed work for issue {issue_number}, no changes made, no PR created")

if __name__ == '__main__':
    main()
