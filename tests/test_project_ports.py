"""Multi-port app discovery without changing host firewall/proxy."""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("ADMIN_PASSWORD", "test-password-at-least-12")
os.environ.setdefault("SESSION_SECRET", "test-session-secret-long-enough-for-tests-123456")
import app


class FakeProc:
    def __init__(self, pid, descendants=()):
        self.pid = pid
        self.descendants = descendants

    def children(self, recursive=False):
        return [FakeProc(pid) for pid in self.descendants]


def listening(port, ip, pid, state=None):
    return SimpleNamespace(status=app.psutil.CONN_LISTEN if state is None else state,
                           laddr=SimpleNamespace(port=port, ip=ip), pid=pid)


class MultiPortTests(unittest.TestCase):
    def test_child_process_api_and_frontend_are_detected(self):
        processes = [
            {"name": "aicorta-api", "pid": 111},
            {"name": "aicorta-web", "pid": 222},
        ]
        sockets = [listening(3001, "127.0.0.1", 113),
                   listening(3000, "0.0.0.0", 223),
                   listening(5000, "0.0.0.0", 999)]
        def process(pid):
            return FakeProc(pid, {111: (113,), 222: (223,)}.get(pid, ()))
        with patch.object(app.psutil, "Process", side_effect=process):
            result = app.project_listeners(processes, [3000, 3001, 8080], sockets)
        by_port = {row["port"]: row for row in result}
        self.assertEqual(set(by_port), {3000, 3001, 8080})
        self.assertEqual(by_port[3001]["scope"], "local")
        self.assertEqual(by_port[3001]["process"], "aicorta-api")
        self.assertEqual(by_port[3000]["scope"], "network")
        self.assertEqual(by_port[3000]["process"], "aicorta-web")
        self.assertFalse(by_port[8080]["listening"])

    def test_other_project_process_is_not_claimed(self):
        with patch.object(app.psutil, "Process", return_value=FakeProc(11)):
            result = app.project_listeners([{"name": "project", "pid": 11}],
                                           [3000], [listening(3000, "0.0.0.0", 20)])
        self.assertEqual(len(result), 1)
        self.assertFalse(result[0]["listening"])

    def test_invalid_or_duplicate_additional_ports_are_rejected(self):
        from fastapi import HTTPException
        with patch.object(app, "assert_auth"), patch.object(app, "get_project"), \
             patch.object(app, "records") as records:
            payload = app.AppConfig(port=3000, additional_ports=[3001, 3001])
            with self.assertRaises(HTTPException) as caught:
                app.configure_project("AiCorta", payload, None)
            self.assertEqual(caught.exception.status_code, 400)
            records.assert_not_called()


if __name__ == "__main__":
    unittest.main()
