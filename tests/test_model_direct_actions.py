from __future__ import annotations

import sys
import types
import unittest
import os
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "vendor"))

httpx = types.ModuleType("httpx")
httpx.Client = lambda *args, **kwargs: None
httpx.HTTPError = RuntimeError
sys.modules["httpx"] = httpx
browser_harness = types.ModuleType("browser_harness")
admin = types.ModuleType("browser_harness.admin")
helpers = types.ModuleType("browser_harness.helpers")
admin.ensure_daemon = lambda: None
helpers.cdp = lambda *args, **kwargs: None
sys.modules["browser_harness"] = browser_harness
sys.modules["browser_harness.admin"] = admin
sys.modules["browser_harness.helpers"] = helpers

from jev_ultrafast import model


class PrimitiveActionTest(unittest.TestCase):
    def test_collection_action_space_hides_item_links_when_pagination_is_available(self):
        _, targets, controls = model.action_space(
            [
                {"id": "book_1", "kind": "click", "label": "Book A", "node": 1, "role": "link", "href": "https://example.test/book-a"},
                {"id": "book_2", "kind": "click", "label": "Book B", "node": 2, "role": "link", "href": "https://example.test/book-b"},
                {"id": "book_3", "kind": "click", "label": "Book C", "node": 3, "role": "link", "href": "https://example.test/book-c"},
                {"id": "basket", "kind": "click", "label": "Add to basket", "node": 5, "role": "button"},
                {"id": "next", "kind": "click", "label": "next", "node": 4, "role": "link", "href": "https://example.test/catalogue/page-2.html"},
            ],
            collection_mode=True,
        )

        self.assertEqual(set(targets["CLICK"]), {"1"})
        self.assertEqual(controls, {})

    def test_collection_action_space_does_not_treat_tag_page_as_pagination(self):
        _, targets, _ = model.action_space(
            [
                {"id": "tag", "kind": "click", "label": "change", "node": 1, "role": "link", "href": "https://example.test/tag/change/page/1/"},
                {"id": "author", "kind": "click", "label": "(about)", "node": 2, "role": "link", "href": "https://example.test/author/example"},
                {"id": "tag_2", "kind": "click", "label": "thinking", "node": 3, "role": "link", "href": "https://example.test/tag/thinking/"},
                {"id": "next", "kind": "click", "label": "next", "node": 4, "role": "link", "href": "https://example.test/page/2/"},
            ],
            collection_mode=True,
        )

        self.assertEqual(set(targets["CLICK"]), {"1"})

    def test_action_space_exposes_targetless_browser_primitives(self):
        _, _, controls = model.action_space(
            [
                {"id": "scroll_up", "kind": "scroll", "label": "Scroll up", "delta": -560},
                {"id": "scroll_down", "kind": "scroll", "label": "Scroll down", "delta": 560},
                {"id": "back", "kind": "back", "label": "Go back"},
                {"id": "reload", "kind": "reload", "label": "Reload the page"},
                {"id": "key_enter", "kind": "key", "label": "Press Enter", "key": "Enter"},
                {"id": "wait", "kind": "wait", "label": "Wait for the page to update"},
            ]
        )

        self.assertEqual(
            set(controls),
            {"SCROLL_UP", "SCROLL_DOWN", "BACK", "RELOAD", "KEY_ENTER", "WAIT"},
        )

    def test_model_selects_scroll_control_from_primitive_choices(self):
        state = {
            "url": "https://www.example.test/",
            "title": "Example",
            "text": "At the bottom",
            "actions": [
                {"id": "scroll_down", "kind": "scroll", "label": "Scroll down", "delta": 560},
                {"id": "wait", "kind": "wait", "label": "Wait for the page to update"},
            ],
        }
        answer = {
            "choice": "SCROLL_DOWN",
            "probabilities": {"SCROLL_DOWN": 1.0, "WAIT": 0.0, "DONE": 0.0, "BLOCKED": 0.0},
            "confidence": 1.0,
        }
        with patch.dict(os.environ, {"TYPESAFE_API_KEY": "test-key"}), patch.object(
            model, "post_json", return_value={"answers": {"operation": answer}, "model": "test"}
        ):
            decision = model.choose(state, "Scroll down", [])

        self.assertEqual(decision["choice"], "scroll_down")
        self.assertEqual(decision["operation"], "SCROLL_DOWN")

    def test_collection_pass_cannot_finish_before_first_action(self):
        captured = {}

        def request_json(_url, _key, body):
            captured["body"] = body
            return {
                "model": "test",
                "answers": {
                    "operation": {
                        "choice": "SCROLL_DOWN",
                        "probabilities": {"SCROLL_DOWN": 1.0, "BLOCKED": 0.0},
                        "confidence": 1.0,
                    }
                }
            }

        state = {
            "url": "https://books.toscrape.com/",
            "title": "Books",
            "text": "Book listing",
            "actions": [{"id": "scroll_down", "kind": "scroll", "label": "Scroll down", "delta": 560}],
        }
        with patch.dict(os.environ, {"TYPESAFE_API_KEY": "test-key"}), patch.object(
            model, "post_json", side_effect=request_json
        ):
            decision = model.choose(state, "Collect books", [], collection_mode=True)

        self.assertEqual(decision["choice"], "scroll_down")
        self.assertNotIn("DONE", captured["body"]["questions"]["operation"]["criteria"])

if __name__ == "__main__":
    unittest.main()
