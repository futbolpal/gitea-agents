"""Utility functions for the kilo-agent."""

import logging

logger = logging.getLogger(__name__)

def analyze_and_respond(comment_body):
    """Analyze comment and generate response."""
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