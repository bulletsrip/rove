from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path


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

from jev_ultrafast.browser import choose_page_target


class BrowserTargetSelectionTest(unittest.TestCase):
    def test_prefers_top_level_page_over_popup_opener(self):
        target = choose_page_target(
            [
                {"type": "page", "url": "https://www.tiktok.com/", "openerId": "main"},
                {"type": "page", "url": "https://www.tiktok.com/", "title": "TikTok"},
            ]
        )

        self.assertEqual(target["title"], "TikTok")
        self.assertNotIn("openerId", target)


if __name__ == "__main__":
    unittest.main()
