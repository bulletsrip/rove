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

from jev_ultrafast import browser


class BrowserTargetVisibilityTest(unittest.TestCase):
    def test_google_urls_request_english_interface(self):
        self.assertEqual(browser.english_google_url("https://google.com"), "https://google.com?hl=en")
        self.assertEqual(
            browser.english_google_url("https://maps.google.com/?api=1"),
            "https://maps.google.com/?api=1&hl=en",
        )
        self.assertEqual(browser.english_google_url("https://example.com"), "https://example.com")

    def test_agent_target_is_foreground(self):
        calls = []

        def fake_cdp(method, **params):
            calls.append((method, params))
            if method == "Target.getTargets":
                return {"targetInfos": []}
            if method == "Target.createTarget":
                return {"targetId": "task-target"}
            if method == "Target.attachToTarget":
                return {"sessionId": "task-session"}
            if method == "Runtime.evaluate":
                return {"result": {"value": "complete"}}
            return {}

        with patch.object(browser, "cdp", side_effect=fake_cdp):
            task_browser = browser.Browser("https://example.test")
            task_browser.close()

        create = next(params for method, params in calls if method == "Target.createTarget")
        self.assertFalse(create.get("background", False))
        self.assertTrue(any(method == "Target.activateTarget" for method, _ in calls))
        metrics = next(params for method, params in calls if method == "Emulation.setDeviceMetricsOverride")
        self.assertEqual(metrics["width"], 1440)
        self.assertEqual(metrics["height"], 900)


if __name__ == "__main__":
    unittest.main()
