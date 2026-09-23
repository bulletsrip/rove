from __future__ import annotations

import json
import os
from typing import Callable


PLANNER_PROMPT = """You are Rove's task planner.

Convert the user's browser request into the smallest ordered list of independently verifiable objectives.
Return exactly one JSON object with this shape:
{"mode":"standard","objectives":[{"id":"objective_1","instruction":"...","target_count":null}]}

Rules:
- Set mode to "collection" when the outcome requires gathering multiple items across scrolling,
  pagination, Load more controls, repeated pages, or an unknown number of results. Set mode to
  "standard" for a single answer, one browser operation, or a finite list of independent objectives.
- Preserve the user's intent and important names, values, URLs, and constraints.
- Return one objective for a simple request.
- Split multiple independent deliverables into separate objectives.
- Keep steps that must happen together in the same objective.
- An objective must describe an outcome, not a browser click sequence.
- Do not invent missing information or add objectives the user did not request.
- For repeated comparisons or lookups, create one objective per requested item.
- For a collection with a finite requested count, set that objective's target_count to the integer.
  Otherwise set target_count to null. Do not infer a count that the user did not request.
- Return no more than 12 objectives.
"""


class PlanningUnavailable(RuntimeError):
    """The optional planner could not produce a usable task plan."""


def _fallback_plan(goal):
    return {"mode": "standard", "objectives": [{"id": "objective_1", "instruction": goal}]}


def _validate_plan(value, goal):
    if not isinstance(value, dict) or not isinstance(value.get("objectives"), list):
        raise ValueError("Planner returned an invalid objective list")
    mode = value.get("mode", "standard")
    if mode not in {"standard", "collection"}:
        raise ValueError("Planner returned an invalid task mode")
    objectives = []
    for index, item in enumerate(value["objectives"], 1):
        instruction = item.get("instruction") if isinstance(item, dict) else item
        if not isinstance(instruction, str):
            raise ValueError("Planner returned an invalid objective")
        instruction = instruction.strip()
        if not instruction or len(instruction) > 2000:
            raise ValueError("Planner returned an invalid objective")
        objective = {"id": f"objective_{index}", "instruction": instruction}
        if isinstance(item, dict) and item.get("target_count") is not None:
            target_count = item["target_count"]
            if type(target_count) is not int or target_count < 1:
                raise ValueError("Planner returned an invalid target count")
            objective["target_count"] = target_count
        objectives.append(objective)
    if not objectives or len(objectives) > 12:
        raise ValueError("Planner returned an invalid objective count")
    return {"mode": mode, "objectives": objectives}


def plan_goal(goal: str, *, request_json: Callable | None = None, api_key=None, base_url=None):
    """Return a model-classified task plan, falling back safely to one standard objective."""
    goal = goal.strip()

    api_key = api_key or os.getenv("TEXT_MODEL_API_KEY")
    if not api_key:
        return _fallback_plan(goal)
    if request_json is None:
        from jev_ultrafast.model import post_json

        request_json = post_json
    base_url = (base_url or os.getenv("TEXT_MODEL_BASE_URL", "https://api.deepseek.com/v1")).rstrip("/")
    body = {
        "model": os.getenv("TEXT_MODEL", "deepseek-chat"),
        "max_tokens": 1600,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": PLANNER_PROMPT},
            {"role": "user", "content": goal},
        ],
    }
    try:
        response = request_json(base_url + "/chat/completions", api_key, body)
        content = response["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            raise ValueError("Planner returned non-text content")
        content = content.strip()
        if content.startswith("```") and content.endswith("```"):
            content = content.split("\n", 1)[1].rsplit("\n", 1)[0].strip()
        return _validate_plan(json.loads(content), goal)
    except (KeyError, IndexError, TypeError, ValueError, RuntimeError, json.JSONDecodeError):
        return _fallback_plan(goal)
