from __future__ import annotations

import sys
import time
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
from jev_ultrafast.browser import StalePage


class StaleAfterActionBrowser:
    def fresh(self, page, action=None):
        return True

    def act(self, action, page, text=None):
        return {"executed": action["id"]}

    def observe(self, screenshot=True):
        raise StalePage("navigation is still settling")


class AgentRepeatGuardTest(unittest.TestCase):
    def setUp(self):
        self.agent = Agent.__new__(Agent)
        self.agent.pending_text = None
        self.agent.stop_event = None
        self.agent.repeat_key = None
        self.agent.repeat_count = 0
        self.agent.screenshots = False
        self.agent.state = {
            "browser": StaleAfterActionBrowser(),
            "goal": "Search for a result",
            "page": {
                "url": "https://example.test",
                "title": "Example",
                "text": "",
                "fingerprint": "same-page",
                "actions": [
                    {
                        "id": "search",
                        "kind": "click",
                        "label": "Search",
                        "node": 1,
                        "role": "button",
                    }
                ],
            },
            "decision": None,
            "history": [],
            "status": "ready",
            "started_at": time.perf_counter(),
            "run_history_start": 0,
            "record": False,
        }

    def choose_search(self):
        self.agent.state["decision"] = {
            "choice": "search",
            "operation": "CLICK",
            "target": "1",
            "probabilities": {"search": 1.0},
            "confidence": 1.0,
            "latency_ms": 0,
            "usage": {},
        }

    def test_three_identical_actions_block_when_observe_is_stale(self):
        for _ in range(2):
            self.choose_search()
            with self.assertRaises(StalePage):
                self.agent.command("act", {"fingerprint": "same-page"})

        self.choose_search()
        snapshot = self.agent.command("act", {"fingerprint": "same-page"})

        self.assertEqual(snapshot["status"], "blocked")
        self.assertEqual(self.agent.repeat_count, 3)
        self.assertEqual(len(snapshot["history"]), 3)
        self.assertEqual([entry["action"] for entry in snapshot["history"]], ["Search"] * 3)

    def test_standalone_primitive_finishes_after_one_execution(self):
        page = {
            "url": "https://example.test",
            "title": "Example",
            "text": "",
            "fingerprint": "same-page",
            "actions": [{"id": "scroll_down", "kind": "scroll", "label": "Scroll down", "delta": 560}],
        }

        class Browser:
            def fresh(self, current_page, action=None):
                return True

            def act(self, action, current_page, text=None):
                return {"executed": action["id"]}

            def observe(self, screenshot=True):
                return page

        agent = Agent.__new__(Agent)
        agent.pending_text = None
        agent.stop_event = None
        agent.repeat_key = None
        agent.repeat_count = 0
        agent.screenshots = False
        agent.state = {
            "browser": Browser(),
            "goal": "Scroll down",
            "page": page,
            "decision": {
                "choice": "scroll_down",
                "operation": "SCROLL_DOWN",
                "target": None,
                "probabilities": {"scroll_down": 1.0},
                "confidence": 1.0,
                "latency_ms": 0,
                "usage": {},
            },
            "history": [],
            "status": "ready",
            "started_at": time.perf_counter(),
            "run_history_start": 0,
            "record": False,
        }

        snapshot = agent.command("act", {"fingerprint": "same-page"})

        self.assertEqual(snapshot["status"], "done")
        self.assertEqual(len(snapshot["history"]), 1)

if __name__ == "__main__":
    unittest.main()
