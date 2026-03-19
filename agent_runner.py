import logging
import os
import re
import shutil
import subprocess
import tempfile


logger = logging.getLogger(__name__)


_TOKEN_USAGE_RE = re.compile(r"tokens used\s*[:=]?\s*([\d,]+)", re.IGNORECASE)


def _ensure_cli_available(cli_name):
    if shutil.which(cli_name) is None:
        raise FileNotFoundError(f"Required CLI '{cli_name}' not found in PATH")


def _run_with_output_file(cmd, prompt, repo_dir, config):
    output_dir = config.data_dir
    os.makedirs(output_dir, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        delete=False,
        dir=output_dir,
        suffix=".log",
        encoding="utf-8",
    ) as output_file:
        output_path = output_file.name

    with open(output_path, "w", encoding="utf-8") as output_handle:
        result = subprocess.run(
            cmd,
            cwd=repo_dir,
            input=prompt,
            text=True,
            stdout=output_handle,
            stderr=subprocess.PIPE,
        )
    return result, output_path


def _log_token_usage(stderr):
    if not stderr:
        return

    match = _TOKEN_USAGE_RE.search(stderr)
    if not match:
        return

    try:
        token_count = int(match.group(1).replace(",", ""))
    except ValueError:
        return

    logger.info("Codex token usage: %s tokens", f"{token_count:,}")


def run_kilocode(prompt, repo_dir, config):
    _ensure_cli_available("kilocode")
    cmd = ["kilocode"] + config.kilocode_args
    logger.info("Running kilocode with args: %s", " ".join(cmd))
    result, output_path = _run_with_output_file(cmd, prompt, repo_dir, config)
    return result, output_path


def run_codex(prompt, repo_dir, config):
    _ensure_cli_available("codex")
    cmd = ["codex", "exec", "-C", repo_dir] + config.codex_exec_args
    if config.codex_model:
        cmd += ["-m", config.codex_model]
    if config.codex_prompt_mode == "stdin":
        cmd += ["-"]
        input_prompt = prompt
    else:
        cmd += [prompt]
        input_prompt = None

    logger.info("Running codex with args: %s", " ".join(cmd))
    result, output_path = _run_with_output_file(cmd, input_prompt, repo_dir, config)
    _log_token_usage(result.stderr)
    return result, output_path


def run_agent(prompt, repo_dir, config):
    if config.agent_cli == "kilocode":
        return run_kilocode(prompt, repo_dir, config)
    if config.agent_cli == "codex":
        return run_codex(prompt, repo_dir, config)
    raise ValueError(f"Unsupported AGENT_CLI '{config.agent_cli}'")
