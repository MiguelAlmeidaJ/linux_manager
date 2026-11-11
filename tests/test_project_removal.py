import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("ADMIN_PASSWORD", "test-only-admin-password")
os.environ.setdefault("SESSION_SECRET", "test-only-long-secret-for-linux-manager-123456789")
import app


class ProjectRemovalTests(unittest.TestCase):
    def test_confirmation_required(self):
        with patch.object(app, "assert_auth"), patch.object(app, "require_secure_database_admin"):
            with self.assertRaises(app.HTTPException) as caught:
                app.remove_project("AiCorta", app.RemoveProject(confirmation="wrong"), None)
        self.assertEqual(caught.exception.status_code, 400)

    def test_archive_only_owned_pm2_and_preserve_unrelated_data(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project_root = base / "projects"
            project = project_root / "AiCorta"
            project.mkdir(parents=True)
            (project / "ecosystem.config.cjs").write_text("module.exports = {}")
            envdir = base / "env"
            envdir.mkdir()
            (envdir / "AiCorta.json").write_text('{"API_PORT": "3001"}')
            dbmarker = base / "managed-databases.json"
            dbmarker.write_text("precious data")
            datafile = base / "linux-manager-projects.json"
            datafile.write_text(json.dumps({"AiCorta": {"runtime": "node", "process_mode": "ecosystem"}}))
            pm2_result = Mock(returncode=0, stdout=json.dumps([
                {"name": "aicorta-api", "pm2_env": {"pm_cwd": str(project / "apps/api")}},
                {"name": "aicorta-web", "pm2_env": {"pm_cwd": str(project / "apps/web")}},
                {"name": "other-service", "pm2_env": {"pm_cwd": str(base / "other")}},
            ]))
            with patch.object(app, "ROOT", project_root), patch.object(app, "DATA_FILE", datafile), \
                 patch.object(app, "assert_auth"), patch.object(app, "require_secure_database_admin"), \
                 patch.object(app.subprocess, "run", return_value=pm2_result), \
                 patch.object(app, "command", return_value="OK") as command:
                result = app.remove_project("AiCorta", app.RemoveProject(confirmation="AiCorta"), None)
            self.assertTrue(result["removed"])
            self.assertFalse(project.exists())
            archive = Path(result["archived_at"])
            self.assertTrue((archive / "project" / "ecosystem.config.cjs").exists())
            self.assertTrue((archive / "variables.json").exists())
            self.assertEqual(dbmarker.read_text(), "precious data")
            self.assertEqual(json.loads(datafile.read_text()), {})
            commands = [args[0] for args, _ in command.call_args_list]
            self.assertIn(["pm2", "delete", "aicorta-api"], commands)
            self.assertIn(["pm2", "delete", "aicorta-web"], commands)
            self.assertNotIn(["pm2", "delete", "other-service"], commands)

    def test_docker_project_refused_before_stopping_anything(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "projects"
            project = root / "DockerApp"
            project.mkdir(parents=True)
            catalog = base / "linux-manager-projects.json"
            catalog.write_text(json.dumps({"DockerApp": {"runtime": "docker"}}))
            with patch.object(app, "ROOT", root), patch.object(app, "DATA_FILE", catalog), \
                 patch.object(app, "assert_auth"), patch.object(app, "require_secure_database_admin"), \
                 patch.object(app, "command") as command:
                with self.assertRaises(app.HTTPException) as caught:
                    app.remove_project("DockerApp", app.RemoveProject(confirmation="DockerApp"), None)
            self.assertEqual(caught.exception.status_code, 409)
            self.assertTrue(project.exists())
            command.assert_not_called()


if __name__ == "__main__":
    unittest.main()
