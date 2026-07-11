import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PROGRAM = ROOT / "config_cli.py"


def run_cli(*arguments):
    environment = os.environ.copy()
    environment.pop("ASKME_TIMEOUT", None)
    return subprocess.run(
        [sys.executable, str(PROGRAM), *arguments],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


class PublicBehaviorTests(unittest.TestCase):
    def test_default_configuration(self):
        completed = run_cli()
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(json.loads(completed.stdout), {"name": "service", "timeout": 30})

    def test_json_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.json"
            config.write_text('{"timeout": 45}', encoding="utf-8")
            completed = run_cli("--config", str(config))
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(json.loads(completed.stdout)["timeout"], 45)

    def test_name_is_preserved(self):
        completed = run_cli("--name", "worker")
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(json.loads(completed.stdout), {"name": "worker", "timeout": 30})


if __name__ == "__main__":
    unittest.main()
