import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import container_manager as cm
import database_manager as dbm
from fastapi import HTTPException


class ContainerManagerTests(unittest.TestCase):
    def test_clean_logs_strips_ansi_without_destroying_text(self):
        text = "\x1b[32m[PM2]\x1b[39m [online] \ufffd[31merror"
        self.assertEqual(cm.clean_output(text), "[PM2] [online] error")

    def test_docker_inventory_distinguishes_managed_and_external(self):
        lines = [
            {"ID": "a"*64, "Names": "linux-manager-databases-postgres-1",
             "Image": "postgres:17-alpine", "Status": "Up 2 hours (healthy)",
             "State": "running", "Ports": "127.0.0.1:5432->5432/tcp",
             "Labels": "com.docker.compose.project=linux-manager-databases,com.docker.compose.service=postgres"},
            {"ID": "b"*64, "Names": "unrelated", "Image": "some-app",
             "Status": "Up", "State": "running", "Ports": "",
             "Labels": "com.docker.compose.project=other"},
        ]
        with patch.object(cm.subprocess, "run", return_value=Mock(
                returncode=0, stdout="\n".join(json.dumps(x) for x in lines))):
            result = cm.docker_list()
        self.assertTrue(result[0]["managed"])
        self.assertFalse(result[1]["managed"])
        self.assertEqual(len(result[0]["id"]), 12)

    def test_stop_requires_confirmation(self):
        with self.assertRaises(HTTPException) as context:
            cm.manage("redis", "stop", "not-redis")
        self.assertEqual(context.exception.status_code, 400)

    def test_database_stop_refuses_if_catalog_has_data(self):
        with patch.object(dbm, "list_db", return_value=[{"engine": "postgres", "name": "live"}]), \
             patch.object(dbm, "compose") as compose:
            with self.assertRaises(HTTPException) as context:
                cm.manage("postgres", "stop", "postgres")
        self.assertEqual(context.exception.status_code, 409)
        compose.assert_not_called()

    def test_only_fixed_services_are_executable(self):
        with self.assertRaises(HTTPException):
            cm.manage("arbitrary-image;rm", "start")


if __name__ == "__main__":
    unittest.main()
