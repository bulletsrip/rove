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
    def __init__(self, url, goal, screenshots=False, collection=False):
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
                long_task=False,
                iteration=0,
                collected_items=[],
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

    def test_compound_plan_runs_each_objective_and_aggregates_results(self):
        existing = FakeAgent("https://maps.google.com/", "Read Setiabudi")
        objectives = [
            {"id": "objective_1", "instruction": "Read Setiabudi"},
            {"id": "objective_2", "instruction": "Read Benhil"},
        ]
        results = [
            {
                "kind": "answer",
                "summary": "Setiabudi is 10 km away.",
                "facts": {"distance": "10 km"},
                "source_url": "https://maps.google.com/",
            },
            {
                "kind": "answer",
                "summary": "Benhil is 8 km away.",
                "facts": {"distance": "8 km"},
                "source_url": "https://maps.google.com/",
            },
        ]
        with patch.object(server, "Agent", return_value=existing), patch.object(
            server, "plan_goal", return_value=objectives
        ), patch.object(server, "extract_result", side_effect=results):
            server.execute("https://maps.google.com/", "Compare two destinations")

        state = server.snapshot()
        self.assertEqual(state["status"], "answered")
        self.assertEqual(len(state["result_history"]), 2)
        self.assertEqual(existing.continuations, ["Read Benhil"])
        self.assertIn("10 km", state["result"]["facts"]["1. Read Setiabudi — distance"])
        self.assertIn("8 km", state["result"]["facts"]["2. Read Benhil — distance"])

    def test_long_collection_runs_bounded_passes_and_deduplicates_items(self):
        existing = FakeAgent("https://example.test/gallery", "Scrape every photo")
        results = [
            {"kind": "answer", "summary": "First batch", "facts": {}, "items": ["Photo A"], "source_url": "https://example.test/gallery"},
            {"kind": "answer", "summary": "Second batch", "facts": {}, "items": ["Photo B"], "source_url": "https://example.test/gallery"},
            {"kind": "answer", "summary": "Same batch", "facts": {}, "items": ["Photo B"], "source_url": "https://example.test/gallery"},
            {"kind": "answer", "summary": "Same batch", "facts": {}, "items": ["Photo B"], "source_url": "https://example.test/gallery"},
        ]
        with patch.object(server, "Agent", return_value=existing), patch.object(
            server,
            "plan_goal",
            return_value={"mode": "collection", "objectives": [{"id": "objective_1", "instruction": "Scrape every photo"}]},
        ), patch.object(server, "extract_result", side_effect=results):
            server.execute("https://example.test/gallery", "Scrape every photo")

        state = server.snapshot()
        self.assertEqual(state["status"], "answered")
        self.assertTrue(state["long_task"])
        self.assertEqual(state["collected_items"], ["Photo A", "Photo B"])
        self.assertEqual(state["result"]["items"], ["Photo A", "Photo B"])
        self.assertEqual(len(state["result_history"]), 4)
        self.assertEqual(len(existing.continuations), 3)

    def test_long_collection_continuation_preserves_prior_items(self):
        existing = FakeAgent("https://example.test/gallery", "Scrape every photo")
        previous = {
            "kind": "answer",
            "summary": "One prior item",
            "facts": {},
            "items": ["Photo A"],
            "source_url": "https://example.test/gallery",
        }
        results = [
            {"kind": "answer", "summary": "New batch", "facts": {}, "items": ["Photo B"], "source_url": "https://example.test/gallery"},
            {"kind": "answer", "summary": "Same batch", "facts": {}, "items": ["Photo B"], "source_url": "https://example.test/gallery"},
            {"kind": "answer", "summary": "Same batch", "facts": {}, "items": ["Photo B"], "source_url": "https://example.test/gallery"},
        ]
        with server._agent_lock:
            server._agent = existing
        with server._lock:
            server._run.update(
                status="answered",
                goal="Scrape every photo",
                result=previous,
                result_history=[{"index": 1, "goal": "Scrape every photo", "result": previous}],
                can_continue=True,
            )
        with patch.object(
            server,
            "plan_goal",
            return_value={"mode": "collection", "objectives": [{"id": "objective_1", "instruction": "Scrape every photo"}]},
        ), patch.object(server, "extract_result", side_effect=results):
            server.execute(
                "",
                "Scrape every photo",
                continuation=True,
                continuation_context={"previous_goal": "Scrape every photo", "previous_result": previous},
            )

        state = server.snapshot()
        self.assertEqual(state["status"], "answered")
        self.assertEqual(state["result"]["items"], ["Photo A", "Photo B"])
        self.assertEqual(state["collected_items"], ["Photo A", "Photo B"])

    def test_completed_session_is_marked_resumable_until_explicitly_reset(self):
        existing = FakeAgent("https://www.youtube.com/watch?v=example", "Open YouTube")
        with server._agent_lock:
            server._agent = existing
        with server._lock:
            server._run.update(status="answered", can_continue=True)

        self.assertTrue(server.has_resumable_session())

    def test_normal_task_endpoint_starts_fresh_even_with_resumable_session(self):
        self.assertFalse(server.is_continuation_request("/api/tasks"))
        self.assertTrue(server.is_continuation_request("/api/tasks/continue"))

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
