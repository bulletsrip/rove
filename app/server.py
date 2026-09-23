from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "vendor"))
from jev_ultrafast import Agent
from app.long_tasks import (
    MAX_LONG_ACTIONS,
    MAX_LONG_ACTIONS_PER_PASS,
    MAX_LONG_ITERATIONS,
    MAX_LONG_NO_NEW_ITERATIONS,
    collection_instruction,
    continuation_instruction,
    batch_extraction_goal,
    merge_items,
)
from app.planner import plan_goal
from app.results import extract_result, is_information_request
PORT = int(os.getenv("APP_PORT", "8080"))
MAX_BODY_BYTES = 64 * 1024
MAX_GOAL_LENGTH = 2000
ACTIVE_TASK_STATES = {"starting", "running", "extracting", "stopping"}
STATIC_FILES = {
    "/": ("static/index.html", "text/html; charset=utf-8"),
    "/index.html": ("static/index.html", "text/html; charset=utf-8"),
    "/app.js": ("static/app.js", "text/javascript; charset=utf-8"),
    "/styles.css": ("static/styles.css", "text/css; charset=utf-8"),
    "/assets/rove-mark.svg": ("static/assets/rove-mark.svg", "image/svg+xml"),
}
_lock = threading.Lock()
_run = {
    "status": "idle",
    "url": None,
    "goal": None,
    "steps": [],
    "error": None,
    "result": None,
    "result_history": [],
    "objectives": [],
    "objective_index": 0,
    "can_continue": False,
    "started_at": None,
    "long_task": False,
    "iteration": 0,
    "collected_items": [],
}
_agent = None
_agent_lock = threading.Lock()
_session_lock = threading.Lock()
_cancel_event = None


def set_desktop_theme(theme: str):
    color = "#f2f7f7" if theme == "light" else "#090d11"
    display = os.getenv("DISPLAY", ":99")
    for _ in range(20):
        try:
            subprocess.run(
                ["xsetroot", "-display", display, "-solid", color],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=1,
            )
            return
        except (OSError, subprocess.SubprocessError):
            time.sleep(0.1)


def snapshot():
    with _lock:
        return dict(
            _run,
            steps=list(_run["steps"]),
            result_history=[dict(entry) for entry in _run["result_history"]],
            objectives=[dict(objective) for objective in _run["objectives"]],
            collected_items=list(_run["collected_items"]),
        )


def has_resumable_session():
    with _lock:
        resumable_state = _run["status"] in {"done", "answered", "blocked", "stopped", "error"} and _run["can_continue"]
    if not resumable_state:
        return False
    with _agent_lock:
        return _agent is not None


def is_continuation_request(path):
    """Continuation is explicit; a normal task request always starts fresh."""
    return path == "/api/tasks/continue"


def _close_agent(agent):
    if agent is None:
        return
    try:
        agent.close()
    except Exception:
        pass


def _finish_stopped(agent, cancel_event):
    with _lock:
        if _cancel_event is cancel_event:
            _run["status"] = "stopped"
            _run["error"] = None
            _run["can_continue"] = agent is not None


def reset_session():
    """Discard the current completed session without starting a new task."""
    global _agent, _cancel_event

    with _session_lock:
        with _lock:
            if _run["status"] in ACTIVE_TASK_STATES:
                return False
        with _agent_lock:
            previous = _agent
            _agent = None
        _close_agent(previous)
        with _lock:
            _run.update(
                status="idle",
                url=None,
                goal=None,
                steps=[],
                error=None,
                result=None,
                result_history=[],
                objectives=[],
                objective_index=0,
                can_continue=False,
                started_at=None,
                long_task=False,
                iteration=0,
                collected_items=[],
            )
            _cancel_event = None
    return True


def _update_from_agent_state(agent, state):
    history = state.get("history", [])
    page = state.get("page") or agent.state.get("page", {})
    with _lock:
        _run["status"] = state.get("status") if state.get("status") in {"done", "blocked"} else "running"
        _run["steps"] = history[-50:]
        if page.get("url"):
            _run["url"] = page["url"]


def _objective_result(agent, goal, history_start, final_status):
    if not is_information_request(goal):
        return {
            "kind": "completion" if final_status == "done" else "needs_review",
            "summary": "Objective completed." if final_status == "done" else "Objective stopped before completion.",
            "facts": {},
            "items": [],
            "source_url": agent.state.get("page", {}).get("url", ""),
        }

    with _lock:
        _run["status"] = "extracting"
    try:
        return extract_result(
            goal,
            agent.state["page"],
            agent.state["history"][history_start:],
        )
    except Exception as exc:
        with _lock:
            _run["error"] = f"Result verifier failed: {type(exc).__name__}: {str(exc)[:300]}"
        return {
            "kind": "needs_review",
            "summary": "The objective finished, but Rove could not verify its result.",
            "facts": {},
            "items": [],
            "source_url": agent.state.get("page", {}).get("url", ""),
        }


def _aggregate_results(objectives, results):
    if len(results) == 1:
        return results[0]

    facts = {}
    items = []
    for index, (objective, result) in enumerate(zip(objectives, results), 1):
        prefix = f"{index}. {objective['instruction'][:100]}"
        for label, value in result.get("facts", {}).items():
            facts[f"{prefix} — {label}"] = value
        for item in result.get("items", []):
            items.append(f"{prefix} — {item}")
        if not result.get("facts") and not result.get("items"):
            items.append(f"{prefix} — {result.get('summary', 'No result returned.')}")

    if any(result.get("kind") == "needs_review" for result in results):
        kind = "needs_review"
    elif any(result.get("kind") == "answer" for result in results):
        kind = "answer"
    else:
        kind = "completion"
    completed = sum(result.get("kind") != "needs_review" for result in results)
    source_url = next((result.get("source_url") for result in reversed(results) if result.get("source_url")), "")
    return {
        "kind": kind,
        "summary": f"Completed {completed} of {len(objectives)} objectives.",
        "facts": facts,
        "items": items,
        "source_url": source_url,
    }


def _record_objective(index, objective, result):
    with _lock:
        entry = {
            "index": len(_run["result_history"]) + 1,
            "goal": objective["instruction"],
            "result": result,
        }
        _run["result_history"].append(entry)
        _run["result"] = result
        _run["objectives"][index].update(
            status="complete" if result.get("kind") != "needs_review" else "needs_review",
            result=result,
        )


def _record_long_cycle(objective, result, iteration):
    with _lock:
        _run["result_history"].append(
            {
                "index": len(_run["result_history"]) + 1,
                "iteration": iteration,
                "goal": objective["instruction"],
                "result": result,
            }
        )


def _collection_result(results, items, source_url, *, complete):
    facts = {}
    for result in results:
        for label, value in result.get("facts", {}).items():
            if label not in facts:
                facts[label] = value
    if items:
        facts["collected_items"] = str(len(items))
    if items and complete:
        kind = "answer"
        summary = f"Collected {len(items)} unique items across {len(results)} passes."
    elif items:
        kind = "needs_review"
        summary = f"Collected {len(items)} unique items, but the page did not clearly finish."
    else:
        kind = "needs_review"
        summary = "The collection task finished without a verified item list."
    return {
        "kind": kind,
        "summary": summary,
        "facts": facts,
        "items": items,
        "source_url": source_url,
    }


def _run_long_objective(agent, objective, cancel_event, *, continuation=False, continuation_context=None):
    """Run a collection goal in bounded passes while preserving the same browser."""
    goal = objective["instruction"]
    results = []
    previous_result = continuation_context.get("previous_result") if continuation and continuation_context else None
    if previous_result is None and continuation:
        with _lock:
            previous_result = _run["result"]
    collected_items = list(previous_result.get("items", [])) if isinstance(previous_result, dict) else []
    no_new_iterations = 0
    target_count = objective.get("target_count")
    reached_target = False
    initial_history_start = len(agent.state.get("history", []))
    source_url = agent.state.get("page", {}).get("url", "")

    for iteration in range(1, MAX_LONG_ITERATIONS + 1):
        if cancel_event.is_set():
            _finish_stopped(agent, cancel_event)
            return None
        with _lock:
            _run["iteration"] = iteration
            _run["collected_items"] = list(collected_items)

        history_start = len(agent.state.get("history", []))
        if iteration == 1 and not continuation:
            agent.stop_event = cancel_event
        else:
            context = {
                "previous_goal": goal,
                "previous_result": results[-1] if results else None,
                "collection_iteration": iteration,
                "collected_items": collected_items[-100:],
            }
            agent.stop_event = cancel_event
            agent.continue_with(
                continuation_instruction(goal, iteration, collected_items, target_count),
                context=context,
            )
        try:
            for state in agent.run():
                _update_from_agent_state(agent, state)
                if len(agent.state.get("history", [])) - history_start >= MAX_LONG_ACTIONS_PER_PASS:
                    agent.state["status"] = "ready"
                    break
        except ValueError as exc:
            if "budget" not in str(exc).lower():
                raise
            agent.state["status"] = "blocked"
        if cancel_event.is_set() or agent.state.get("status") == "stopped":
            _finish_stopped(agent, cancel_event)
            return None

        final_status = agent.state.get("status", "done")
        result = _objective_result(agent, batch_extraction_goal(goal), history_start, final_status)
        results.append(result)
        collected_items, added = merge_items(collected_items, result)
        if target_count is not None and len(collected_items) >= target_count:
            collected_items = collected_items[:target_count]
            reached_target = True
        source_url = result.get("source_url") or agent.state.get("page", {}).get("url", "")
        no_new_iterations = no_new_iterations + 1 if added == 0 else 0
        with _lock:
            _run["collected_items"] = list(collected_items)
            _run["result"] = _collection_result(results, collected_items, source_url, complete=False)
        _record_long_cycle(objective, result, iteration)

        actions_used = len(agent.state.get("history", [])) - initial_history_start
        if final_status == "blocked" or actions_used >= MAX_LONG_ACTIONS:
            break
        if reached_target:
            break
        if no_new_iterations >= MAX_LONG_NO_NEW_ITERATIONS:
            break

    complete = reached_target or (bool(collected_items) and no_new_iterations >= MAX_LONG_NO_NEW_ITERATIONS)
    aggregate = _collection_result(results, collected_items, source_url, complete=complete)
    with _lock:
        _run["result"] = aggregate
        _run["objectives"][0].update(
            status="complete" if aggregate["kind"] == "answer" else "needs_review",
            result=aggregate,
        )
    return aggregate


def execute(url: str, goal: str, continuation: bool = False, cancel_event=None, continuation_context=None):
    global _agent
    agent = None
    cancel_event = cancel_event or threading.Event()
    try:
        planned = plan_goal(goal)
        if isinstance(planned, dict):
            task_mode = planned.get("mode", "standard")
            objectives = planned.get("objectives", [])
        else:
            # Keep compatibility with callers that still return the old objective list.
            task_mode = "standard"
            objectives = planned
        if not objectives:
            raise ValueError("Planner returned no objectives")
        with _lock:
            _run["objectives"] = [
                {
                    "id": objective["id"],
                    "instruction": objective["instruction"],
                    "status": "pending",
                    **({"target_count": objective["target_count"]} if "target_count" in objective else {}),
                }
                for objective in objectives
            ]
            _run["objective_index"] = 0
            _run["long_task"] = task_mode == "collection" and len(objectives) == 1
            _run["iteration"] = 0
            _run["collected_items"] = []

        if continuation:
            with _agent_lock:
                agent = _agent
            if agent is None:
                raise RuntimeError("There is no completed session to continue")
            if continuation_context is None:
                with _lock:
                    previous_result = _run["result"]
                    if previous_result is None and _run["result_history"]:
                        previous_result = _run["result_history"][-1]["result"]
                    continuation_context = {
                        "previous_goal": _run["goal"],
                        "previous_result": previous_result,
                    }
            with _lock:
                _run["goal"] = goal
        else:
            with _agent_lock:
                previous = _agent
                _agent = None
            _close_agent(previous)
            agent_goal = objectives[0]["instruction"]
            if task_mode == "collection":
                agent_goal = collection_instruction(
                    agent_goal,
                    target_count=objectives[0].get("target_count"),
                )
            agent = Agent(url, agent_goal, screenshots=False, collection=task_mode == "collection")
            agent.stop_event = cancel_event
            with _agent_lock:
                _agent = agent
            with _lock:
                _run["can_continue"] = True

        if len(objectives) == 1 and task_mode == "collection":
            result = _run_long_objective(
                agent,
                objectives[0],
                cancel_event,
                continuation=continuation,
                continuation_context=continuation_context,
            )
            if result is None:
                return
            with _lock:
                _run["status"] = "answered" if result["kind"] == "answer" else "blocked"
                _run["can_continue"] = agent is not None
            return

        results = []
        for index, objective in enumerate(objectives):
            if cancel_event.is_set():
                _finish_stopped(agent, cancel_event)
                return
            with _lock:
                _run["objective_index"] = index
                _run["objectives"][index]["status"] = "running"
            history_start = len(agent.state.get("history", []))
            if (continuation and index == 0) or index > 0:
                context = dict(continuation_context or {})
                if index > 0:
                    context["completed_objectives"] = [
                        {"instruction": item["instruction"], "result": result}
                        for item, result in zip(objectives[:index], results)
                    ]
                agent.stop_event = cancel_event
                agent.continue_with(objective["instruction"], context=context)
                history_start = len(agent.state.get("history", []))
            for state in agent.run():
                _update_from_agent_state(agent, state)
            if cancel_event.is_set() or agent.state.get("status") == "stopped":
                _finish_stopped(agent, cancel_event)
                return
            final_status = agent.state.get("status", "done")
            result = _objective_result(agent, objective["instruction"], history_start, final_status)
            if cancel_event.is_set():
                _finish_stopped(agent, cancel_event)
                return
            results.append(result)
            _record_objective(index, objective, result)

        aggregate = _aggregate_results(objectives, results)
        with _lock:
            _run["result"] = aggregate
            _run["status"] = (
                "blocked" if aggregate["kind"] == "needs_review"
                else "answered" if aggregate["kind"] == "answer"
                else "done"
            )
            _run["can_continue"] = agent is not None
    except Exception as exc:
        if cancel_event.is_set():
            _finish_stopped(agent, cancel_event)
            return
        with _lock:
            _run["status"] = "error"
            _run["error"] = f"{type(exc).__name__}: {exc}"
            _run["can_continue"] = agent is not None


class Handler(BaseHTTPRequestHandler):
    def send_bytes(self, code, data, content_type, cache_control="no-store"):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", cache_control)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, code, payload):
        data = json.dumps(payload).encode()
        self.send_bytes(code, data, "application/json; charset=utf-8")

    def send_file(self, relative_path, content_type):
        try:
            data = (ROOT / relative_path).read_bytes()
        except OSError:
            return self.send_json(404, {"error": "not found"})
        return self.send_bytes(200, data, content_type)

    def read_json_body(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValueError("Content-Length must be an integer") from None
        if length < 0 or length > MAX_BODY_BYTES:
            raise ValueError("request body is too large")
        body = json.loads(self.rfile.read(length))
        if not isinstance(body, dict):
            raise ValueError("request body must be a JSON object")
        return body

    def start_task(self, body):
        global _cancel_event

        goal = body.get("goal", "")
        if not isinstance(goal, str):
            raise ValueError("goal must be a string")
        goal = goal.strip()
        if not goal or len(goal) > MAX_GOAL_LENGTH:
            raise ValueError(f"goal must be 1-{MAX_GOAL_LENGTH} characters")

        with _session_lock:
            with _lock:
                if _run["status"] in ACTIVE_TASK_STATES:
                    return self.send_json(409, {"error": "a task is already running"})
            continuation_request = is_continuation_request(self.path)
            if continuation_request:
                with _agent_lock:
                    if _agent is None:
                        return self.send_json(409, {"error": "there is no resumable session to continue"})
                with _lock:
                    if _run["status"] not in {"done", "answered", "blocked", "stopped", "error"} or not _run["can_continue"]:
                        return self.send_json(409, {"error": "the current session is not resumable"})
                    previous_result = _run["result"]
                    if previous_result is None and _run["result_history"]:
                        previous_result = _run["result_history"][-1]["result"]
                    continuation_context = {
                        "previous_goal": _run["goal"],
                        "previous_result": previous_result,
                    }
                    _cancel_event = threading.Event()
                    cancel_event = _cancel_event
                    _run.update(
                        status="starting",
                        goal=goal,
                        error=None,
                        result=None,
                        objectives=[],
                        objective_index=0,
                        long_task=False,
                        iteration=0,
                        collected_items=[],
                        can_continue=True,
                        started_at=time.time(),
                    )
                threading.Thread(
                    target=execute,
                    args=("", goal, True, cancel_event, continuation_context),
                    daemon=True,
                ).start()
                return self.send_json(202, snapshot())

            url = body.get("url", "")
            if not isinstance(url, str):
                raise ValueError("url must be a string")
            url = url.strip()
            parsed_url = urlparse(url)
            if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
                raise ValueError("url must be a valid http(s) URL")
            with _lock:
                _cancel_event = threading.Event()
                cancel_event = _cancel_event
                _run.update(
                    status="starting",
                    url=url,
                    goal=goal,
                    steps=[],
                    error=None,
                    result=None,
                    result_history=[],
                    objectives=[],
                    objective_index=0,
                    long_task=False,
                    iteration=0,
                    collected_items=[],
                    can_continue=False,
                    started_at=time.time(),
                )
            threading.Thread(target=execute, args=(url, goal, False, cancel_event), daemon=True).start()
            return self.send_json(202, snapshot())

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/status":
            return self.send_json(200, snapshot())
        if path in STATIC_FILES:
            relative_path, content_type = STATIC_FILES[path]
            return self.send_file(relative_path, content_type)
        self.send_json(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/api/tasks/stop":
            with _lock:
                if _run["status"] not in {"starting", "running", "extracting"} or _cancel_event is None:
                    return self.send_json(409, {"error": "there is no active task to stop"})
                _run["status"] = "stopping"
                cancel_event = _cancel_event
            cancel_event.set()
            return self.send_json(202, snapshot())
        if self.path == "/api/theme":
            try:
                body = self.read_json_body()
                theme = body.get("theme")
                if theme not in {"light", "dark"}:
                    raise ValueError("theme must be light or dark")
                set_desktop_theme(theme)
                return self.send_json(200, {"theme": theme})
            except (ValueError, json.JSONDecodeError) as exc:
                return self.send_json(400, {"error": str(exc)})
        if self.path == "/api/sessions":
            if not reset_session():
                return self.send_json(409, {"error": "a task is already running"})
            return self.send_json(200, snapshot())
        if self.path not in {"/api/tasks", "/api/tasks/continue"}:
            return self.send_json(404, {"error": "not found"})
        try:
            return self.start_task(self.read_json_body())
        except (ValueError, json.JSONDecodeError) as exc:
            return self.send_json(400, {"error": str(exc)})

    def log_message(self, *_args):
        pass


if __name__ == "__main__":
    set_desktop_theme("dark")
    print(f"Rove listening on http://0.0.0.0:{PORT}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
