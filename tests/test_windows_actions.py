"""Test Windows agent command allowlist without executing Windows commands."""
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "agents" / "windows_actions.py"
spec = importlib.util.spec_from_file_location("windows_actions_test", SCRIPT)
actions = importlib.util.module_from_spec(spec)
spec.loader.exec_module(actions)
IDENTIFIER = "a" * 32


class ActionExecutionTests(unittest.TestCase):
    def test_reject_shell_metacharacters_before_process_calls(self):
        with patch.object(actions, "_run") as runner:
            result = actions.perform({"id": IDENTIFIER, "kind": "stop",
                                      "target": "api & powershell"})
            self.assertFalse(result["ok"])
            runner.assert_not_called()

    def test_pm2_rejects_unmanaged_process(self):
        with patch.object(actions.os.path, "isfile", return_value=True), \
             patch.object(actions, "_run", return_value=(True, '[{"name":"owned-app"}]')) as runner:
            result = actions.perform({"id": IDENTIFIER, "kind": "stop",
                                      "target": "other-process"}, pm2_command=r"C:\Tools\pm2.cmd")
            self.assertFalse(result["ok"])
            self.assertEqual(runner.call_count, 1)

    def test_pm2_reload_command_is_literal_args(self):
        with patch.object(actions.os.path, "isfile", return_value=True), \
             patch.object(actions, "_run",
                          side_effect=[(True, '[{"name":"api-prod"}]'), (True, "ok")]) as runner:
            result = actions.perform({"id": IDENTIFIER, "kind": "reload",
                                      "target": "api-prod"}, pm2_command=r"C:\Tools\pm2.cmd")
            self.assertTrue(result["ok"])
            self.assertEqual(runner.call_args.args[0],
                             [r"C:\Tools\pm2.cmd", "reload", "api-prod"])

    def test_arbitrary_windows_service_cannot_restart(self):
        with patch.object(actions, "_run") as runner:
            result = actions.perform({"id": IDENTIFIER, "kind": "service_restart",
                                      "target": "ServerManagerAgent"})
            self.assertFalse(result["ok"])
            runner.assert_not_called()

    def test_xampp_log_readonly(self):
        with tempfile.TemporaryDirectory() as root:
            log = Path(root) / "apache" / "logs" / "error.log"
            log.parent.mkdir(parents=True)
            log.write_text("Apache started\n", encoding="utf-8")
            with patch.dict(os.environ, {"XAMPP_HOME": root}), \
                 patch.object(actions, "_run") as runner:
                result = actions.perform({"id": IDENTIFIER, "kind": "service_logs",
                                          "target": "apache"})
            self.assertTrue(result["ok"])
            self.assertIn("Apache started", result["output"])
            runner.assert_not_called()


if __name__ == "__main__":
    unittest.main()
