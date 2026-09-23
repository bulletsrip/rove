from __future__ import annotations

import json
import unittest

from app.results import extract_result


class ResultExtractionTest(unittest.TestCase):
    def test_extracts_answer_facts_from_final_page(self):
        def request_json(url, key, body):
            self.assertEqual(url, "https://api.example.test/chat/completions")
            self.assertEqual(key, "test-key")
            self.assertIn("Tanjung Duren", body["messages"][1]["content"])
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "kind": "answer",
                                    "summary": "The driving distance is 12.4 km and takes about 32 minutes.",
                                    "facts": {
                                        "distance": "12.4 km",
                                        "duration": "32 minutes",
                                        "mode": "driving",
                                    },
                                    "source_url": "https://maps.google.com/",
                                }
                            )
                        }
                    }
                ]
            }

        result = extract_result(
            "Search distance of Tanjung Duren to Tebet",
            {
                "url": "https://maps.google.com/",
                "title": "Google Maps",
                "text": "Tanjung Duren to Tebet 12.4 km 32 min",
            },
            [],
            request_json=request_json,
            api_key="test-key",
            base_url="https://api.example.test",
        )

        self.assertEqual(result["kind"], "answer")
        self.assertEqual(result["facts"]["distance"], "12.4 km")
        self.assertEqual(result["source_url"], "https://maps.google.com/")

    def test_scrape_result_preserves_each_visible_item(self):
        def request_json(url, key, body):
            prompt = body["messages"][0]["content"].lower()
            self.assertIn("outcome", prompt)
            self.assertIn("evidence", prompt)
            self.assertIn("items", prompt)
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "kind": "answer",
                                    "summary": "Found two visible comments.",
                                    "facts": {},
                                    "items": ["First comment", "Second comment"],
                                    "source_url": "https://www.youtube.com/watch?v=example",
                                }
                            )
                        }
                    }
                ]
            }

        result = extract_result(
            "Scrape all the comments from this video",
            {
                "url": "https://www.youtube.com/watch?v=example",
                "title": "Example video",
                "text": "First comment\nSecond comment",
            },
            [],
            request_json=request_json,
            api_key="test-key",
            base_url="https://api.example.test",
        )

        self.assertEqual(result["kind"], "answer")
        self.assertEqual(result["items"], ["First comment", "Second comment"])

    def test_information_request_cannot_be_reported_as_empty_completion(self):
        def request_json(url, key, body):
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "kind": "completion",
                                    "summary": "Scraped the comments.",
                                    "facts": {},
                                    "source_url": "https://www.youtube.com/watch?v=example",
                                }
                            )
                        }
                    }
                ]
            }

        result = extract_result(
            "Scrape all the comments from this video",
            {"url": "https://www.youtube.com/watch?v=example", "title": "Example", "text": ""},
            [],
            request_json=request_json,
            api_key="test-key",
            base_url="https://api.example.test",
        )

        self.assertEqual(result["kind"], "needs_review")

    def test_action_completion_does_not_require_a_matching_previous_action(self):
        def request_json(url, key, body):
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "kind": "completion",
                                    "summary": "The page was scrolled up.",
                                    "facts": {},
                                    "source_url": "https://www.tiktok.com/",
                                }
                            )
                        }
                    }
                ]
            }

        result = extract_result(
            "Scroll up",
            {"url": "https://www.tiktok.com/", "title": "TikTok", "text": "For You"},
            [{"action": "Collapse sidebar"}],
            request_json=request_json,
            api_key="test-key",
            base_url="https://api.example.test",
        )

        self.assertEqual(result["kind"], "completion")

    def test_result_source_must_be_an_http_url(self):
        def request_json(url, key, body):
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "kind": "answer",
                                    "summary": "The answer is visible.",
                                    "facts": {"value": "42"},
                                    "source_url": "javascript:alert(1)",
                                }
                            )
                        }
                    }
                ]
            }

        result = extract_result(
            "Read the value",
            {"url": "https://example.test/page", "title": "Example", "text": "Value 42"},
            [],
            request_json=request_json,
            api_key="test-key",
            base_url="https://api.example.test",
        )

        self.assertEqual(result["source_url"], "https://example.test/page")

if __name__ == "__main__":
    unittest.main()
