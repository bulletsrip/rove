from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "vendor"))
from jev_ultrafast import Agent
from app.results import extract_result
PORT = int(os.getenv("APP_PORT", "8080"))
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
            if _run["status"] in {"starting", "running", "extracting", "stopping"}:
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


def execute(url: str, goal: str, continuation: bool = False, cancel_event=None):
    global _agent
    agent = None
    cancel_event = cancel_event or threading.Event()
    try:
        if continuation:
            with _agent_lock:
                agent = _agent
            if agent is None:
                raise RuntimeError("There is no completed session to continue")
            agent.stop_event = cancel_event
            agent.continue_with(goal)
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
            with _lock:
                _run["status"] = state.get("status") if state.get("status") in {"done", "blocked"} else "running"
                _run["steps"] = history[-50:]
        if cancel_event.is_set() or agent.state.get("status") == "stopped":
            _finish_stopped(agent, cancel_event)
            return
        final_status = agent.state.get("status", "done")
        if final_status in {"done", "blocked"}:
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
    def send_json(self, code, payload):
        data = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/api/status":
            return self.send_json(200, snapshot())
        if self.path in ("/", "/index.html"):
            data = (ROOT / "static/index.html").read_bytes()
            self.send_response(200); self.send_header("Content-Type", "text/html"); self.send_header("Cache-Control", "no-store"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data); return
        if self.path == "/app.js":
            data = (ROOT / "static/app.js").read_bytes()
            self.send_response(200); self.send_header("Content-Type", "text/javascript"); self.send_header("Cache-Control", "no-store"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data); return
        if self.path == "/styles.css":
            data = (ROOT / "static/styles.css").read_bytes()
            self.send_response(200); self.send_header("Content-Type", "text/css"); self.send_header("Cache-Control", "no-store"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data); return
        if self.path == "/assets/rove-mark.png":
            data = (ROOT / "static/assets/rove-mark.png").read_bytes()
            self.send_response(200); self.send_header("Content-Type", "image/png"); self.send_header("Cache-Control", "public, max-age=86400"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data); return
        if self.path == "/assets/rove-mark.svg":
            data = (ROOT / "static/assets/rove-mark.svg").read_bytes()
            self.send_response(200); self.send_header("Content-Type", "image/svg+xml"); self.send_header("Cache-Control", "no-store"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data); return
        self.send_json(404, {"error": "not found"})

    def do_POST(self):
        global _cancel_event
        if self.path == "/api/tasks/stop":
            with _lock:
                if _run["status"] not in {"starting", "running", "extracting"} or _cancel_event is None:
                    return self.send_json(409, {"error": "there is no active task to stop"})
                _run["status"] = "stopping"
                cancel_event = _cancel_event
            cancel_event.set()
            return self.send_json(202, snapshot())
        if self.path == "/api/theme":
            length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(length))
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
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length))
            goal = body.get("goal", "").strip()
            if not goal or len(goal) > 2000:
                raise ValueError("url must be http(s) and goal must be 1-2000 characters")
            with _lock:
                if _run["status"] in {"starting", "running", "extracting"}:
                    return self.send_json(409, {"error": "a task is already running"})
            if self.path == "/api/tasks/continue":
                with _agent_lock:
                    if _agent is None:
                        return self.send_json(409, {"error": "there is no resumable session to continue"})
                with _lock:
                    _cancel_event = threading.Event()
                    cancel_event = _cancel_event
                    if _run["status"] not in {"done", "answered", "blocked", "stopped", "error"} or not _run["can_continue"]:
                        return self.send_json(409, {"error": "the current session is not resumable"})
                    _run.update(
                        status="starting",
                        goal=goal,
                        error=None,
                        result=None,
                        can_continue=True,
                        started_at=time.time(),
                    )
                threading.Thread(target=execute, args=("", goal, True, cancel_event), daemon=True).start()
                return self.send_json(202, snapshot())

            url = body.get("url", "").strip()
            if not url.startswith(("http://", "https://")):
                raise ValueError("url must be http(s) and goal must be 1-2000 characters")
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
        except (ValueError, json.JSONDecodeError) as exc:
            return self.send_json(400, {"error": str(exc)})

    def log_message(self, *_args):
        pass


if __name__ == "__main__":
    set_desktop_theme("dark")
    print(f"Rove listening on http://0.0.0.0:{PORT}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
