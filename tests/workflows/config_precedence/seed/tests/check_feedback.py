import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PROGRAM = ROOT / "config_cli.py"


class VisibleFeedbackTests(unittest.TestCase):
    def test_environment_overrides_json_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.json"
            config.write_text('{"timeout": 40}', encoding="utf-8")
            environment = os.environ.copy()
            environment["ASKME_TIMEOUT"] = "50"
            completed = subprocess.run(
                [sys.executable, str(PROGRAM), "--config", str(config)],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(completed.returncode, 0)
        self.assertEqual(json.loads(completed.stdout)["timeout"], 50)


if __name__ == "__main__":
    unittest.main()
