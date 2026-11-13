"""Windows agent update bundle must not expose stored enrollment credentials."""
import io
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("ADMIN_PASSWORD", "test-safe-initial-password-1234")
os.environ.setdefault("SESSION_SECRET", "long-session-secret-for-unit-testing-1234567")
import app


class WindowsUpdateBundleTests(unittest.TestCase):
    def test_update_bundle_never_embeds_agent_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agents = root / "agents"
            agents.mkdir()
            for filename in ("Update-Agent.ps1", "Configure-MySQL.ps1",
                             "Configure-Existing-MySQL.ps1", "windows_agent.py", "windows_metrics.py", "windows_actions.py", "windows_service.py"):
                (agents / filename).write_text("# safe update file\n")
            request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))
            with patch.object(app, "__file__", str(root / "app.py")), \
                 patch.object(app, "assert_admin", return_value={"id": 1, "username": "admin"}), \
                 patch.object(app, "require_secure_database_admin"), \
                 patch.object(app.nodes, "detail", return_value={"os_type": "windows", "enabled": True}), \
                 patch.object(app.identities, "log"):
                result = app.server_agent_update_download(3, request)
            with zipfile.ZipFile(io.BytesIO(result.body)) as archive:
                names = set(archive.namelist())
                self.assertIn("Update-Agent.ps1", names)
                self.assertIn("Configure-MySQL.ps1", names)
                self.assertIn("Configure-Existing-MySQL.ps1", names)
                self.assertIn("windows_actions.py", names)
                self.assertIn("windows_metrics.py", names)
                self.assertNotIn("enrollment.json", names)
                self.assertNotIn("agent.json", names)
            self.assertIn("no-store", result.headers.get("cache-control", ""))

    def test_bundle_matches_updater_required_python_files(self):
        from re import findall
        source = (Path(__file__).resolve().parents[1] / "agents" / "Update-Agent.ps1").read_text(encoding="utf-8")
        required = set(findall(r'"(windows_[a-z_]+\.py)"', source))
        self.assertTrue(required, "No Python dependencies found in updater")
        with patch.object(app, "assert_admin", return_value={"id": 1, "username": "admin"}), \
             patch.object(app, "require_secure_database_admin"), \
             patch.object(app.nodes, "detail", return_value={"os_type": "windows", "enabled": True}), \
             patch.object(app.identities, "log"):
            result = app.server_agent_update_download(3, SimpleNamespace(
                client=SimpleNamespace(host="127.0.0.1")))
        with zipfile.ZipFile(io.BytesIO(result.body)) as archive:
            included = set(archive.namelist())
        self.assertFalse(required - included, "ZIP missing updater-required files: " + str(required - included))

    def test_linux_node_cannot_download_windows_update(self):
        request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))
        with patch.object(app, "assert_admin", return_value={"id": 1, "username": "admin"}), \
             patch.object(app, "require_secure_database_admin"), \
             patch.object(app.nodes, "detail", return_value={"os_type": "linux", "enabled": True}):
            with self.assertRaises(app.HTTPException) as error:
                app.server_agent_update_download(99, request)
        self.assertEqual(error.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
