from __future__ import annotations

import unittest

from app.long_tasks import batch_extraction_goal, continuation_instruction, merge_items


class LongTaskTest(unittest.TestCase):
    def test_merges_unique_items_without_losing_order(self):
        items, added = merge_items(["First photo"], {"items": ["first PHOTO", "Second photo"]})
        self.assertEqual(items, ["First photo", "Second photo"])
        self.assertEqual(added, 1)

    def test_continuation_instruction_carries_collection_context(self):
        instruction = continuation_instruction("Collect all comments", 3, ["Comment A"])
        self.assertIn("collection pass 3", instruction)
        self.assertIn("Comment A", instruction)
        self.assertIn("next unseen batch", instruction)

    def test_batch_extraction_does_not_require_other_pages(self):
        goal = batch_extraction_goal("Collect books across every page")
        self.assertIn("current catalog/listing page", goal)
        self.assertIn("do not return needs_review", goal)


if __name__ == "__main__":
    unittest.main()
