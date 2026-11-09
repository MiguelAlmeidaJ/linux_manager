import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import github_ssh


class GitHubSSHTests(unittest.TestCase):
    def test_generate_key_keeps_private_key_on_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"HOME": tmp}):
                key = github_ssh.generate_key()
                self.assertTrue(key.startswith("ssh-ed25519 "))
                self.assertEqual(github_ssh.status()["public_key"], key)
                private_file = Path(tmp) / ".ssh" / "linux_manager_github_ed25519"
                self.assertTrue(private_file.exists())
                self.assertEqual(private_file.stat().st_mode & 0o777, 0o600)
                self.assertEqual((Path(tmp) / ".ssh").stat().st_mode & 0o777, 0o700)
                env = github_ssh.git_environment({})
                self.assertIn("StrictHostKeyChecking=yes", env["GIT_SSH_COMMAND"])
                self.assertIn("linux_manager_known_hosts", env["GIT_SSH_COMMAND"])
                self.assertNotIn("PRIVATE KEY", str(github_ssh.status()))

    def test_test_repo_requires_configured_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"HOME": tmp}):
                with self.assertRaises(Exception):
                    github_ssh.test_repository("git@github.com:company/private.git")


if __name__ == "__main__":
    unittest.main()
