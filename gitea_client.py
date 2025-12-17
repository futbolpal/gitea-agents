import requests
import logging
import time
from requests.exceptions import RequestException, Timeout, ConnectionError

logger = logging.getLogger(__name__)

class GiteaClient:
    def __init__(self, base_url, token, max_retries=3, backoff_factor=2):
        self.base_url = base_url.rstrip('/')
        self.session = requests.Session()
        self.session.headers.update({
            'Authorization': f'token {token}',
            'Content-Type': 'application/json'
        })
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor

    def _make_request(self, method, url, **kwargs):
        """Make HTTP request with retry logic and error handling."""
        for attempt in range(self.max_retries + 1):
            try:
                logger.debug(f"Making {method} request to {url} (attempt {attempt + 1})")
                response = self.session.request(method, url, **kwargs)
                response.raise_for_status()
                logger.debug(f"Request successful: {method} {url}")
                return response.json()
            except requests.exceptions.HTTPError as e:
                if response.status_code >= 500:
                    # Server error, retry
                    logger.warning(f"Server error {response.status_code} for {method} {url}: {e}")
                elif response.status_code == 429:
                    # Rate limited, retry with longer backoff
                    logger.warning(f"Rate limited for {method} {url}, retrying")
                else:
                    # Client error, don't retry
                    try:
                        error_data = response.json()
                        error_msg = error_data.get('message', response.text)
                    except:
                        error_msg = response.text
                    logger.error(f"Client error {response.status_code} for {method} {url}: {error_msg}")
                    raise Exception(f"API Error {response.status_code}: {error_msg}")
            except (Timeout, ConnectionError) as e:
                logger.warning(f"Network error for {method} {url}: {e}")
            except RequestException as e:
                logger.error(f"Request error for {method} {url}: {e}")
                raise

            if attempt < self.max_retries:
                sleep_time = self.backoff_factor ** attempt
                logger.info(f"Retrying in {sleep_time} seconds...")
                time.sleep(sleep_time)

        logger.error(f"Failed to complete request after {self.max_retries + 1} attempts: {method} {url}")
        raise RequestException(f"Failed to complete request after retries: {method} {url}")

    def get_issues(self, owner, repo, state='open', labels=None, limit=None):
        """Get issues for a repository."""
        url = f'{self.base_url}/repos/{owner}/{repo}/issues'
        params = {'state': state, 'type': 'issues'}
        if labels:
            params['labels'] = ','.join(labels)
        if limit:
            params['limit'] = limit
        logger.info(f"Getting issues for {owner}/{repo} with state={state}, limit={limit}")
        return self._make_request('GET', url, params=params)

    def update_issue_labels(self, owner, repo, issue_number, labels):
        """Update labels on an issue."""
        url = f'{self.base_url}/repos/{owner}/{repo}/issues/{issue_number}/labels'
        data = {'labels': labels}
        logger.info(f"Updating labels for issue #{issue_number} in {owner}/{repo}")
        return self._make_request('PUT', url, json=data)

    def get_pulls(self, owner, repo, state='open'):
        """Get pull requests for a repository."""
        url = f'{self.base_url}/repos/{owner}/{repo}/pulls'
        params = {'state': state}
        logger.info(f"Getting pull requests for {owner}/{repo} with state={state}")
        return self._make_request('GET', url, params=params)

    def create_pull_request(self, owner, repo, title, head, base, body=''):
        """Create a pull request."""
        url = f'{self.base_url}/repos/{owner}/{repo}/pulls'
        data = {
            'title': title,
            'head': head,
            'base': base,
            'body': body
        }
        logger.info(f"Creating pull request in {owner}/{repo}: {title}")
        return self._make_request('POST', url, json=data)

    def get_pull_comments(self, owner, repo, pull_number):
        """Get comments on a pull request."""
        url = f'{self.base_url}/repos/{owner}/{repo}/issues/{pull_number}/comments'
        logger.debug(f"Getting comments for PR #{pull_number} in {owner}/{repo}")
        return self._make_request('GET', url)

    def get_pull_request(self, owner, repo, pull_number):
        """Get a specific pull request."""
        url = f'{self.base_url}/repos/{owner}/{repo}/pulls/{pull_number}'
        logger.debug(f"Getting PR #{pull_number} details from {owner}/{repo}")
        return self._make_request('GET', url)

    def create_pull_comment(self, owner, repo, pull_number, body):
        """Create a comment on a pull request."""
        url = f'{self.base_url}/repos/{owner}/{repo}/issues/{pull_number}/comments'
        data = {'body': body}
        logger.info(f"Creating comment on PR #{pull_number} in {owner}/{repo}")
        return self._make_request('POST', url, json=data)

    def get_issue(self, owner, repo, issue_number):
        """Get a specific issue."""
        url = f'{self.base_url}/repos/{owner}/{repo}/issues/{issue_number}'
        logger.debug(f"Getting issue #{issue_number} from {owner}/{repo}")
        return self._make_request('GET', url)

    def get_repo(self, owner, repo):
        """Get repository details."""
        url = f'{self.base_url}/repos/{owner}/{repo}'
        logger.debug(f"Getting repo details for {owner}/{repo}")
        return self._make_request('GET', url)

    def create_label(self, owner, repo, name, color="#ffffff", description=""):
        """Create a label in the repository."""
        url = f'{self.base_url}/repos/{owner}/{repo}/labels'
        data = {
            "name": name,
            "color": color,
            "description": description
        }
        logger.info(f"Creating label '{name}' in {owner}/{repo}")
        return self._make_request('POST', url, json=data)

    def get_labels(self, owner, repo):
        """Get all labels for a repository."""
        url = f'{self.base_url}/repos/{owner}/{repo}/labels'
        logger.debug(f"Getting labels for {owner}/{repo}")
        return self._make_request('GET', url)

    def get_pull_reviews(self, owner, repo, pull_number):
        """Get reviews for a pull request."""
        url = f'{self.base_url}/repos/{owner}/{repo}/pulls/{pull_number}/reviews'
        logger.debug(f"Getting reviews for PR #{pull_number} in {owner}/{repo}")
        return self._make_request('GET', url)

    def get_pull_review_comments(self, owner, repo, pull_number, review_id):
        """Get comments for a specific review."""
        url = f'{self.base_url}/repos/{owner}/{repo}/pulls/{pull_number}/reviews/{review_id}/comments'
        logger.debug(f"Getting comments for review {review_id} on PR #{pull_number} in {owner}/{repo}")
        return self._make_request('GET', url)