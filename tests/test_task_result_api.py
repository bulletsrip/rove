from __future__ import annotations

import sys
import threading
import types
import unittest
from unittest.mock import patch


browser_harness = types.ModuleType("browser_harness")
admin = types.ModuleType("browser_harness.admin")
helpers = types.ModuleType("browser_harness.helpers")
admin.ensure_daemon = lambda: None
helpers.cdp = lambda *args, **kwargs: None
sys.modules["browser_harness"] = browser_harness
sys.modules["browser_harness.admin"] = admin
sys.modules["browser_harness.helpers"] = helpers
httpx = types.ModuleType("httpx")
httpx.Client = lambda *args, **kwargs: None
httpx.HTTPError = RuntimeError
sys.modules["httpx"] = httpx

import app.server as server


class FakeAgent:
    def __init__(self, url, goal, screenshots=False):
        self.continuations = []
        self.continuation_contexts = []
        self.state = {
            "status": "done",
            "goal": goal,
            "history": [],
            "page": {
                "url": url,
                "title": "Maps",
                "text": "Tanjung Duren to Tebet 12.4 km 32 minutes",
            },
        }

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def run(self):
        yield {"status": "done", "history": []}

    def continue_with(self, goal, context=None):
        self.continuations.append(goal)
        self.continuation_contexts.append(context)
        self.state["goal"] = goal

    def close(self):
        pass


class StoppedAgent(FakeAgent):
    def run(self):
        self.state["status"] = "stopped"
        yield {"status": "running", "history": []}


class NavigatingAgent(FakeAgent):
    def run(self):
        self.state["page"]["url"] = "https://www.youtube.com/watch?v=example"
        yield {"status": "done", "history": []}


class TaskResultApiTest(unittest.TestCase):
    def setUp(self):
        with server._lock:
            server._run.update(
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
        with server._agent_lock:
            server._agent = None
        server._cancel_event = None

    def test_completed_task_exposes_answer_result(self):
        result = {
            "kind": "answer",
            "summary": "The driving distance is 12.4 km.",
            "facts": {"distance": "12.4 km"},
            "source_url": "https://maps.google.com/",
        }
        with patch.object(server, "Agent", FakeAgent), patch.object(server, "extract_result", return_value=result):
            server.execute("https://maps.google.com/", "Search distance of Tanjung Duren to Tebet")

        state = server.snapshot()
        self.assertEqual(state["status"], "answered")
        self.assertEqual(state["result"], result)
        self.assertEqual(state["result_history"], [{"index": 1, "goal": "Search distance of Tanjung Duren to Tebet", "result": result}])

    def test_action_only_task_finishes_without_result_extraction(self):
        with patch.object(server, "Agent", FakeAgent), patch.object(
            server, "extract_result", side_effect=AssertionError("action-only tasks should not extract")
        ):
            server.execute("https://www.tiktok.com/", "Scroll down")

        state = server.snapshot()
        self.assertEqual(state["status"], "done")
        self.assertEqual(state["result"]["kind"], "completion")
        self.assertEqual(state["result_history"][0]["goal"], "Scroll down")

    def test_status_tracks_the_current_page_url_after_navigation(self):
        result = {
            "kind": "completion",
            "summary": "The page is open.",
            "facts": {},
            "source_url": "https://www.youtube.com/watch?v=example",
        }
        with patch.object(server, "Agent", NavigatingAgent), patch.object(server, "extract_result", return_value=result):
            server.execute("https://google.com", "Open the YouTube video")

        self.assertEqual(server.snapshot()["url"], "https://www.youtube.com/watch?v=example")

    def test_follow_up_keeps_previous_result_and_adds_latest_result(self):
        existing = FakeAgent("https://maps.google.com/", "Read the map title")
        first_result = {
            "kind": "answer",
            "summary": "The map is open.",
            "facts": {"place": "Tanjung Duren"},
            "source_url": "https://maps.google.com/",
        }
        second_result = {
            "kind": "answer",
            "summary": "The distance is 12.4 km.",
            "facts": {"distance": "12.4 km"},
            "source_url": "https://maps.google.com/",
        }

        with patch.object(server, "Agent", return_value=existing), patch.object(server, "extract_result", return_value=first_result):
            server.execute("https://maps.google.com/", "Read the map title")
        with patch.object(server, "extract_result", return_value=second_result):
            server.execute("", "Find the distance", continuation=True)

        state = server.snapshot()
        self.assertEqual(state["result"], second_result)
        self.assertEqual([item["result"] for item in state["result_history"]], [first_result, second_result])

    def test_follow_up_reuses_completed_agent(self):
        result = {
            "kind": "answer",
            "summary": "The map is open.",
            "facts": {},
            "source_url": "https://maps.google.com/",
        }
        existing = FakeAgent("https://maps.google.com/", "Read the map title")
        with server._agent_lock:
            server._agent = existing
        with server._lock:
            server._run.update(
                status="answered",
                url="https://maps.google.com/",
                goal="Read the map title",
                steps=[],
                result={"kind": "answer"},
            )

        with patch.object(server, "extract_result", return_value=result):
            server.execute("", "Calculate the distance to Tebet", continuation=True)

        self.assertEqual(existing.continuations, ["Calculate the distance to Tebet"])
        self.assertEqual(existing.continuation_contexts, [{"previous_goal": "Read the map title", "previous_result": {"kind": "answer"}}])
        self.assertEqual(server.snapshot()["status"], "answered")
        self.assertEqual(server.snapshot()["goal"], "Calculate the distance to Tebet")

    def test_completed_session_is_marked_resumable_until_explicitly_reset(self):
        existing = FakeAgent("https://www.youtube.com/watch?v=example", "Open YouTube")
        with server._agent_lock:
            server._agent = existing
        with server._lock:
            server._run.update(status="answered", can_continue=True)

        self.assertTrue(server.has_resumable_session())

    def test_reset_session_clears_completed_session_without_starting_task(self):
        existing = FakeAgent("https://maps.google.com/", "Open maps")
        with server._agent_lock:
            server._agent = existing
        with server._lock:
            server._run.update(
                status="answered",
                url="https://maps.google.com/",
                goal="Open maps",
                steps=[{"step": 1, "action": "Open maps"}],
                result={"kind": "answer"},
                result_history=[{"index": 1, "goal": "Open maps", "result": {"kind": "answer"}}],
            )

        self.assertTrue(server.reset_session())
        state = server.snapshot()
        self.assertEqual(state["status"], "idle")
        self.assertIsNone(state["url"])
        self.assertIsNone(state["goal"])
        self.assertEqual(state["steps"], [])
        self.assertIsNone(state["result"])
        self.assertEqual(state["result_history"], [])
        with server._agent_lock:
            self.assertIsNone(server._agent)

    def test_cancelled_task_transitions_to_stopped_and_keeps_agent(self):
        cancel_event = threading.Event()
        cancel_event.set()
        agent = StoppedAgent("https://example.test", "Stop this task")
        server._cancel_event = cancel_event

        with patch.object(server, "Agent", return_value=agent):
            server.execute("https://example.test", "Stop this task", cancel_event=cancel_event)

        self.assertEqual(server.snapshot()["status"], "stopped")
        with server._agent_lock:
            self.assertIs(agent, server._agent)


if __name__ == "__main__":
    unittest.main()
