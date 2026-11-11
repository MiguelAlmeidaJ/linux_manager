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

    def test_sync_creates_private_file_preserving_values(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "projects"
            project = root / "AiCorta"
            project.mkdir(parents=True)
            dj.save_env(root, "AiCorta", dj.EnvChange(values={
                "DATABASE_URL": "postgresql://some-user:pass@127.0.0.1:5432/db",
                "SYSTEM_NAME": 'Director "Ops"',
            }))
            target = project / ".env"
            self.assertTrue(target.is_file())
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            data = target.read_text()
            self.assertTrue(data.startswith(dj.ENV_HEADER + "\n"))
            self.assertIn("DATABASE_URL=", data)
            self.assertIn('SYSTEM_NAME="Director \\"Ops\\""', data)
            dj.save_env(root, "AiCorta", dj.EnvChange(values={"SYSTEM_USERNAME": "system"}))
            self.assertIn("SYSTEM_USERNAME=", target.read_text())
            self.assertEqual(len(dj.read_env(root, "AiCorta")), 3)

    def test_sync_does_not_overwrite_unmanaged_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "projects"
            project = root / "AiCorta"
            project.mkdir(parents=True)
            target = project / ".env"
            target.write_text("EXISTING=keep-me\n")
            from fastapi import HTTPException
            with self.assertRaises(HTTPException) as ctx:
                dj.save_env(root, "AiCorta", dj.EnvChange(values={"SYSTEM_NAME": "new"}))
            self.assertEqual(ctx.exception.status_code, 409)
            self.assertEqual(target.read_text(), "EXISTING=keep-me\n")

    def test_sync_refuses_symlink(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "projects"
            project = root / "AiCorta"
            project.mkdir(parents=True)
            sensitive = Path(temp) / "sensitive"
            sensitive.write_text("secret")
            (project / ".env").symlink_to(sensitive)
            from fastapi import HTTPException
            with self.assertRaises(HTTPException):
                dj.sync_project_dotenv(root, "AiCorta", {"SAFE": "yes"})
            self.assertEqual(sensitive.read_text(), "secret")

    def test_document_editor_round_trip_and_concurrent_revision(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "projects"
            (root / "AiCorta").mkdir(parents=True)
            dj.save_env(root, "AiCorta", dj.EnvChange(values={
                "DATABASE_URL": "postgresql://owner:p@127.0.0.1:5432/app?schema=public",
                "DISPLAY_NAME": 'O "Barbeiro" \\ Spa',
                "SYSTEM_NAME": "Sistema",
            }))
            original = dj.read_env_document(root, "AiCorta")
            self.assertNotIn("SYSTEM_NAME=Sistema", original["content"])
            self.assertEqual(dj.parse_env_document(original["content"]),
                             dj.read_env(root, "AiCorta"))
            revised = original["content"].replace('SYSTEM_NAME="Sistema"', 'SYSTEM_NAME="Admin"')
            response = dj.write_env_document(root, "AiCorta",
                dj.EnvDocument(content=revised, revision=original["revision"]))
            self.assertTrue(response["saved"])
            self.assertEqual(dj.read_env(root, "AiCorta")["SYSTEM_NAME"], "Admin")
            self.assertEqual((root / "AiCorta" / ".env").stat().st_mode & 0o777, 0o600)
            from fastapi import HTTPException
            with self.assertRaises(HTTPException) as conflict:
                dj.write_env_document(root, "AiCorta",
                    dj.EnvDocument(content=revised, revision=original["revision"]))
            self.assertEqual(conflict.exception.status_code, 409)

    def test_document_rejects_duplicates_reserved_and_multiline_values(self):
        from fastapi import HTTPException
        for content in (
            "API_PORT=3001\nAPI_PORT=3002\n",
            "SESSION_SECRET=x\n",
            "INVALID-KEY=yes\n",
            'PASSWORD="unterminated\n',
            'VALUE="first\\nsecond"\n',
            "EMPTY\n",
        ):
            with self.subTest(content=content), self.assertRaises(HTTPException):
                dj.parse_env_document(content)
        self.assertEqual(
            dj.parse_env_document("## comment\nEMPTY=\nFOO='literal # value'\nPORT_CUSTOM=3001"),
            {"EMPTY": "", "FOO": "literal # value", "PORT_CUSTOM": "3001"},
        )

    def test_missing_example(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(dj.example_env_keys(Path(temp), {}),
                             {"source": None, "variables": [], "missing": 0})


if __name__ == "__main__":
    unittest.main()
