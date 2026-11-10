import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import database_manager as dm
from fastapi import HTTPException


class DatabaseManagerTests(unittest.TestCase):
    def test_identifiers_reject_sql_injection(self):
        for value in ("root", "postgres", "bad-name", "x';DROP", "UPPER", "x", "a" * 49):
            with self.subTest(value=value), self.assertRaises(HTTPException):
                dm.valid(value)
        self.assertEqual(dm.valid("aicorta_db"), "aicorta_db")

    def test_credentials_files_are_private_and_persistent(self):
        with tempfile.TemporaryDirectory() as temp:
            original = dm.SECRETS
            try:
                dm.SECRETS = Path(temp) / "database-services.env"
                first = dm.root_secrets()
                second = dm.root_secrets()
                self.assertEqual(first, second)
                self.assertEqual(dm.SECRETS.stat().st_mode & 0o777, 0o600)
                self.assertGreaterEqual(len(first["POSTGRES_ROOT_PASSWORD"]), 24)
            finally:
                dm.SECRETS = original

    def test_bind_refuses_docker_before_database_provision(self):
        payload = dm.NewDatabase(engine="postgres", name="my_db", username="owner_user",
                                 password="StrongPasswordExample123", project="demo")
        with self.assertRaises(HTTPException):
            dm.create(payload, {"demo": {"runtime": "docker"}})

    def test_list_never_returns_passwords(self):
        with patch.object(dm, "catalog", return_value={
            "databases": [{"engine": "postgres", "name": "my_db",
                           "owner": "owner_user", "users": [
                               {"username": "owner_user", "permission": "owner",
                                "password": "do-not-leak"}]}]
        }):
            result = dm.list_db()
            self.assertNotIn("do-not-leak", str(result))
            self.assertEqual(result[0]["users"][0]["permission"], "owner")


if __name__ == "__main__":
    unittest.main()
