from __future__ import annotations

import json
import os
import re
from typing import Callable
from urllib.parse import urlparse


RESULT_SYSTEM_PROMPT = """You are Rove's final result verifier.

Verify the user's outcome against the supplied final page and return exactly one JSON object with:
kind, summary, facts, items, source_url

Classify the outcome:
- answer: the supplied page contains the requested information or extracted items
- completion: the requested browser action is visibly complete
- needs_review: the supplied evidence is insufficient or contradicts the requested outcome

Verification:
1. Read the goal as an outcome, not as a list of browser actions.
2. Ground every fact and item in the supplied final page, full page text, visible result, or supplied page links.
3. Use action history as context for how the page was reached; the final evidence determines completion.
4. Preserve observed names, places, values, and search terms.
5. Use visible links as evidence for requested URLs; do not invent URLs from titles.
6. Return needs_review when the requested evidence is absent, incomplete, or visibly contradicted.

Output rules:
- summary is concise and useful.
- facts is an object of short label/value strings; use {} when no single-value facts are available.
- items is an ordered list of extracted strings; use [] when no list is requested or visible.
- For a collection request, return at most the requested number of items. Keep each item to one compact line using the requested fields, separated by " | ". Do not add commentary inside items.
- source_url is the supplied page URL unless the page visibly provides a more specific source URL.
- Report only the evidence contained in the supplied input. Do not estimate, infer missing values, or claim unseen pages."""


class ResultExtractionUnavailable(RuntimeError):
    """The configured answer model cannot be called."""


def _parse_content(content):
    if not isinstance(content, str):
        raise ValueError("Result model returned non-text content")
    content = content.strip()
    if content.startswith("```") and content.endswith("```"):
        content = content.split("\n", 1)[1].rsplit("\n", 1)[0]
    return json.loads(content)


def is_information_request(goal):
    return bool(
        re.search(
            r"\b(?:scrap(?:e|ing|ed)?|extract(?:ed|ion)?|collect|list|enumerate|show|return|give me|tell me|how (?:many|much|is|are)|what (?:is|are)|which|where|when|who|read|calculate|compare|distance|price|cost|address|email|phone|title|name|details|information|result)\b",
            goal or "",
            re.IGNORECASE,
        )
    )


def _validate_result(value, fallback_url, *, goal=""):
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
    items = value.get("items", [])
    if not isinstance(items, list) or len(items) > 500 or any(
        not isinstance(item, str) or not item.strip() or len(item) > 2000 for item in items
    ):
        raise ValueError("Result model returned invalid items")
    if is_information_request(goal) and kind == "completion":
        kind = "answer" if facts or items else "needs_review"
    source_url = value.get("source_url") or fallback_url
    parsed_source = urlparse(source_url) if isinstance(source_url, str) else None
    if not parsed_source or parsed_source.scheme not in {"http", "https"} or not parsed_source.netloc:
        source_url = fallback_url
    return {
        "kind": kind,
        "summary": summary.strip(),
        "facts": facts,
        "items": items,
        "source_url": source_url,
    }


def _compact_page_links(page):
    """Keep evidence useful without flooding the verifier with duplicate DOM links."""
    candidates = []
    seen_urls = set()
    for link in page.get("page_links", []):
        if not isinstance(link, dict) or not link.get("url"):
            continue
        url = link["url"]
        context = f"{link.get('text', '')} {link.get('classes', '')}".strip()
        label = str(link.get("label", "")).strip()
        navigation = bool(re.search(r"\b(?:next|previous|page\s+\d+|load more)\b", label, re.I))
        score = 3 if navigation else 2 if context else 1
        if url in seen_urls:
            continue
        seen_urls.add(url)
        candidates.append({"label": label, "url": url, "context": context[:500], "score": score})
    candidates.sort(key=lambda item: (-item["score"], item["url"]))
    return [{key: item[key] for key in ("label", "url", "context")} for item in candidates[:60]]


def extract_result(goal, page, history, *, request_json: Callable | None = None, api_key=None, base_url=None):
    """Verify and summarize the final observed page for a completed task."""
    use_default_requester = request_json is None
    if request_json is None:
        from jev_ultrafast.model import post_json

        request_json = post_json
    api_key = api_key or os.getenv("TEXT_MODEL_API_KEY")
    if not api_key:
        raise ResultExtractionUnavailable("TEXT_MODEL_API_KEY is not configured")
    base_url = (base_url or os.getenv("TEXT_MODEL_BASE_URL", "https://api.deepseek.com/v1")).rstrip("/")
    page_links = _compact_page_links(page)
    seen_urls = {link["url"] for link in page_links}
    for action in page.get("actions", []):
        url = action.get("href", "")
        if url and url not in seen_urls and len(page_links) < 60:
            page_links.append({"label": action.get("label", ""), "url": url, "context": ""})
            seen_urls.add(url)
    body = {
        "model": os.getenv("TEXT_MODEL", "deepseek-chat"),
        "max_tokens": 1800,
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
                            "text": (page.get("text") or "")[:6000],
                            "visible_links": page_links,
                            "full_text": (page.get("full_text") or page.get("text") or "")[:7000],
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
    endpoint = base_url + "/chat/completions"
    if use_default_requester:
        response = request_json(
            endpoint,
            api_key,
            body,
            timeout=float(os.getenv("TEXT_MODEL_RESULT_TIMEOUT", "90")),
        )
    else:
        response = request_json(endpoint, api_key, body)
    try:
        content = response["choices"][0]["message"]["content"]
        return _validate_result(_parse_content(content), page.get("url", ""), goal=goal)
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("Result model returned an invalid response") from exc
