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
    "can_continue": False,
    "started_at": None,
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
        return dict(_run, steps=list(_run["steps"]), result_history=list(_run["result_history"]))


def has_resumable_session():
    with _lock:
        resumable_state = _run["status"] in {"done", "answered", "blocked", "stopped", "error"} and _run["can_continue"]
    if not resumable_state:
        return False
    with _agent_lock:
        return _agent is not None


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
                can_continue=False,
                started_at=None,
            )
            _cancel_event = None
    return True


def execute(url: str, goal: str, continuation: bool = False, cancel_event=None, continuation_context=None):
    global _agent
    agent = None
    cancel_event = cancel_event or threading.Event()
    try:
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
            agent.stop_event = cancel_event
            agent.continue_with(goal, context=continuation_context)
            with _lock:
                _run["goal"] = goal
        else:
            with _agent_lock:
                previous = _agent
                _agent = None
            _close_agent(previous)
            agent = Agent(url, goal, screenshots=False)
            agent.stop_event = cancel_event
            with _agent_lock:
                _agent = agent
            with _lock:
                _run["can_continue"] = True

        for state in agent.run():
            history = state.get("history", [])
            page = state.get("page") or agent.state.get("page", {})
            with _lock:
                _run["status"] = state.get("status") if state.get("status") in {"done", "blocked"} else "running"
                _run["steps"] = history[-50:]
                if page.get("url"):
                    _run["url"] = page["url"]
        if cancel_event.is_set() or agent.state.get("status") == "stopped":
            _finish_stopped(agent, cancel_event)
            return
        final_status = agent.state.get("status", "done")
        if final_status in {"done", "blocked"}:
            if not is_information_request(agent.state["goal"]):
                result = {
                    "kind": "completion" if final_status == "done" else "needs_review",
                    "summary": "Task completed." if final_status == "done" else "The task stopped before completion.",
                    "facts": {},
                    "items": [],
                    "source_url": agent.state.get("page", {}).get("url", ""),
                }
                with _lock:
                    _run["result"] = result
                    _run["result_history"].append(
                        {
                            "index": len(_run["result_history"]) + 1,
                            "goal": agent.state["goal"],
                            "result": result,
                        }
                    )
                    _run["status"] = final_status
                return
            with _lock:
                _run["status"] = "extracting"
            if cancel_event.is_set():
                _finish_stopped(agent, cancel_event)
                return
            try:
                result = extract_result(
                    agent.state["goal"],
                    agent.state["page"],
                    agent.state["history"],
                )
            except Exception:
                result = {
                    "kind": "needs_review",
                    "summary": "The task finished, but Rove could not verify a final result.",
                    "facts": {},
                    "items": [],
                    "source_url": agent.state.get("page", {}).get("url", ""),
                }
            if cancel_event.is_set():
                _finish_stopped(agent, cancel_event)
                return
            with _lock:
                _run["result"] = result
                _run["result_history"].append(
                    {
                        "index": len(_run["result_history"]) + 1,
                        "goal": agent.state["goal"],
                        "result": result,
                    }
                )
                _run["status"] = "answered" if result["kind"] == "answer" else final_status
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
            continuation_request = self.path == "/api/tasks/continue" or (
                self.path == "/api/tasks" and has_resumable_session()
            )
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
