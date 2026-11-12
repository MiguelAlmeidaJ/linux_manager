"""Windows telemetry inventory tests (safe on non-Windows development hosts)."""
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "agents" / "windows_agent.py"
spec = importlib.util.spec_from_file_location("windows_agent_for_test", SCRIPT)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)


class WindowsInventoryTests(unittest.TestCase):
    def test_pm2_ports_assigned_only_to_related_process_tree(self):
        pm2 = [{"name": "frontend", "pid": "120", "status": "online"},
               {"name": "api", "pid": "220", "status": "online"}]
        output = json.dumps({"processes": [
            {"ProcessId": 121, "ParentProcessId": 120},
            {"ProcessId": 222, "ParentProcessId": 220},
            {"ProcessId": 999, "ParentProcessId": 1}],
            "tcp": [
                {"OwningProcess": 121, "LocalPort": 3000, "LocalAddress": "0.0.0.0"},
                {"OwningProcess": 222, "LocalPort": 3001, "LocalAddress": "127.0.0.1"},
                {"OwningProcess": 999, "LocalPort": 8080, "LocalAddress": "0.0.0.0"}]})
        with patch.object(agent, "run", return_value=output):
            report = agent.windows_pm2_ports(pm2)
        self.assertEqual(report[0]["ports"], [{"port": 3000, "address": "0.0.0.0"}])
        self.assertEqual(report[1]["ports"], [{"port": 3001, "address": "127.0.0.1"}])

    def test_mysql_does_not_attempt_login_without_readonly_config(self):
        with tempfile.TemporaryDirectory() as folder:
            xampp = Path(folder)
            cli = xampp / "mysql" / "bin" / "mysql.exe"
            cli.parent.mkdir(parents=True)
            cli.touch()
            with patch.object(agent, "CONFIG_PATH", xampp / "private" / "agent.json"), \
                 patch.dict(os.environ, {"XAMPP_HOME": str(xampp)}), \
                 patch.object(agent, "run") as run:
                output = agent.mysql_databases()
            self.assertEqual(output["status"], "configuration_required")
            run.assert_not_called()

    def test_mysql_filters_system_schemas_and_no_password_in_argv(self):
        with tempfile.TemporaryDirectory() as folder:
            xampp = Path(folder)
            cli = xampp / "mysql" / "bin" / "mysql.exe"
            cli.parent.mkdir(parents=True)
            cli.touch()
            cfg = xampp / "private" / "mysql-monitor.cnf"
            cfg.parent.mkdir()
            cfg.write_text("[client]\nuser=monitor\npassword=private\n")
            with patch.object(agent, "CONFIG_PATH", cfg.parent / "agent.json"), \
                 patch.dict(os.environ, {"XAMPP_HOME": str(xampp)}), \
                 patch.object(agent, "run", return_value="information_schema\nappdb\nmysql\nanother_db\n") as run:
                result = agent.mysql_databases()
            self.assertEqual(result["status"], "ok")
            self.assertEqual([x["name"] for x in result["databases"]], ["appdb", "another_db"])
            self.assertNotIn("private", " ".join(map(str, run.call_args.args[0])))


if __name__ == "__main__":
    unittest.main()
