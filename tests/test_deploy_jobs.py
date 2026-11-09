import tempfile
import unittest
from pathlib import Path

import deploy_jobs as jobs


class DeploySecurityTests(unittest.TestCase):
    def test_env_is_private_and_not_returned(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "projects"
            root.mkdir()
            keys = jobs.save_env(root, "demo", jobs.EnvChange(values={"API_TOKEN": "very-secret-value"}))
            self.assertEqual(keys, ["API_TOKEN"])
            self.assertEqual(jobs.env_keys(root, "demo"), ["API_TOKEN"])
            self.assertEqual(jobs.read_env(root, "demo")["API_TOKEN"], "very-secret-value")
            self.assertEqual(jobs.env_path(root, "demo").stat().st_mode & 0o777, 0o600)
            self.assertEqual(jobs.mask("token: very-secret-value", jobs.read_env(root, "demo")), "token: [REDACTED]")

    def test_reserved_keys_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "projects"
            root.mkdir()
            with self.assertRaises(Exception):
                jobs.save_env(root, "demo", jobs.EnvChange(values={"ADMIN_PASSWORD": "bad"}))

    def test_shell_not_accepted_as_command(self):
        with self.assertRaises(RuntimeError):
            jobs.safe_args("sh -c id")
        self.assertEqual(jobs.safe_args("npm run build"), ["npm", "run", "build"])


if __name__ == "__main__":
    unittest.main()
