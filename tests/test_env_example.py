import tempfile
import unittest
from pathlib import Path

import deploy_jobs as dj


class EnvExampleTests(unittest.TestCase):
    def test_only_variable_names_are_returned_and_existing_are_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            (project / ".env.example").write_text(
                '# example\nDATABASE_URL="postgres://sample:password@localhost"\n'
                'JWT_SECRET=change-me\n# SMTP_HOST=optional.example\n'
                'export NEXT_PUBLIC_API_URL=http://localhost:3001/api\n'
                'DATABASE_URL=duplicate\nINVALID-KEY=x\nADMIN_PASSWORD=never\n',
                encoding="utf-8")
            result = dj.example_env_keys(project, {"DATABASE_URL": "secret-real-database-url"})
            self.assertEqual(result["source"], ".env.example")
            self.assertEqual(result["missing"], 2)
            self.assertEqual(result["variables"], [
                {"key": "DATABASE_URL", "configured": True},
                {"key": "JWT_SECRET", "configured": False},
                {"key": "NEXT_PUBLIC_API_URL", "configured": False},
            ])
            self.assertNotIn("sample:password", str(result))
            self.assertNotIn("secret-real-database-url", str(result))

    def test_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            secret = project / "secret.env"
            secret.write_text("PRIVATE_KEY=value\n")
            (project / ".env.example").symlink_to(secret)
            self.assertIsNone(dj.example_env_keys(project, {})["source"])

    def test_missing_example(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(dj.example_env_keys(Path(temp), {}),
                             {"source": None, "variables": [], "missing": 0})


if __name__ == "__main__":
    unittest.main()
