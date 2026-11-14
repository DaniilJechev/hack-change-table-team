"""Exercise actual release workflow shell steps without GitHub API writes."""

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
RELEASE = WORKFLOW["jobs"]["release"]


def step(name):
    return next(item["run"] for item in RELEASE["steps"] if item.get("name") == name)


class ReleaseWorkflowTest(unittest.TestCase):
    def test_desktop_rel_02_checksum_and_publication(self):
        for state, expected, success in [
            ("missing", ["view", "create", "edit"], True),
            ("draft", ["view", "upload", "edit"], True),
            ("published", ["view"], False),
            ("upload-failed", ["view", "upload"], False),
        ]:
            with self.subTest(state=state), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                assets = root / "desktop-release"
                assets.mkdir()
                installer = assets / "AvitoCode Setup 0.1.1.exe"
                installer.write_bytes(b"synthetic installer with spaces in name")
                subprocess.run(
                    ["bash", "-e", "-c", step("Prepare checksum")],
                    cwd=assets,
                    check=True,
                )
                checksum = (assets / "SHA256SUMS").read_text()
                self.assertIn(
                    hashlib.sha256(installer.read_bytes()).hexdigest(), checksum
                )
                self.assertIn(installer.name, checksum)
                fake = root / "gh"
                fake.write_text(
                    "#!/usr/bin/env python3\n"
                    "import json, os, sys\n"
                    "with open(os.environ['CALL_LOG'], 'a') as f: f.write(json.dumps(sys.argv[1:])+'\\n')\n"
                    "if sys.argv[2] == 'view':\n"
                    " if os.environ['RELEASE_STATE'] == 'missing': sys.exit(1)\n"
                    " print('false' if os.environ['RELEASE_STATE'] == 'published' else 'true')\n"
                    "if sys.argv[2] == 'upload' and os.environ['RELEASE_STATE'] == 'upload-failed':\n"
                    " sys.exit(2)\n"
                )
                fake.chmod(0o755)
                log = root / "calls.jsonl"
                environment = {
                    **os.environ,
                    "PATH": str(root) + os.pathsep + os.environ["PATH"],
                    "GH_TOKEN": "synthetic-not-a-token",
                    "GH_REPO": "synthetic/repository",
                    "RELEASE_TAG": "v0.1.1",
                    "RELEASE_STATE": state,
                    "CALL_LOG": str(log),
                }
                result = subprocess.run(
                    ["bash", "-e", "-c", step("Publish GitHub Release")],
                    cwd=root,
                    env=environment,
                    capture_output=True,
                    text=True,
                )
                calls = [json.loads(line) for line in log.read_text().splitlines()]
                self.assertEqual([call[1] for call in calls], expected)
                self.assertEqual(result.returncode == 0, success)
                if state != "published":
                    self.assertIn("desktop-release/" + installer.name, calls[1])
                    self.assertIn("desktop-release/SHA256SUMS", calls[1])
                    if success:
                        self.assertIn("--draft=false", calls[-1])
                    if state == "missing":
                        self.assertIn("--verify-tag", calls[1])
                        self.assertIn("--draft", calls[1])

    def test_desktop_rel_02_only_main_commits_are_eligible(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def git(*args):
                subprocess.run(
                    ["git", *args], cwd=root, check=True, capture_output=True
                )

            git("init", "-b", "main")
            git("config", "user.name", "Synthetic Test")
            git("config", "user.email", "synthetic@example.invalid")
            git("commit", "--allow-empty", "-m", "main")
            git("clone", "--bare", ".", "origin.git")
            git("remote", "add", "origin", str(root / "origin.git"))
            for eligible in [True, False]:
                if not eligible:
                    git("switch", "-c", "unmerged")
                    git("commit", "--allow-empty", "-m", "unmerged")
                result = subprocess.run(
                    ["bash", "-e", "-c", step("Require a commit from main")],
                    cwd=root,
                    capture_output=True,
                )
                self.assertEqual(result.returncode == 0, eligible)


if __name__ == "__main__":
    unittest.main()
