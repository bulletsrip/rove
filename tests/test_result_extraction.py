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


if __name__ == "__main__":
    unittest.main()
