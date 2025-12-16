import os
import logging
from logging.handlers import RotatingFileHandler

class Config:
    def __init__(self):
        self.gitea_base_url = os.getenv('GITEA_BASE_URL')
        if self.gitea_base_url:
            self.gitea_base_url = self.gitea_base_url.rstrip('/')
            if not self.gitea_base_url.endswith('/api/v1'):
                self.gitea_base_url += '/api/v1'
        self.gitea_token = os.getenv('GITEA_TOKEN')
        self.gitea_repos = [repo.strip() for repo in os.getenv('GITEA_REPOS', '').split(',') if repo.strip()]
        self.polling_frequency = int(os.getenv('POLLING_FREQUENCY', '60'))
        self.issue_label_reserve = os.getenv('ISSUE_LABEL_RESERVE', 'agent-working')
        self.issue_label_acceptance = os.getenv('ISSUE_LABEL_ACCEPTANCE', 'agent-acceptance')
        self.log_level = os.getenv('LOG_LEVEL', 'INFO').upper()
        self.log_file = os.getenv('LOG_FILE', 'kilocode_agent.log')
        self.max_log_size = int(os.getenv('MAX_LOG_SIZE', '10485760'))  # 10MB default
        self.backup_count = int(os.getenv('LOG_BACKUP_COUNT', '5'))

    def setup_logging(self):
        """Setup logging configuration with console and file handlers."""
        # Create logger
        logger = logging.getLogger()
        logger.setLevel(getattr(logging, self.log_level, logging.INFO))

        # Create formatter
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
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

        return logger

    def validate(self):
        if not self.gitea_base_url:
            raise ValueError("GITEA_BASE_URL is required")
        if not self.gitea_token:
            raise ValueError("GITEA_TOKEN is required")
        if not self.gitea_repos:
            raise ValueError("GITEA_REPOS is required")