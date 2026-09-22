from __future__ import annotations

import os
import sys
import types
import unittest
from unittest.mock import patch
from pathlib import Path


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


class FieldTextTest(unittest.TestCase):
    def test_accepts_fenced_json_text_response(self):
        response = {
            "choices": [
                {
                    "message": {
                        "content": '```json\n{"text":"Tanjung Duren to Tebet"}\n```',
                    }
                }
            ]
        }
        with patch.dict(os.environ, {"TEXT_MODEL_API_KEY": "test-key"}), patch.object(model, "post_json", return_value=response):
            value, _helper = model.field_text({"goal": "Search a route"})

        self.assertEqual(value, "Tanjung Duren to Tebet")


if __name__ == "__main__":
    unittest.main()
