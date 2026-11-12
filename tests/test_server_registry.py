import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
import server_registry as nodes


class ServerRegistryTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.old = nodes.NODES_DB
        nodes.NODES_DB = Path(self.folder.name) / "auth" / "nodes.sqlite3"

    def tearDown(self):
        nodes.NODES_DB = self.old
        self.folder.cleanup()

    def test_create_heartbeat_and_token_not_exposed(self):
        saved = nodes.create(nodes.NodeCreate(name="win-app-01", os_type="windows"))
        self.assertGreaterEqual(len(saved["token"]), 48)
        overview = nodes.listing()[0]
        self.assertNotIn("token", overview)
        self.assertFalse(overview["online"])
        body = nodes.Heartbeat(hostname="win-app-01", os_version="Windows Server",
            pm2=[{"name": "api", "status": "online", "password": "NOPE"}],
            services=[{"name": "Apache2.4", "status": "Running"}])
        with self.assertRaises(HTTPException) as wrong:
            nodes.heartbeat(saved["id"], "Bearer invalid", body)
        self.assertEqual(wrong.exception.status_code, 401)
        self.assertTrue(nodes.heartbeat(saved["id"], "Bearer " + saved["token"], body)["ok"])
        detail = nodes.detail(saved["id"])
        self.assertTrue(detail["online"])
        self.assertEqual(detail["pm2"][0]["name"], "api")
        self.assertNotIn("NOPE", str(detail))
        self.assertEqual(nodes.NODES_DB.parent.stat().st_mode & 0o777, 0o700)

    def test_disable_revokes_token(self):
        saved = nodes.create(nodes.NodeCreate(name="win-app-02"))
        nodes.disable(saved["id"])
        with self.assertRaises(HTTPException):
            nodes.heartbeat(saved["id"], "Bearer " + saved["token"],
                            nodes.Heartbeat(hostname="win-app-02"))
        self.assertFalse(nodes.detail(saved["id"])["enabled"])

    def test_network_ips_are_validated_and_stored_without_loopback(self):
        saved = nodes.create(nodes.NodeCreate(name="win-ips-01"))
        body = nodes.Heartbeat(hostname="win-ips-01",
            addresses=["192.168.199.25", "10.0.0.5", "127.0.0.1",
                       "not-an-ip", "192.168.199.25", "::1"])
        nodes.heartbeat(saved["id"], "Bearer " + saved["token"], body)
        ips = nodes.detail(saved["id"])["addresses"]
        self.assertEqual(ips, ["192.168.199.25", "10.0.0.5"])

    def test_registered_token_is_required_to_generate_installer(self):
        saved = nodes.create(nodes.NodeCreate(name="win-agent-04"))
        self.assertEqual(nodes.verify_token(saved["id"], saved["token"]), "windows")
        with self.assertRaises(HTTPException):
            nodes.verify_token(saved["id"], "not-a-valid-token")
        nodes.disable(saved["id"])
        with self.assertRaises(HTTPException):
            nodes.verify_token(saved["id"], saved["token"])

    def test_duplicate_rejected(self):
        nodes.create(nodes.NodeCreate(name="win-app-03"))
        with self.assertRaises(HTTPException):
            nodes.create(nodes.NodeCreate(name="win-app-03"))


if __name__ == "__main__":
    unittest.main()
