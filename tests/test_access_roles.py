import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

os.environ.setdefault("ADMIN_PASSWORD", "test-long-initial-password")
os.environ.setdefault("SESSION_SECRET", "test-session-secret-long-enough-to-pass-12345")
import app
import identity_store as ids


class RoleAndPM2Tests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.old_db = ids.DB_PATH
        ids.DB_PATH = Path(self.folder.name) / "auth" / "manager-auth.sqlite3"
        ids.init("bootstrap-admin-password-123")

    def tearDown(self):
        ids.DB_PATH = self.old_db
        self.folder.cleanup()

    def test_non_admin_cannot_access_admin_pages(self):
        user = ids.create_user(ids.NewUser(
            username="staff", email="staff@example.com",
            password="strong-staff-password-1234", role="user"))
        from starlette.requests import Request
        token = app.serializer.dumps({"id": user["id"], "version": user["session_version"]})
        scope = {"type": "http", "method": "GET", "path": "/api/users",
                 "headers": [(b"cookie", ("lm_session=" + token).encode())]}
        request = Request(scope)
        with self.assertRaises(app.HTTPException) as err:
            app.list_panel_users(request)
        self.assertEqual(err.exception.status_code, 403)

    def test_revoked_session_does_not_authenticate(self):
        user = ids.create_user(ids.NewUser(
            username="staff", email="staff@example.com",
            password="strong-staff-password-1234", role="user"))
        token = app.serializer.dumps({"id": user["id"], "version": user["session_version"]})
        self.assertTrue(app.authorized(token))
        ids.edit_user(user["id"], ids.EditUser(
            username="staff", email="staff@example.com",
            role="user", active=False), actor_id=1)
        self.assertFalse(app.authorized(token))

    def test_pm2_action_cannot_target_external_process(self):
        from starlette.requests import Request
        admin = ids.get_user(1)
        token = app.serializer.dumps({"id": 1, "version": admin["session_version"]})
        scope = {"type": "http", "method": "POST",
                 "path": "/api/pm2/processes/foreign/restart",
                 "headers": [(b"cookie", ("lm_session=" + token).encode())]}
        request = Request(scope)
        with patch.object(app, "pm2_process_snapshot", return_value=[
            {"name": "foreign", "pm2_env": {"pm_cwd": "/other/app"}}
        ]), patch.object(app, "records", return_value={"local": {}}), \
             patch.object(app, "ROOT", Path("/srv/linux-manager/projects")), \
             patch.object(app, "command") as command:
            with self.assertRaises(app.HTTPException) as err:
                app.manage_pm2_process("foreign", "restart", request)
            self.assertEqual(err.exception.status_code, 403)
            command.assert_not_called()


if __name__ == "__main__":
    unittest.main()
