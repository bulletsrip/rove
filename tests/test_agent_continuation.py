from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "vendor"))

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

from jev_ultrafast.agent import Agent


class AgentContinuationTest(unittest.TestCase):
    def test_follow_up_resets_run_state_but_preserves_page_and_history(self):
        agent = Agent.__new__(Agent)
        agent.pending_text = ("old context", "old value", {"model": "helper"})
        agent.state = {
            "browser": object(),
            "goal": "Open the map",
            "goal_history": ["Open the map"],
            "page": {"url": "https://maps.google.com", "fingerprint": "page-2", "actions": []},
            "decision": {"choice": "CLICK"},
            "history": [{"step": 1, "action": "Open maps"}],
            "status": "done",
            "plan": ["Open the map"],
            "plan_index": 1,
            "decisions": [{"choice": "DONE"}],
            "text_calls": [{"field": "search", "value": "maps"}],
            "elapsed_ms": 1200,
            "started_at": 12.0,
            "run_history_start": 0,
            "record": False,
        }

        agent.continue_with("Now search for Tebet")

        self.assertEqual(agent.state["status"], "ready")
        self.assertEqual(agent.state["goal"], "Now search for Tebet")
        self.assertEqual(agent.state["goal_history"], ["Open the map", "Now search for Tebet"])
        self.assertEqual(agent.state["history"], [{"step": 1, "action": "Open maps"}])
        self.assertEqual(agent.state["run_history_start"], 1)
        self.assertEqual(agent.state["decisions"], [])
        self.assertEqual(agent.state["text_calls"], [])
        self.assertIsNone(agent.state["decision"])
        self.assertIsNone(agent.state["started_at"])
        self.assertIsNone(agent.pending_text)

    def test_stopped_run_can_continue_without_starting_fresh(self):
        agent = Agent.__new__(Agent)
        agent.pending_text = None
        agent.state = {
            "browser": object(),
            "goal": "Open the map",
            "goal_history": ["Open the map"],
            "page": {"url": "https://maps.google.com", "fingerprint": "page-2", "actions": []},
            "decision": None,
            "history": [{"step": 1, "action": "Open maps"}],
            "status": "stopped",
            "plan": ["Open the map"],
            "plan_index": 0,
            "decisions": [],
            "text_calls": [],
            "elapsed_ms": 1200,
            "started_at": 12.0,
            "run_history_start": 0,
            "record": False,
        }

        agent.continue_with("Now search for Tebet")

        self.assertEqual(agent.state["status"], "ready")
        self.assertEqual(agent.state["goal_history"], ["Open the map", "Now search for Tebet"])
        self.assertEqual(agent.state["history"], [{"step": 1, "action": "Open maps"}])

    def test_follow_up_keeps_previous_goal_and_result_as_context(self):
        agent = Agent.__new__(Agent)
        agent.pending_text = None
        agent.state = {
            "browser": object(),
            "goal": "Open maps",
            "goal_history": ["Open maps"],
            "page": {"url": "https://maps.google.com", "fingerprint": "page-2", "actions": []},
            "decision": None,
            "history": [],
            "status": "answered",
            "plan": ["Open maps"],
            "plan_index": 1,
            "decisions": [],
            "text_calls": [],
            "elapsed_ms": 0,
            "started_at": None,
            "run_history_start": 0,
            "record": False,
        }

        agent.continue_with(
            "Use that result to compare another route",
            context={
                "previous_goal": "Open maps",
                "previous_result": {"kind": "answer", "summary": "The route is 12.4 km."},
            },
        )

        self.assertEqual(
            agent.state["continuation_context"],
            {
                "previous_goal": "Open maps",
                "previous_result": {"kind": "answer", "summary": "The route is 12.4 km."},
            },
        )


if __name__ == "__main__":
    unittest.main()
