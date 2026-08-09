"""
Module: toolkit.ai.client
Purpose: Thin async HTTP wrapper around the Anthropic Messages API.
         All Claude API calls go through this single module, making it easy
         to swap models, add retries, or change the API version in one place.
"""
import logging
import httpx
from app.core.config import settings
from app.modules.dbtoolkit.core.constants import (
    ANTHROPIC_API_URL,
    ANTHROPIC_API_VERSION,
    ANTHROPIC_TIMEOUT_SECONDS,
    AI_DEFAULT_MAX_TOKENS,
)

logger = logging.getLogger(__name__)


async def call_claude(
    model: str,
    system: str,
    user_msg: str,
    max_tokens: int = AI_DEFAULT_MAX_TOKENS,
) -> str:
    """Send a request to the Anthropic Messages API and return the response text.

    Args:
        model: Anthropic model ID (e.g. settings.CLAUDE_SONNET).
        system: System prompt string.
        user_msg: User message content.
        max_tokens: Maximum tokens in the response.

    Returns:
        The text content of the first response block.

    Raises:
        RuntimeError: If the API returns a non-200 status code.
    """
    headers = {
        "x-api-key": settings.ANTHROPIC_API_KEY,
        "anthropic-version": ANTHROPIC_API_VERSION,
        "content-type": "application/json",
    }
    payload = {
        "model":      model,
        "max_tokens": max_tokens,
        "system":     system,
        "messages":   [{"role": "user", "content": user_msg}],
    }
    async with httpx.AsyncClient(timeout=ANTHROPIC_TIMEOUT_SECONDS) as client:
        resp = await client.post(ANTHROPIC_API_URL, headers=headers, json=payload)
    if resp.status_code != 200:
        raise RuntimeError(f"Anthropic API error {resp.status_code}: {resp.text}")
    return resp.json()["content"][0]["text"]


def strip_json_fence(text: str) -> str:
    """Strip markdown code fences from an AI response.

    Claude sometimes wraps JSON in ```json ... ``` even when told not to.
    This function strips the fence so the content can be parsed with json.loads.

    Args:
        text: Raw text from the AI response.

    Returns:
        Clean JSON string ready for json.loads.
    """
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
    return text.strip()
