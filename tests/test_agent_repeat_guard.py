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


class StableAfterActionBrowser:
    def __init__(self):
        self.observations = 0

    def fresh(self, page, action=None):
        return True

    def act(self, action, page, text=None):
        return {"executed": action["id"]}

    def observe(self, screenshot=True):
        self.observations += 1
        fingerprint = f"page-{self.observations}"
        return {
            "url": "https://example.test",
            "title": "Example",
            "text": f"visible-state-{fingerprint}",
            "fingerprint": fingerprint,
            "page_key": fingerprint,
            "marker": fingerprint,
            "guards": {"1": "same-node"},
            "actions": [
                {
                    "id": "search",
                    "kind": "click",
                    "label": "Search",
                    "node": 1,
                    "role": "button",
                }
            ],
        }


class NoProgressBrowser(StableAfterActionBrowser):
    def observe(self, screenshot=True):
        page = super().observe(screenshot=screenshot)
        page["text"] = "same-state"
        page["fingerprint"] = "same-page"
        page["page_key"] = "same-page"
        page["marker"] = "same-page"
        return page


class DynamicFingerprintNoProgressBrowser(StableAfterActionBrowser):
    def observe(self, screenshot=True):
        page = super().observe(screenshot=screenshot)
        page["text"] = "same-state"
        page["fingerprint"] = f"transient-{self.observations}"
        page["page_key"] = f"transient-{self.observations}"
        page["marker"] = f"transient-{self.observations}"
        return page


class TwoStateCycleBrowser:
    def __init__(self):
        self.mode = "list"

    def fresh(self, page, action=None):
        return True

    def act(self, action, page, text=None):
        self.mode = "detail" if action["id"] == "open_book" else "list"
        return {"executed": action["id"]}

    def observe(self, screenshot=True):
        if self.mode == "list":
            return {
                "url": "https://example.test/catalog",
                "title": "Catalog",
                "text": "Book listing",
                "fingerprint": "catalog",
                "actions": [{"id": "open_book", "kind": "click", "label": "Book A", "node": 1, "role": "link"}],
            }
        return {
            "url": "https://example.test/book-a",
            "title": "Book A",
            "text": "Book detail",
            "fingerprint": "book-a",
            "actions": [{"id": "back", "kind": "back", "label": "Go back"}],
        }


class AgentRepetitionTest(unittest.TestCase):
    def setUp(self):
        self.agent = Agent.__new__(Agent)
        self.agent.pending_text = None
        self.agent.stop_event = None
        self.agent.no_progress_key = None
        self.agent.no_progress_count = 0
        self.agent.screenshots = False
        self.agent.state = {
            "browser": StableAfterActionBrowser(),
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

    def test_repeated_identical_actions_do_not_block_a_compound_goal(self):
        for _ in range(3):
            self.choose_search()
            snapshot = self.agent.command("act", {"fingerprint": self.agent.state["page"]["fingerprint"]})
            self.assertEqual(snapshot["status"], "ready")

        self.assertEqual(len(snapshot["history"]), 3)
        self.assertEqual([entry["action"] for entry in snapshot["history"]], ["Search"] * 3)

    def test_same_action_without_progress_is_stopped(self):
        self.agent.state["browser"] = NoProgressBrowser()
        for _ in range(2):
            self.choose_search()
            snapshot = self.agent.command("act", {"fingerprint": self.agent.state["page"]["fingerprint"]})

        self.assertEqual(snapshot["status"], "blocked")
        self.assertEqual(len(snapshot["history"]), 2)

    def test_dynamic_fingerprint_cannot_hide_repeated_action_without_progress(self):
        self.agent.state["browser"] = DynamicFingerprintNoProgressBrowser()
        for _ in range(2):
            self.choose_search()
            snapshot = self.agent.command("act", {"fingerprint": self.agent.state["page"]["fingerprint"]})

        self.assertEqual(snapshot["status"], "blocked")
        self.assertEqual(len(snapshot["history"]), 2)

    def test_repeating_two_state_cycle_is_stopped(self):
        browser = TwoStateCycleBrowser()
        self.agent.state["browser"] = browser
        self.agent.state["page"] = browser.observe()
        for _ in range(4):
            action = self.agent.state["page"]["actions"][0]
            self.agent.state["decision"] = {
                "choice": action["id"],
                "operation": "CLICK" if action["kind"] == "click" else "BACK",
                "target": "1" if action["kind"] == "click" else None,
                "probabilities": {action["id"]: 1.0},
                "confidence": 1.0,
                "latency_ms": 0,
                "usage": {},
            }
            snapshot = self.agent.command("act", {"fingerprint": self.agent.state["page"]["fingerprint"]})
            if snapshot["status"] == "blocked":
                break

        self.assertEqual(snapshot["status"], "blocked")
        self.assertLessEqual(len(snapshot["history"]), 3)
        self.assertIn("cycle", snapshot["block_reason"])

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
        agent.no_progress_key = None
        agent.no_progress_count = 0
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
