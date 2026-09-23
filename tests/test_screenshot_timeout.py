from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "vendor"))

# Keep this unit test independent from the browser-harness installation.
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

from jev_ultrafast import browser


class ScreenshotTimeoutTest(unittest.TestCase):
    def test_capture_screenshot_timeout_keeps_page_observation_usable(self):
        page = {
            "url": "https://example.test",
            "title": "Example",
            "text": "Example page",
            "actions": [],
            "scroll": 0,
            "marker": "page-marker",
        }

        def fake_cdp(method, **_kwargs):
            if method == "Runtime.evaluate":
                return {"result": {"value": page}}
            if method == "Page.captureScreenshot":
                raise RuntimeError("_IPCResponseTimeout: Page.captureScreenshot timed out after 5s waiting for the daemon")
            raise AssertionError(f"unexpected CDP method: {method}")

        with patch.object(browser, "cdp", side_effect=fake_cdp):
            observed = browser.browser_operation({"operation": "observe", "session": "session", "screenshot": True})

        self.assertEqual(observed["url"], "https://example.test")
        self.assertNotIn("screenshot", observed)


if __name__ == "__main__":
    unittest.main()
