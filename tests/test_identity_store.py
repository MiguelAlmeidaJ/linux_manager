import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
import identity_store as ids


class IdentityStoreTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.old = ids.DB_PATH
        ids.DB_PATH = Path(self.folder.name) / "auth" / "manager-auth.sqlite3"
        ids.init("initial-password-of-admin-12345")

    def tearDown(self):
        ids.DB_PATH = self.old
        self.folder.cleanup()

    def test_bootstrap_creates_admin_and_password_not_plaintext(self):
        users = ids.list_users()
        self.assertEqual(len(users), 1)
        self.assertEqual(users[0]["username"], "admin")
        self.assertEqual(users[0]["role"], "admin")
        self.assertNotIn("password_hash", users[0])
        self.assertTrue(ids.authenticate("admin", "initial-password-of-admin-12345", "10.1.1.1"))
        with self.assertRaises(HTTPException):
            ids.authenticate("admin", "wrong-password", "10.1.1.2")
        self.assertEqual(ids.DB_PATH.stat().st_mode & 0o777, 0o600)
        self.assertEqual(ids.DB_PATH.parent.stat().st_mode & 0o777, 0o700)

    def test_user_create_edit_disable_and_revoke_sessions(self):
        created = ids.create_user(ids.NewUser(
            username="operator", email="operator@example.com", role="user",
            password="another-strong-password-123"))
        self.assertTrue(created["active"])
        self.assertEqual(created["role"], "user")
        saved = ids.get_user(created["id"])
        old_version = saved["session_version"]
        updated = ids.edit_user(created["id"], ids.EditUser(
            username="operator", email="operator@example.com",
            role="user", active=False), actor_id=1)
        self.assertFalse(updated["active"])
        self.assertGreater(updated["session_version"], old_version)

    def test_cannot_disable_last_active_admin(self):
        with self.assertRaises(HTTPException) as context:
            ids.edit_user(1, ids.EditUser(
                username="admin", email="admin@localhost.invalid",
                role="admin", active=False), actor_id=2)
        self.assertEqual(context.exception.status_code, 409)

    def test_duplicate_username_and_email_rejected(self):
        first=ids.NewUser(username="person",email="person@example.com",password="p" * 16)
        ids.create_user(first)
        with self.assertRaises(HTTPException):
            ids.create_user(first)

    def test_audit_no_secrets_and_pagination(self):
        ids.log(1, "admin", "api.post", "/api/projects/demo/deploy", 200, "127.0.0.1")
        data = ids.list_audit(1, 0)
        self.assertEqual(len(data["items"]), 1)
        self.assertNotIn("password_hash", str(data))
        self.assertTrue(data["total"] >= 2)


if __name__ == "__main__":
    unittest.main()
