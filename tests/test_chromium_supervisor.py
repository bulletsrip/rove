from __future__ import annotations

import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SUPERVISOR = ROOT / "docker" / "chromium-supervisor.sh"


class ChromiumSupervisorTest(unittest.TestCase):
    def test_restarts_chromium_after_it_exits(self):
        with tempfile.TemporaryDirectory() as directory:
            temp_dir = Path(directory)
            fake_chromium = temp_dir / "fake-chromium"
            launch_count = temp_dir / "launch-count"
            log_file = temp_dir / "chromium.log"
            fake_chromium.write_text(
                "#!/bin/sh\n"
                "count=0\n"
                "if [ -f \"$FAKE_LAUNCH_COUNT\" ]; then count=$(cat \"$FAKE_LAUNCH_COUNT\"); fi\n"
                "count=$((count + 1))\n"
                "echo \"$count\" > \"$FAKE_LAUNCH_COUNT\"\n"
                "if [ \"$count\" -eq 1 ]; then exit 42; fi\n"
                "trap 'exit 0' TERM INT\n"
                "while :; do sleep 1; done\n"
            )
            fake_chromium.chmod(0o755)

            environment = os.environ.copy()
            environment.update(
                {
                    "CHROME_BIN": str(fake_chromium),
                    "CHROME_LOG": str(log_file),
                    "CHROME_RESTART_DELAY": "0.01",
                    "FAKE_LAUNCH_COUNT": str(launch_count),
                }
            )
            process = subprocess.Popen(["sh", str(SUPERVISOR)], env=environment)
            try:
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    if launch_count.exists() and int(launch_count.read_text()) >= 2:
                        break
                    time.sleep(0.01)
                self.assertTrue(launch_count.exists())
                self.assertGreaterEqual(int(launch_count.read_text()), 2)
            finally:
                process.terminate()
                process.wait(timeout=2)


if __name__ == "__main__":
    unittest.main()
