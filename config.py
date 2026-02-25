import os
import logging
import shlex
from logging.handlers import RotatingFileHandler

class CustomFormatter(logging.Formatter):
    def __init__(self, process_type, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.process_type = process_type

    def format(self, record):
        record.process_type = self.process_type
        return super().format(record)

class Config:
    def __init__(self):
        self.gitea_base_url = os.getenv('GITEA_BASE_URL')
        if self.gitea_base_url:
            self.gitea_base_url = self.gitea_base_url.rstrip('/')
            if not self.gitea_base_url.endswith('/api/v1'):
                self.gitea_base_url += '/api/v1'
        self.gitea_token = os.getenv('GITEA_TOKEN')
        self.gitea_username = os.getenv('GITEA_USERNAME', 'oauth2')
        self.gitea_bot_username = os.getenv('GITEA_BOT_USERNAME', self.gitea_username)
        self.gitea_repos = [repo.strip() for repo in os.getenv('GITEA_REPOS', '').split(',') if repo.strip()]
        self.polling_frequency = int(os.getenv('POLLING_FREQUENCY', '60'))
        self.issue_label_reserve = os.getenv('ISSUE_LABEL_RESERVE', 'agent-working')
        self.issue_label_in_review = os.getenv('ISSUE_LABEL_IN_REVIEW', 'agent-in-review')
        self.max_concurrent_subagents = int(os.getenv('MAX_CONCURRENT_SUBAGENTS', '3'))
        self.data_dir = os.getenv('DATA_DIR', '/data')
        self.log_level = os.getenv('LOG_LEVEL', 'INFO').upper()
        self.log_file = os.getenv('LOG_FILE', os.path.join(self.data_dir, 'kilocode_agent.log'))
        self.max_log_size = int(os.getenv('MAX_LOG_SIZE', '10485760'))  # 10MB default
        self.backup_count = int(os.getenv('LOG_BACKUP_COUNT', '5'))
        self.process_type = os.getenv('PROCESS_TYPE', 'unknown')
        self.agent_cli = os.getenv('AGENT_CLI', 'kilocode').strip().lower()
        self.kilocode_args = shlex.split(os.getenv('KILOCODE_ARGS', '-a -m orchestrator -j'))
        self.codex_exec_args = shlex.split(os.getenv('CODEX_EXEC_ARGS', '--full-auto'))
        self.codex_prompt_mode = os.getenv('CODEX_PROMPT_MODE', 'stdin').strip().lower()
        self.codex_model = os.getenv('CODEX_MODEL')
        self.prompt_template_path = os.getenv('PROMPT_TEMPLATE_PATH', 'prompt_template.txt')
        self.max_context_chars = int(os.getenv('MAX_CONTEXT_CHARS', '8000'))
        self.workspace_dir = os.getenv('WORKSPACE_DIR', '/workspace')
        self.git_user_name = os.getenv('GIT_USER_NAME', 'kilo-agent')
        self.git_user_email = os.getenv('GIT_USER_EMAIL', 'kilo-agent@localhost')

    def setup_logging(self):
        """Setup logging configuration with console and file handlers."""
        logger = logging.getLogger()
        if getattr(logger, "_kilo_configured", False):
            return logger

        logger.setLevel(getattr(logging, self.log_level, logging.INFO))

        # Create formatter
        formatter = CustomFormatter(
            self.process_type,
            '%(asctime)s - [%(process_type)s] - %(levelname)s - %(message)s'
        )

        # Console handler
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

        # File handler with rotation
        file_handler = RotatingFileHandler(
            self.log_file,
            maxBytes=self.max_log_size,
            backupCount=self.backup_count
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

        logger._kilo_configured = True
        return logger

    def log_config(self, logger):
        """Log configuration with sensitive values redacted."""
        def _redact(value):
            if not value:
                return value
            if len(value) <= 4:
                return "****"
            return value[:2] + "****" + value[-2:]

        config_view = {
            "gitea_base_url": self.gitea_base_url,
            "gitea_token": _redact(self.gitea_token),
            "gitea_username": self.gitea_username,
            "gitea_bot_username": self.gitea_bot_username,
            "gitea_repos": self.gitea_repos,
            "polling_frequency": self.polling_frequency,
            "issue_label_reserve": self.issue_label_reserve,
            "issue_label_in_review": self.issue_label_in_review,
            "max_concurrent_subagents": self.max_concurrent_subagents,
            "data_dir": self.data_dir,
            "workspace_dir": self.workspace_dir,
            "log_level": self.log_level,
            "log_file": self.log_file,
            "max_log_size": self.max_log_size,
            "backup_count": self.backup_count,
            "agent_cli": self.agent_cli,
            "kilocode_args": self.kilocode_args,
            "codex_exec_args": self.codex_exec_args,
            "codex_prompt_mode": self.codex_prompt_mode,
            "codex_model": self.codex_model,
            "prompt_template_path": self.prompt_template_path,
            "max_context_chars": self.max_context_chars,
            "git_user_name": self.git_user_name,
            "git_user_email": self.git_user_email,
        }
        logger.info("Configuration: %s", config_view)

    def validate(self):
        if not self.gitea_base_url:
            raise ValueError("GITEA_BASE_URL is required")
        if not self.gitea_token:
            raise ValueError("GITEA_TOKEN is required")
        if not self.gitea_repos:
            raise ValueError("GITEA_REPOS is required")
        if self.agent_cli not in {'kilocode', 'codex'}:
            raise ValueError("AGENT_CLI must be 'kilocode' or 'codex'")
        if self.codex_prompt_mode not in {'stdin', 'arg'}:
            raise ValueError("CODEX_PROMPT_MODE must be 'stdin' or 'arg'")
        try:
            os.makedirs(self.data_dir, exist_ok=True)
        except Exception as e:
            raise ValueError(f"Failed to create DATA_DIR '{self.data_dir}': {e}")
        if self.workspace_dir:
            try:
                os.makedirs(self.workspace_dir, exist_ok=True)
            except Exception:
                # Defer to runtime fallback if workspace isn't writable.
                pass
