import json
import logging

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are a PR comment triage assistant. Decide if a comment is a question that should be answered, "
    "or an action request that should be handled by a coding agent. Return JSON only.\n"
    "Schema: {\"classification\": \"question\"|\"action\"|\"ignore\", \"answer\": string, \"reason\": string}.\n"
    "If classification is action or ignore, answer must be an empty string.\n"
    "If classification is question, provide a concise, helpful answer based only on the comment text. "
    "If unsure, ask a brief clarification question in the answer."
)

USER_PROMPT = "Comment:\n{comment}\n"


def _parse_analysis(text):
    try:
        data = json.loads(text)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    classification = data.get("classification")
    answer = data.get("answer", "") or ""
    reason = data.get("reason", "") or ""
    if classification not in ("question", "action", "ignore"):
        return None
    if classification != "question":
        answer = ""
    return {
        "classification": classification,
        "answer": answer.strip(),
        "reason": reason.strip(),
    }


def _get_model(config):
    try:
        from langchain_openai import ChatOpenAI
    except Exception as exc:
        logger.error("Failed to import langchain_openai: %s", exc)
        return None

    headers = {}
    if config.openrouter_referrer:
        headers["HTTP-Referer"] = config.openrouter_referrer
    if config.openrouter_title:
        headers["X-Title"] = config.openrouter_title

    return ChatOpenAI(
        model=config.comment_analyzer_model,
        api_key=config.openrouter_api_key,
        base_url=config.openrouter_base_url,
        default_headers=headers or None,
        temperature=0,
        model_kwargs={"response_format": {"type": "json_object"}},
    )


def analyze_comment(comment_body, config):
    if not config.comment_analyzer_enabled:
        return None
    if not config.openrouter_api_key:
        logger.warning("COMMENT_ANALYZER enabled but OPENROUTER_API_KEY is missing")
        return None

    model = _get_model(config)
    if not model:
        return None

    messages = [
        ("system", SYSTEM_PROMPT),
        ("user", USER_PROMPT.format(comment=comment_body)),
    ]
    result = model.invoke(messages)
    content = getattr(result, "content", "")
    parsed = _parse_analysis(content)
    if not parsed:
        logger.warning("Failed to parse comment analysis response: %s", content)
    return parsed
