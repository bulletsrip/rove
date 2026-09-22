from __future__ import annotations

import json
import os
from typing import Callable
from urllib.parse import urlparse


RESULT_SYSTEM_PROMPT = """You are Rove's final result verifier.

Decide whether the user's goal is an information request or an action request.
Return one JSON object with exactly these keys:
kind, summary, facts, source_url

kind must be one of:
- answer: the final observed page contains the requested information
- completion: the user asked for an action and it was completed
- needs_review: the user asked for information but the page does not visibly contain a verified answer

Rules:
- Use only the supplied final page snapshot. Never invent, estimate, or use outside knowledge.
- Preserve names, places, and search terms exactly as observed.
- facts must be an object of short label/value strings. Use {} for completion or needs_review when appropriate.
- summary must be concise and useful to the user.
- source_url must be the supplied page URL unless the page visibly provides a more specific source URL.
"""


class ResultExtractionUnavailable(RuntimeError):
    """The configured answer model cannot be called."""


def _parse_content(content):
    if not isinstance(content, str):
        raise ValueError("Result model returned non-text content")
    content = content.strip()
    if content.startswith("```") and content.endswith("```"):
        content = content.split("\n", 1)[1].rsplit("\n", 1)[0]
    return json.loads(content)


def _validate_result(value, fallback_url):
    if not isinstance(value, dict):
        raise ValueError("Result model returned an invalid object")
    kind = value.get("kind")
    if kind not in {"answer", "completion", "needs_review"}:
        raise ValueError("Result model returned an invalid result kind")
    summary = value.get("summary")
    if not isinstance(summary, str) or not summary.strip() or len(summary) > 2000:
        raise ValueError("Result model returned an invalid summary")
    facts = value.get("facts", {})
    if not isinstance(facts, dict) or any(
        not isinstance(key, str) or not isinstance(item, str) or len(item) > 500
        for key, item in facts.items()
    ):
        raise ValueError("Result model returned invalid facts")
    source_url = value.get("source_url") or fallback_url
    if not isinstance(source_url, str) or not urlparse(source_url).scheme:
        source_url = fallback_url
    return {
        "kind": kind,
        "summary": summary.strip(),
        "facts": facts,
        "source_url": source_url,
    }


def extract_result(goal, page, history, *, request_json: Callable | None = None, api_key=None, base_url=None):
    """Verify and summarize the final observed page for a completed task."""
    if request_json is None:
        from jev_ultrafast.model import post_json

        request_json = post_json
    api_key = api_key or os.getenv("TEXT_MODEL_API_KEY")
    if not api_key:
        raise ResultExtractionUnavailable("TEXT_MODEL_API_KEY is not configured")
    base_url = (base_url or os.getenv("TEXT_MODEL_BASE_URL", "https://api.deepseek.com/v1")).rstrip("/")
    body = {
        "model": os.getenv("TEXT_MODEL", "deepseek-chat"),
        "max_tokens": 700,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": RESULT_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "goal": goal,
                        "final_page": {
                            "url": page.get("url"),
                            "title": page.get("title"),
                            "text": (page.get("text") or "")[:12000],
                        },
                        "recent_actions": [
                            {key: action.get(key) for key in ("action", "kind", "text", "url")}
                            for action in history[-10:]
                        ],
                    },
                    ensure_ascii=False,
                ),
            },
        ],
    }
    response = request_json(base_url + "/chat/completions", api_key, body)
    try:
        content = response["choices"][0]["message"]["content"]
        return _validate_result(_parse_content(content), page.get("url", ""))
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("Result model returned an invalid response") from exc
