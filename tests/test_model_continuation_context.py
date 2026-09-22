from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


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

from jev_ultrafast import model


class ModelContinuationContextTest(unittest.TestCase):
    def test_choose_sends_compact_continuation_context(self):
        captured = {}

        def request_json(_url, _key, body):
            captured["body"] = body
            return {
                "model": "test-model",
                "answers": {
                    "operation": {
                        "choice": "CLICK",
                        "probabilities": {"CLICK": 1.0, "WAIT": 0.0, "DONE": 0.0, "BLOCKED": 0.0},
                        "confidence": 1.0,
                    },
                    "click_target": {"choice": "1", "probabilities": {"1": 1.0}, "confidence": 1.0},
                },
            }

        page = {
            "url": "https://maps.google.com",
            "title": "Google Maps",
            "text": "The route result is visible.",
            "actions": [
                {"id": "e1", "kind": "click", "label": "Directions", "node": 1, "role": "button"},
                {"id": "wait", "kind": "wait", "label": "Wait for the page to update"},
            ],
        }
        context = {
            "previous_goal": "Find the route",
            "previous_result": {"kind": "answer", "summary": "The route is 12.4 km."},
        }

        with patch.object(model, "post_json", side_effect=request_json), patch.dict("os.environ", {"TYPESAFE_API_KEY": "test-key"}):
            model.choose(page, "Use that result to compare another route", [], context)

        self.assertEqual(captured["body"]["state"]["continuation_context"], context)

    def test_current_request_actions_exclude_previous_session_actions(self):
        captured = {}

        def request_json(_url, _key, body):
            captured["body"] = body
            return {
                "model": "test-model",
                "answers": {
                    "operation": {
                        "choice": "SCROLL_DOWN",
                        "probabilities": {"SCROLL_DOWN": 1.0, "WAIT": 0.0, "DONE": 0.0, "BLOCKED": 0.0},
                        "confidence": 1.0,
                    },
                },
            }

        page = {
            "url": "https://www.tiktok.com",
            "title": "TikTok",
            "text": "For You",
            "actions": [
                {"id": "scroll_down", "kind": "scroll", "label": "Scroll down", "delta": 560},
                {"id": "wait", "kind": "wait", "label": "Wait for the page to update"},
            ],
        }
        history = [{"action": "Scroll down", "kind": "scroll", "text": None, "page_changed": True}]

        with patch.object(model, "post_json", side_effect=request_json), patch.dict("os.environ", {"TYPESAFE_API_KEY": "test-key"}):
            decision = model.choose(page, "Scroll down again", history, {"previous_goal": "Scroll down"}, 1)

        self.assertEqual(decision["choice"], "scroll_down")
        self.assertEqual(captured["body"]["state"]["current_request_actions"], [])


if __name__ == "__main__":
    unittest.main()
