from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from app.planner import plan_goal


class PlannerTest(unittest.TestCase):
    def test_simple_goal_avoids_an_extra_model_call(self):
        with patch.dict(os.environ, {"TEXT_MODEL_API_KEY": ""}), patch(
            "app.planner.json.loads", side_effect=AssertionError("planner should not be called")
        ):
            plan = plan_goal("Open the page and read its title.")

        self.assertEqual(
            plan,
            {"mode": "standard", "objectives": [{"id": "objective_1", "instruction": "Open the page and read its title."}]},
        )

    def test_compound_goal_returns_ordered_objectives(self):
        response = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "mode": "standard",
                                "objectives": [
                                    {"id": "first", "instruction": "Read item one."},
                                    {"id": "second", "instruction": "Read item two."},
                                ]
                            }
                        )
                    }
                }
            ]
        }
        calls = []

        def request_json(url, key, body):
            calls.append((url, key, body))
            return response

        with patch.dict("os.environ", {"TEXT_MODEL_API_KEY": "test-key"}):
            plan = plan_goal("Read item one.\nRead item two.", request_json=request_json)

        self.assertEqual(
            plan,
            {
                "mode": "standard",
                "objectives": [
                    {"id": "objective_1", "instruction": "Read item one."},
                    {"id": "objective_2", "instruction": "Read item two."},
                ],
            },
        )
        self.assertEqual(len(calls), 1)

    def test_planner_can_select_collection_mode(self):
        response = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "mode": "collection",
                                "objectives": [{"id": "collection", "instruction": "Collect the visible items and continue until the page ends."}],
                            }
                        )
                    }
                }
            ]
        }
        with patch.dict("os.environ", {"TEXT_MODEL_API_KEY": "test-key"}):
            plan = plan_goal("Collect all items across the page", request_json=lambda *_args: response)

        self.assertEqual(plan["mode"], "collection")
        self.assertEqual(len(plan["objectives"]), 1)

    def test_planner_preserves_generic_collection_target_count(self):
        response = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "mode": "collection",
                                "objectives": [{"id": "one", "instruction": "Collect the records.", "target_count": 10}],
                            }
                        )
                    }
                }
            ]
        }
        with patch.dict("os.environ", {"TEXT_MODEL_API_KEY": "test-key"}):
            plan = plan_goal(
                "Collect a finite set of records",
                request_json=lambda *_args: response,
            )

        self.assertEqual(plan["mode"], "collection")
        self.assertEqual(plan["objectives"][0]["target_count"], 10)

    def test_invalid_planner_response_falls_back_to_original_goal(self):
        with patch.dict("os.environ", {"TEXT_MODEL_API_KEY": "test-key"}):
            plan = plan_goal(
                "Read item one.\nRead item two.",
                request_json=lambda *_args: {"choices": [{"message": {"content": "not json"}}]},
            )

        self.assertEqual(
            plan,
            {"mode": "standard", "objectives": [{"id": "objective_1", "instruction": "Read item one.\nRead item two."}]},
        )


if __name__ == "__main__":
    unittest.main()
