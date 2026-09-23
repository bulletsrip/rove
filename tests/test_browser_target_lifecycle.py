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


class BrowserTargetLifecycleTest(unittest.TestCase):
    def test_reuses_visible_tab_and_keeps_page_after_close(self):
        calls = []

        def fake_cdp(method, **params):
            calls.append((method, params))
            if method == "Target.getTargets":
                return {"targetInfos": [{"targetId": "visible-target", "type": "page", "url": "about:blank"}]}
            if method == "Target.attachToTarget":
                return {"sessionId": "task-session"}
            if method == "Runtime.evaluate":
                return {"result": {"value": "complete"}}
            return {}

        with patch.object(browser, "cdp", side_effect=fake_cdp):
            task_browser = browser.Browser("https://example.test")
            task_browser.close()

        methods = [method for method, _ in calls]
        self.assertNotIn("Target.createTarget", methods)
        self.assertNotIn("Target.closeTarget", methods)
        self.assertIn("Target.detachFromTarget", methods)


if __name__ == "__main__":
    unittest.main()
