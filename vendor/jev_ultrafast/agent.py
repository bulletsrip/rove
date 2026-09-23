"""The complete agent loop. Typed choices, observable state, bounded execution."""

import base64
import json
import re
import time
from pathlib import Path

from .browser import Browser, StalePage
from .model import action_space, choose, field_context, field_text
from .questions import MAX_STEPS


def _is_single_primitive_goal(goal):
    """Whether a targetless browser primitive is the complete user request."""
    normalized = re.sub(r"\s+", " ", (goal or "").strip().lower())
    return not re.search(
        r"\b(?:and|then|until|while|before|after|to\s+(?:find|see|reach|open|read|get|extract|submit))\b",
        normalized,
    )


def _progress_signature(page):
    """Capture meaningful page state while ignoring transient DOM identities."""
    actions = [
        {
            key: action.get(key)
            for key in ("kind", "role", "label", "value", "checked", "selected", "expanded", "href")
        }
        for action in page.get("actions", [])
    ]
    scroll = page.get("scroll") or {}
    containers = [tuple(container[:3]) for container in scroll.get("containers", []) if len(container) >= 3]
    return json.dumps(
        {
            "url": page.get("url"),
            "title": page.get("title"),
            "text": page.get("text"),
            "actions": actions,
            "scroll": {"y": scroll.get("y"), "containers": containers},
        },
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )


class Agent:
    def __init__(self, url, goals, *, record_dir=None, screenshots=False, collection=False):
        task = goals.strip() if isinstance(goals, str) else "\n".join(goals).strip()
        if not task:
            raise ValueError("Supply a task")
        plan = [task]
        self.pending_text = None
        self.stop_event = None
        self.no_progress_key = None
        self.no_progress_count = 0
        self.transition_counts = {}
        self.browser = Browser(url)
        self.record_dir = Path(record_dir) if record_dir else None
        self.screenshots = screenshots or bool(record_dir)
        try:
            page = self.browser.observe(screenshot=self.screenshots)
        except Exception:
            self.browser.close()
            raise
        self.state = dict(
            browser=self.browser,
            goal="\n".join(plan),
            goal_history=plan.copy(),
            page=page,
            decision=None,
            history=[],
            status="ready",
            plan=plan,
            plan_index=0,
            decisions=[],
            text_calls=[],
            continuation_context=None,
            block_reason=None,
            elapsed_ms=0,
            started_at=None,
            run_history_start=0,
            record=bool(self.record_dir),
            collection_mode=collection,
        )
        if self.record_dir:
            self.record_dir.mkdir(parents=True, exist_ok=True)
            if page.get("screenshot"):
                (self.record_dir / "000000.jpg").write_bytes(base64.b64decode(page["screenshot"]))

    def continue_with(self, goal, *, context=None):
        """Start a follow-up run while preserving the current page and history."""
        task = goal.strip() if isinstance(goal, str) else "\n".join(goal).strip()
        if not task:
            raise ValueError("Supply a task")
        if not self.state.get("browser"):
            raise ValueError("Start a task first")

        self.pending_text = None
        self.no_progress_key = None
        self.no_progress_count = 0
        self.transition_counts = {}
        context = context or {}
        continuation_context = {
            "previous_goal": context.get("previous_goal") or self.state.get("goal", ""),
            "previous_result": context.get("previous_result"),
        }
        if "completed_objectives" in context:
            continuation_context["completed_objectives"] = context["completed_objectives"]
        for key in ("collection_iteration", "collected_items"):
            if key in context:
                continuation_context[key] = context[key]
        self.state.update(
            goal=task,
            goal_history=[*self.state.get("goal_history", []), task],
            continuation_context=continuation_context,
            decision=None,
            status="ready",
            plan=[task],
            plan_index=0,
            decisions=[],
            text_calls=[],
            elapsed_ms=0,
            block_reason=None,
            started_at=None,
            run_history_start=len(self.state["history"]),
        )
        return self.snapshot()

    def snapshot(self):
        return {
            **{k: v for k, v in self.state.items() if k != "browser"},
            "elements": action_space(self.state["page"]["actions"])[0],
        }

    def command(self, name, body=None):
        body = body or {}
        state = self.state
        if name == "tick":
            try:
                self.command("predict", {})
                return self.command("act", {"fingerprint": state["page"]["fingerprint"]})
            except StalePage:
                state["decision"] = None
                state["status"] = "ready"
                state["page"] = state["browser"].observe(screenshot=self.screenshots)
                state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
                return self.snapshot()
        elif name == "predict":
            if not state["browser"]:
                raise ValueError("Start a task first")
            if state["started_at"] is None:
                state["started_at"] = time.perf_counter()
            if not state["browser"].fresh(state["page"]):
                state["page"] = state["browser"].observe(screenshot=self.screenshots)
            state["decision"] = None
            if state["status"] in {"done", "blocked"}:
                raise ValueError("This run has stopped. Start a fresh task or continue the session.")
            if len(state["decisions"]) >= MAX_STEPS * 2:
                raise ValueError("Reached the model-call budget")
            state["decision"] = choose(
                state["page"],
                state["goal"],
                state["history"],
                state.get("continuation_context"),
                state.get("run_history_start", 0),
                state.get("collection_mode", False),
            )
            state["decisions"].append(
                {
                    **state["decision"],
                    "fingerprint": state["page"]["fingerprint"],
                    "elapsed_ms": round((time.perf_counter() - state["started_at"]) * 1000),
                }
            )
            state["status"] = "predicted"
        elif name == "act":
            decision, page = state["decision"], state["page"]
            if not decision or body.get("fingerprint") != page["fingerprint"]:
                raise ValueError("Observe and choose before acting")
            # Consume once, before any mutation or model call. A retry cannot double-click.
            state["decision"] = None
            selected = decision["choice"]
            if selected in {"DONE", "BLOCKED"}:
                if not state["browser"].fresh(page):
                    state["status"] = "ready"
                    raise StalePage("Page changed since the decision. Choose again.")
                state["status"] = "done" if selected == "DONE" else "blocked"
                state["plan_index"] = int(selected == "DONE")
                state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
                return self.snapshot()
            action = next(a for a in page["actions"] if a["id"] == selected)
            actions_this_run = len(state["history"]) - state.get("run_history_start", 0)
            if actions_this_run >= MAX_STEPS:
                state["status"] = "blocked"
                raise ValueError(f"Stopped at the {MAX_STEPS}-action budget")
            text, helper = None, None
            if action["kind"] == "fill":
                if not state["browser"].fresh(page):
                    raise StalePage("Page changed before text generation. Choose again.")
                context = field_context(state["goal"], action, page, state["history"])
                if self.pending_text and self.pending_text[0] == context:
                    _, text, helper = self.pending_text
                else:
                    text, helper = field_text(context)
                    self.pending_text = (context, text, helper)
                    state["text_calls"].append({**helper, "field": action["label"], "value": text})
            # Browser.act checks freshness immediately before input, including after text generation.
            state["browser"].act(action, page, text=text)
            self.pending_text = None
            state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
            # Record execution before observing. A stale post-action observation must not erase the action.
            state["history"].append(
                {
                    "step": len(state["history"]) + 1,
                    "action": action["label"],
                    "kind": action["kind"],
                    "choice": selected,
                    "probability": decision["probabilities"][selected],
                    "confidence": decision["confidence"],
                    "latency_ms": decision["latency_ms"],
                    "text": text,
                    "text_helper": helper["model"] if helper else None,
                    "text_latency_ms": helper["latency_ms"] if helper else 0,
                    "operation": decision["operation"],
                    "target": decision["target"],
                    "page_changed": None,
                    "url": page["url"],
                    "usage": decision["usage"],
                    "executed_ms": round((time.perf_counter() - state["started_at"]) * 1000),
                    "elapsed_ms": state["elapsed_ms"],
                }
            )
            try:
                state["page"] = state["browser"].observe(screenshot=self.screenshots)
            except StalePage:
                raise
            state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
            state["history"][-1].update(
                page_changed=state["page"]["fingerprint"] != page["fingerprint"],
                url=state["page"]["url"],
                elapsed_ms=state["elapsed_ms"],
            )
            if state["record"] and state["page"].get("screenshot"):
                (self.record_dir / f"{state['elapsed_ms']:06d}.jpg").write_bytes(
                    base64.b64decode(state["page"]["screenshot"])
                )
            page_changed = state["history"][-1]["page_changed"]
            progress_key = (action["kind"], action["id"], _progress_signature(state["page"]))
            if progress_key == self.no_progress_key:
                self.no_progress_count += 1
            else:
                self.no_progress_key = progress_key
                self.no_progress_count = 1
            stalled = self.no_progress_count >= 2
            transition_key = (
                action["kind"],
                action["id"],
                _progress_signature(page),
                _progress_signature(state["page"]),
            )
            self.transition_counts = getattr(self, "transition_counts", {})
            self.transition_counts[transition_key] = self.transition_counts.get(transition_key, 0) + 1
            cycling = self.transition_counts[transition_key] >= 2
            single_primitive = action["kind"] in {"scroll", "back", "reload", "key"} and _is_single_primitive_goal(state["goal"])
            if cycling:
                state["status"] = "blocked"
                state["block_reason"] = "The same page transition repeated; the agent appears to be in a cycle."
            elif stalled:
                state["status"] = "blocked"
                state["block_reason"] = "The same action produced no observable page progress twice."
            else:
                state["status"] = "done" if single_primitive else "ready"
        else:
            raise ValueError("Unknown command")
        return self.snapshot()

    def run(self):
        while self.state["status"] not in {"done", "blocked"}:
            if self.stop_event is not None and self.stop_event.is_set():
                self.state["status"] = "stopped"
                break
            yield self.command("tick")

    def close(self):
        self.browser.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
