"""Regression: PM2 JSON can exceed command()'s 12 KB log limit."""
import json
import os
import unittest
from unittest.mock import patch, Mock

os.environ.setdefault("ADMIN_PASSWORD", "test-password-do-not-use")
os.environ.setdefault("SESSION_SECRET", "a-long-test-only-session-secret-123456789012")
import app


class PM2SnapshotTests(unittest.TestCase):
    def test_full_json_and_no_environment_secrets(self):
        process = {
            "name": "aicorta-api",
            "pid": 106492,
            "pm2_env": {
                "pm_cwd": "/srv/linux-manager/projects/AiCorta/apps/api",
                "status": "online",
                "restart_time": 2,
                "DATABASE_URL": "postgresql://secret-user:secret-password@localhost/db",
            },
            "monit": {"memory": 30210000, "cpu": 0},
            "large": "A" * 30000,
        }
        with patch.object(app.subprocess, "run", return_value=Mock(
            returncode=0, stdout=json.dumps([process]), stderr=""
        )):
            items = app.pm2_process_snapshot()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["pm2_env"]["status"], "online")
        self.assertEqual(items[0]["pid"], 106492)
        self.assertNotIn("DATABASE_URL", str(items))
        self.assertNotIn("secret-password", str(items))

    def test_invalid_response_results_in_empty_list(self):
        with patch.object(app.subprocess, "run", return_value=Mock(
            returncode=0, stdout='[broken', stderr=""
        )):
            self.assertEqual(app.pm2_process_snapshot(), [])


if __name__ == "__main__":
    unittest.main()
